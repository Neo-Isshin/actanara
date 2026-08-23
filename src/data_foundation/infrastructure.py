"""Infrastructure active graph helpers.

The graph is the safe Dashboard authority for devices, services, and their
recent changes. Diary Markdown may retain raw user-provided wording, but this
module redacts secret-like values before storing graph fields or API payloads.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .db import connect, migrate
from .paths import RuntimePaths

ENTITY_TYPES = {"device", "service"}
IN_PROGRESS_STATUSES = {"active", "available", "configured", "online", "ready", "running", "unknown"}
SECRET_FIELD_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|api[_-]?key|secret|credential|private[_-]?key|authorization|bearer|cookie)"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![?&])\b(password|passwd|pwd|token|api[_-]?key|secret|credential|private[_-]?key|authorization|bearer|cookie)"
    r"(\s*[:=]\s*)([^,\s;]+)"
)
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>()\"'`,;]+", re.IGNORECASE)
REDACTED = "[redacted]"


class InfrastructureIdentityError(RuntimeError):
    """An infrastructure update did not bind to the active entity catalog."""


class InfrastructureCatalogActionError(RuntimeError):
    """A catalog-maintenance proposal could not be applied safely."""


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return normalized or "unnamed"


def _entity_type(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ENTITY_TYPES:
        return normalized
    if normalized in {"hardware", "host", "machine", "server", "router", "pc", "vps", "instance"}:
        return "device"
    return "service"


def _sensitive_field(name: str | None) -> bool:
    return bool(SECRET_FIELD_RE.search(str(name or "")))


def _redact_url(value: str) -> str:
    try:
        split = urlsplit(value)
    except ValueError:
        return value
    if not split.scheme or not split.netloc:
        return value
    netloc = split.hostname or split.netloc.rsplit("@", 1)[-1]
    if split.port:
        netloc = f"{netloc}:{split.port}"
    query = urlencode(
        [(key, REDACTED if _sensitive_field(key) else val) for key, val in parse_qsl(split.query, keep_blank_values=True)]
    )
    return urlunsplit((split.scheme, netloc, split.path, query, ""))


def redact_sensitive_value(value: Any, *, field_name: str = "") -> str:
    """Return a Dashboard-safe string for infrastructure graph storage/display."""
    text = str(value or "").strip()
    if not text:
        return ""
    if _sensitive_field(field_name):
        return REDACTED
    if "://" in text:
        text = URL_RE.sub(lambda match: _redact_url(match.group(0)), text)
    text = SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    return text


def _canonical_key(entity_type: str, name: str, host: str = "") -> str:
    host_part = f":{_slug(host)}" if host else ""
    return f"{entity_type}:{_slug(name)}{host_part}"


def _resolve_entity_id(connection: Any, *, entity_type: str, name: str, host: str, requested_id: str = "") -> tuple[str, str]:
    canonical_key = _canonical_key(entity_type, name, host)
    if requested_id:
        row = connection.execute(
            """
            SELECT entity_id, canonical_key
            FROM infrastructure_entities
            WHERE entity_id = ? AND archived_at IS NULL
            """,
            (requested_id,),
        ).fetchone()
        if row is not None:
            return str(row["entity_id"]), str(row["canonical_key"])
    row = connection.execute(
        """
        SELECT entity_id, canonical_key
        FROM infrastructure_entities
        WHERE canonical_key = ? AND archived_at IS NULL
        """,
        (canonical_key,),
    ).fetchone()
    if row is not None:
        return str(row["entity_id"]), str(row["canonical_key"])
    aliases = connection.execute(
        """
        SELECT e.entity_id, e.canonical_key
        FROM infrastructure_entity_aliases a
        JOIN infrastructure_entities e ON e.entity_id = a.entity_id
        WHERE a.normalized_alias = ? AND e.archived_at IS NULL
        """,
        (_slug(name),),
    ).fetchall()
    host_suffix = f":{_slug(host)}" if host else ""
    for alias in aliases:
        alias_key = str(alias["canonical_key"])
        if host_suffix and not alias_key.endswith(host_suffix):
            continue
        if not alias_key.startswith(f"{entity_type}:"):
            continue
        return str(alias["entity_id"]), alias_key
    return _stable_id(f"infra-{entity_type}", canonical_key), canonical_key


def _event_type(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized:
        return normalized
    return "updated"


def _confidence(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"high", "medium", "low"} else "medium"


def _metadata(update: dict[str, Any], *, host: str) -> dict[str, Any]:
    metadata = update.get("metadata") if isinstance(update.get("metadata"), dict) else {}
    result = dict(metadata)
    if host:
        result["host"] = redact_sensitive_value(host, field_name="host")
    for key in ("role", "owner", "environment"):
        value = update.get(key)
        if value:
            result[key] = redact_sensitive_value(value, field_name=key)
    return result


def _redacted_raw_update(update: dict[str, Any], *, field: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    for key, value in update.items():
        if key == "evidence":
            continue
        field_name = field if key in {"currentValue", "value", "previousValue"} and field else key
        raw[key] = redact_sensitive_value(value, field_name=field_name)
    return raw


def _entity_patch(update: dict[str, Any], *, entity_type: str, host: str) -> dict[str, str]:
    kind = update.get("kind") or update.get("deviceKind") or update.get("serviceKind") or update.get("category")
    field = str(update.get("field") or "").strip().lower()
    field_value = update.get("currentValue") or update.get("value") or ""
    endpoint = update.get("endpoint") or update.get("url") or (field_value if field == "endpoint" else "")
    port = update.get("port") or (field_value if field == "port" else "")
    path = update.get("path") or update.get("runtimePath") or (field_value if field == "path" else "")
    status = update.get("status") or update.get("state") or (field_value if field == "status" else "")
    normalized_status = str(status or "").strip().lower()
    return {
        "kind": redact_sensitive_value(kind, field_name="kind"),
        "status": redact_sensitive_value(status, field_name="status") or "unknown",
        "location": redact_sensitive_value(update.get("location") or update.get("network") or "", field_name="location"),
        "endpoint": redact_sensitive_value(endpoint, field_name="endpoint"),
        "port": redact_sensitive_value(port, field_name="port"),
        "protocol": redact_sensitive_value(update.get("protocol") or "", field_name="protocol"),
        "path": redact_sensitive_value(path, field_name="path"),
        "subtype": "unknown",
        "lifecycle_status": normalized_status if normalized_status in {"planned", "provisioning", "active", "suspended", "retired"} else "unknown",
        "health_status": normalized_status if normalized_status in {"healthy", "degraded", "unhealthy", "offline"} else "unknown",
        "environment": "unknown",
        "exposure_scope": "unknown",
        "metadata_json": _json(_metadata(update, host=host)),
    }


def _event_taxonomy(value: str | None) -> tuple[str, str]:
    normalized = _event_type(value)
    categories = {
        "created": "lifecycle", "activated": "lifecycle", "suspended": "lifecycle", "retired": "lifecycle",
        "configuration_changed": "configuration", "port_changed": "configuration", "path_changed": "configuration", "setting_changed": "configuration",
        "deployed": "deployment", "upgraded": "deployment", "restarted": "deployment",
        "health_changed": "health", "started": "health", "stopped": "health", "recovered": "health", "degraded": "health",
        "endpoint_changed": "network", "exposure_changed": "network", "route_changed": "network",
        "credential_rotated": "security", "access_policy_changed": "security",
        "host_changed": "ownership", "owner_changed": "ownership",
        "capacity_changed": "capacity",
    }
    aliases = {"configured": "configuration_changed"}
    normalized = aliases.get(normalized, normalized)
    category = categories.get(normalized, "other")
    return category, normalized if category != "other" else "updated"


def apply_infrastructure_updates(
    paths: RuntimePaths,
    business_date: date | str,
    updates: list[dict[str, Any]],
    *,
    source: str = "learning-pass",
) -> dict[str, int]:
    """Merge LLM-extracted infrastructure updates into the active graph."""
    migrate(paths)
    date_str = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    applied_entities = 0
    applied_events = 0
    now = _now()
    with connect(paths) as connection:
        for update in updates or []:
            if not isinstance(update, dict):
                continue
            entity_type = _entity_type(update.get("entityType") or update.get("type"))
            name = _normalize_name(update.get("name") or update.get("object") or update.get("target"))
            if not name:
                continue
            host = _normalize_name(update.get("host") or update.get("device") or update.get("parent"))
            requested_id = str(update.get("entityId") or "").strip()
            identity_mode = str(update.get("identityMode") or "").strip().lower()
            strict_catalog_identity = source in {
                "technical-pass",
                "environment-reconciliation",
            }
            if strict_catalog_identity:
                if identity_mode == "existing":
                    if not requested_id:
                        raise InfrastructureIdentityError(
                            "existing Technical infrastructure update requires entityId"
                        )
                    catalog_row = connection.execute(
                        "SELECT * FROM infrastructure_entities WHERE entity_id = ? AND archived_at IS NULL",
                        (requested_id,),
                    ).fetchone()
                    if catalog_row is None:
                        raise InfrastructureIdentityError(
                            "Technical infrastructure update references an unknown entityId"
                        )
                    if str(catalog_row["entity_type"]) != entity_type:
                        raise InfrastructureIdentityError(
                            "Technical infrastructure update entity type does not match the catalog"
                        )
                    entity_id = str(catalog_row["entity_id"])
                    canonical_key = str(catalog_row["canonical_key"])
                    # Entity identity is Foundation-owned.  Model wording may
                    # describe the change, but it may not rename the object.
                    name = str(catalog_row["name"])
                elif identity_mode == "new":
                    if requested_id:
                        raise InfrastructureIdentityError(
                            "new Technical infrastructure update must not provide entityId"
                        )
                    entity_id, canonical_key = _resolve_entity_id(
                        connection,
                        entity_type=entity_type,
                        name=name,
                        host=host,
                    )
                    collision = connection.execute(
                        "SELECT entity_id FROM infrastructure_entities WHERE entity_id = ?",
                        (entity_id,),
                    ).fetchone()
                    if collision is not None:
                        raise InfrastructureIdentityError(
                            "new Technical infrastructure update matches an existing catalog entity"
                        )
                else:
                    raise InfrastructureIdentityError(
                        "Technical infrastructure update requires identityMode existing or new"
                    )
            else:
                entity_id, canonical_key = _resolve_entity_id(
                    connection,
                    entity_type=entity_type,
                    name=name,
                    host=host,
                    requested_id=requested_id,
                )
            patch = _entity_patch(update, entity_type=entity_type, host=host)
            existing = connection.execute(
                "SELECT * FROM infrastructure_entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO infrastructure_entities(
                        entity_id, entity_type, canonical_key, name, kind, status, location,
                        endpoint, port, protocol, path, metadata_json,
                        subtype, lifecycle_status, health_status, environment, exposure_scope,
                        created_at, updated_at, last_seen_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        entity_type,
                        canonical_key,
                        redact_sensitive_value(name, field_name="name"),
                        patch["kind"],
                        patch["status"],
                        patch["location"],
                        patch["endpoint"],
                        patch["port"],
                        patch["protocol"],
                        patch["path"],
                        patch["metadata_json"],
                        patch["subtype"],
                        patch["lifecycle_status"],
                        patch["health_status"],
                        patch["environment"],
                        patch["exposure_scope"],
                        now,
                        now,
                        date_str,
                    ),
                )
                applied_entities += 1
            else:
                merged = dict(existing)
                for key in ("kind", "status", "location", "endpoint", "port", "protocol", "path"):
                    if patch[key]:
                        merged[key] = patch[key]
                metadata = {**_load_json(existing["metadata_json"], {}), **_load_json(patch["metadata_json"], {})}
                connection.execute(
                    """
                    UPDATE infrastructure_entities
                    SET name = ?, kind = ?, status = ?, location = ?, endpoint = ?, port = ?,
                        protocol = ?, path = ?, metadata_json = ?, updated_at = ?,
                        last_seen_date = ?, lifecycle_status = ?, health_status = ?,
                        archived_at = CASE WHEN ? = 'retired' THEN COALESCE(archived_at, ?) ELSE archived_at END
                    WHERE entity_id = ?
                    """,
                    (
                        redact_sensitive_value(name, field_name="name"),
                        merged["kind"],
                        merged["status"],
                        merged["location"],
                        merged["endpoint"],
                        merged["port"],
                        merged["protocol"],
                        merged["path"],
                        _json(metadata),
                        now,
                        date_str,
                        patch["lifecycle_status"] if patch["lifecycle_status"] != "unknown" else existing["lifecycle_status"],
                        patch["health_status"] if patch["health_status"] != "unknown" else existing["health_status"],
                        patch["lifecycle_status"],
                        now,
                        entity_id,
                    ),
                )
            aliases = [name, *(update.get("aliases") if isinstance(update.get("aliases"), list) else [])]
            for alias in aliases:
                normalized_alias = _slug(str(alias or ""))
                if normalized_alias:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO infrastructure_entity_aliases(entity_id, alias, normalized_alias, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (entity_id, redact_sensitive_value(alias, field_name="alias"), normalized_alias, now),
                    )
            change = redact_sensitive_value(update.get("change") or update.get("summary") or "updated", field_name="change")
            field = redact_sensitive_value(update.get("field") or "", field_name="field")
            current = redact_sensitive_value(update.get("currentValue") or update.get("value") or "", field_name=field)
            previous = redact_sensitive_value(update.get("previousValue") or "", field_name=field)
            evidence = update.get("evidence") if isinstance(update.get("evidence"), list) else []
            evidence = [redact_sensitive_value(item, field_name="evidence") for item in evidence if str(item or "").strip()]
            event_type = _event_type(update.get("eventType") or update.get("changeType"))
            event_category, normalized_event_type = _event_taxonomy(event_type)
            evidence_contract = {
                "schema": "actanara.environment-evidence.v1",
                "source": source,
                "evidenceRefs": evidence,
            }
            event_key = _stable_id("infra-event", date_str, entity_id, event_type, field, current, change)
            event_id = _stable_id("IE", event_key)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO infrastructure_events(
                    event_id, event_key, entity_id, business_date, event_type, summary,
                    field, previous_value, current_value, evidence_json, confidence,
                    source, raw_json, created_at, event_category,
                    normalized_event_type, evidence_contract_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_key,
                    entity_id,
                    date_str,
                    event_type,
                    change,
                    field,
                    previous,
                    current,
                    _json(evidence),
                    _confidence(update.get("confidence")),
                    source,
                    _json(_redacted_raw_update(update, field=field)),
                    now,
                    event_category,
                    normalized_event_type,
                    _json(evidence_contract),
                ),
            )
            if cursor.rowcount:
                applied_events += 1
    return {"entities": applied_entities, "events": applied_events}


CATALOG_ACTIONS = frozenset({"merge_existing", "archive_existing", "transition_state"})
CATALOG_LIFECYCLE_STATUSES = frozenset({"planned", "provisioning", "active", "suspended", "retired"})
CATALOG_HEALTH_STATUSES = frozenset({"healthy", "degraded", "unhealthy", "offline"})


def apply_infrastructure_catalog_actions(
    paths: RuntimePaths,
    business_date: date | str,
    actions: list[dict[str, Any]],
    *,
    source: str = "environment-reconciliation",
) -> dict[str, int]:
    """Apply reversible LLM-proposed catalog maintenance in one transaction.

    The model selects an action, but this executor owns identity, state, and
    history safety.  Entities and events are never deleted.  A merge moves
    aliases and hosted-service references to the canonical entity, then
    archives the duplicate while preserving its historical events.
    """

    migrate(paths)
    date_str = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    try:
        date.fromisoformat(date_str)
    except ValueError as exc:
        raise InfrastructureCatalogActionError("catalog action date is invalid") from exc
    normalized = [_normalize_catalog_action(item) for item in actions]
    if not normalized:
        return {"merged": 0, "archived": 0, "transitioned": 0, "events": 0}
    source_ids = [item["entityId"] for item in normalized]
    if len(source_ids) != len(set(source_ids)):
        raise InfrastructureCatalogActionError("catalog actions repeat an entity")

    now = _now()
    counts = {"merged": 0, "archived": 0, "transitioned": 0, "events": 0}
    with connect(paths) as connection:
        active_rows = {
            str(row["entity_id"]): row
            for row in connection.execute(
                "SELECT * FROM infrastructure_entities WHERE archived_at IS NULL"
            ).fetchall()
        }
        action_by_entity = {item["entityId"]: item for item in normalized}
        for item in normalized:
            entity = active_rows.get(item["entityId"])
            if entity is None:
                raise InfrastructureCatalogActionError("catalog action references an inactive entity")
            target_id = item["canonicalEntityId"]
            target = active_rows.get(target_id) if target_id else None
            if item["action"] == "merge_existing":
                if target is None or target_id == item["entityId"]:
                    raise InfrastructureCatalogActionError("catalog merge target is invalid")
                if str(target["entity_type"]) != str(entity["entity_type"]):
                    raise InfrastructureCatalogActionError("catalog merge types do not match")
                if target_id in action_by_entity:
                    raise InfrastructureCatalogActionError("catalog merge target is also being maintained")
            elif item["action"] == "archive_existing" and target_id:
                if target is None or target_id == item["entityId"]:
                    raise InfrastructureCatalogActionError("catalog archive replacement is invalid")
                if str(target["entity_type"]) != str(entity["entity_type"]):
                    raise InfrastructureCatalogActionError("catalog archive replacement type does not match")
                if target_id in action_by_entity:
                    raise InfrastructureCatalogActionError("catalog archive replacement is also being maintained")
            elif item["action"] == "archive_existing" and str(entity["entity_type"]) == "device":
                hosted = connection.execute(
                    """
                    SELECT 1 FROM infrastructure_entities
                    WHERE host_entity_id = ? AND archived_at IS NULL LIMIT 1
                    """,
                    (item["entityId"],),
                ).fetchone()
                if hosted is not None:
                    raise InfrastructureCatalogActionError(
                        "catalog archive would orphan an active hosted service"
                    )

        _validate_catalog_alias_moves(connection, normalized)

        for item in normalized:
            entity_id = item["entityId"]
            entity = active_rows[entity_id]
            action = item["action"]
            target_id = item["canonicalEntityId"]
            previous = _catalog_state_text(entity)
            if action in {"merge_existing", "archive_existing"}:
                if target_id:
                    _move_catalog_aliases(
                        connection,
                        source_entity=entity,
                        target_entity_id=target_id,
                        now=now,
                    )
                    if str(entity["entity_type"]) == "device":
                        connection.execute(
                            """
                            UPDATE infrastructure_entities
                            SET host_entity_id = ?, updated_at = ?
                            WHERE host_entity_id = ? AND archived_at IS NULL
                            """,
                            (target_id, now, entity_id),
                        )
                metadata = _load_json(entity["metadata_json"], {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata["catalogMaintenance"] = {
                    "action": action,
                    "supersededBy": target_id,
                    "businessDate": date_str,
                }
                connection.execute(
                    """
                    UPDATE infrastructure_entities
                    SET metadata_json = ?, archived_at = ?, updated_at = ?
                    WHERE entity_id = ? AND archived_at IS NULL
                    """,
                    (_json(metadata), now, now, entity_id),
                )
                counts["merged" if action == "merge_existing" else "archived"] += 1
                current = f"supersededBy={target_id}" if target_id else "archived"
            else:
                lifecycle = item["lifecycleStatus"] or str(entity["lifecycle_status"])
                health = item["healthStatus"] or str(entity["health_status"])
                status = item["healthStatus"] or item["lifecycleStatus"] or str(entity["status"])
                archived_at = now if lifecycle == "retired" else entity["archived_at"]
                connection.execute(
                    """
                    UPDATE infrastructure_entities
                    SET lifecycle_status = ?, health_status = ?, status = ?,
                        archived_at = ?, updated_at = ?, last_seen_date = ?
                    WHERE entity_id = ? AND archived_at IS NULL
                    """,
                    (lifecycle, health, status, archived_at, now, date_str, entity_id),
                )
                counts["transitioned"] += 1
                current = f"lifecycle={lifecycle};health={health}"
            if _insert_catalog_action_event(
                connection,
                business_date=date_str,
                entity_id=entity_id,
                action=action,
                reason=item["reason"],
                previous=previous,
                current=current,
                confidence=item["confidence"],
                source=source,
                now=now,
            ):
                counts["events"] += 1
    return counts


def _normalize_catalog_action(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "action", "entityId", "canonicalEntityId", "lifecycleStatus",
        "healthStatus", "observationIds", "reason", "confidence",
    }:
        raise InfrastructureCatalogActionError("catalog action contract is malformed")
    action = str(value.get("action") or "")
    entity_id = str(value.get("entityId") or "").strip()
    canonical_id = str(value.get("canonicalEntityId") or "").strip()
    lifecycle = str(value.get("lifecycleStatus") or "").strip()
    health = str(value.get("healthStatus") or "").strip()
    observation_ids = value.get("observationIds")
    reason = redact_sensitive_value(value.get("reason"), field_name="reason")
    confidence = str(value.get("confidence") or "").strip()
    if action not in CATALOG_ACTIONS or not entity_id:
        raise InfrastructureCatalogActionError("catalog action identity is invalid")
    if not isinstance(observation_ids, list) or not observation_ids or any(
        not isinstance(item, str) or not item for item in observation_ids
    ) or len(observation_ids) != len(set(observation_ids)):
        raise InfrastructureCatalogActionError("catalog action observations are invalid")
    if not reason or len(reason) > 1_000 or confidence != "high":
        raise InfrastructureCatalogActionError("catalog action authority is insufficient")
    if action == "merge_existing":
        if not canonical_id or lifecycle or health:
            raise InfrastructureCatalogActionError("catalog merge fields are invalid")
    elif action == "archive_existing":
        if lifecycle or health:
            raise InfrastructureCatalogActionError("catalog archive fields are invalid")
    else:
        if canonical_id or (not lifecycle and not health):
            raise InfrastructureCatalogActionError("catalog state transition fields are invalid")
        if lifecycle and lifecycle not in CATALOG_LIFECYCLE_STATUSES:
            raise InfrastructureCatalogActionError("catalog lifecycle transition is invalid")
        if health and health not in CATALOG_HEALTH_STATUSES:
            raise InfrastructureCatalogActionError("catalog health transition is invalid")
    return {
        "action": action,
        "entityId": entity_id,
        "canonicalEntityId": canonical_id,
        "lifecycleStatus": lifecycle,
        "healthStatus": health,
        "observationIds": list(observation_ids),
        "reason": reason,
        "confidence": confidence,
    }


def _validate_catalog_alias_moves(connection: Any, actions: list[dict[str, Any]]) -> None:
    merge_targets = {
        item["entityId"]: item["canonicalEntityId"]
        for item in actions
        if item["canonicalEntityId"]
    }
    for source_id, target_id in merge_targets.items():
        source = connection.execute(
            "SELECT name FROM infrastructure_entities WHERE entity_id = ? AND archived_at IS NULL",
            (source_id,),
        ).fetchone()
        aliases = [str(source["name"])] if source is not None else []
        aliases.extend(
            str(row["alias"])
            for row in connection.execute(
                "SELECT alias FROM infrastructure_entity_aliases WHERE entity_id = ?",
                (source_id,),
            ).fetchall()
        )
        for alias in aliases:
            normalized = _slug(alias)
            owners = {
                str(row["entity_id"])
                for row in connection.execute(
                    """
                    SELECT a.entity_id
                    FROM infrastructure_entity_aliases a
                    JOIN infrastructure_entities e ON e.entity_id = a.entity_id
                    WHERE a.normalized_alias = ? AND e.archived_at IS NULL
                    """,
                    (normalized,),
                ).fetchall()
            }
            unsafe = {
                owner for owner in owners
                if owner not in {source_id, target_id}
                and merge_targets.get(owner) != target_id
            }
            if unsafe:
                raise InfrastructureCatalogActionError("catalog alias move is ambiguous")


def _move_catalog_aliases(
    connection: Any,
    *,
    source_entity: Mapping[str, Any],
    target_entity_id: str,
    now: str,
) -> None:
    aliases = [str(source_entity["name"])]
    aliases.extend(
        str(row["alias"])
        for row in connection.execute(
            "SELECT alias FROM infrastructure_entity_aliases WHERE entity_id = ?",
            (source_entity["entity_id"],),
        ).fetchall()
    )
    for alias in aliases:
        connection.execute(
            """
            INSERT OR IGNORE INTO infrastructure_entity_aliases(entity_id, alias, normalized_alias, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (target_entity_id, redact_sensitive_value(alias, field_name="alias"), _slug(alias), now),
        )
    connection.execute(
        "DELETE FROM infrastructure_entity_aliases WHERE entity_id = ?",
        (source_entity["entity_id"],),
    )


def _catalog_state_text(entity: Mapping[str, Any]) -> str:
    return (
        f"lifecycle={entity['lifecycle_status']};"
        f"health={entity['health_status']};archived={bool(entity['archived_at'])}"
    )


def _insert_catalog_action_event(
    connection: Any,
    *,
    business_date: str,
    entity_id: str,
    action: str,
    reason: str,
    previous: str,
    current: str,
    confidence: str,
    source: str,
    now: str,
) -> bool:
    event_key = _stable_id("infra-catalog-event", business_date, entity_id, action, current)
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO infrastructure_events(
            event_id, event_key, entity_id, business_date, event_type, summary,
            field, previous_value, current_value, evidence_json, confidence,
            source, raw_json, created_at, event_category,
            normalized_event_type, evidence_contract_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, '{}', ?, 'other', 'updated', ?)
        """,
        (
            _stable_id("IE", event_key), event_key, entity_id, business_date,
            f"catalog_{action}", redact_sensitive_value(reason, field_name="reason"),
            "other", previous, current, confidence, source, now,
            _json({
                "schema": "actanara.environment-catalog-action.v1",
                "source": source,
                "evidenceBound": True,
            }),
        ),
    )
    return bool(cursor.rowcount)


def list_infrastructure_entities(paths: RuntimePaths) -> list[dict[str, Any]]:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM infrastructure_entities
            WHERE archived_at IS NULL
            ORDER BY entity_type, name
            """
        ).fetchall()
    return [_entity_from_row(row) for row in rows]


def infrastructure_entity_catalog(paths: RuntimePaths) -> dict[str, dict[str, Any]]:
    """Return the active, safe identity catalog keyed by authoritative entity ID."""

    entities = list_infrastructure_entities(paths)
    aliases: dict[str, list[str]] = {}
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT a.entity_id, a.alias
            FROM infrastructure_entity_aliases a
            JOIN infrastructure_entities e ON e.entity_id = a.entity_id
            WHERE e.archived_at IS NULL
            ORDER BY a.entity_id, a.normalized_alias
            """
        ).fetchall()
    for row in rows:
        aliases.setdefault(str(row["entity_id"]), []).append(str(row["alias"]))
    return {
        str(entity["entityId"]): {
            **entity,
            "aliases": tuple(aliases.get(str(entity["entityId"]), ())),
        }
        for entity in entities
    }


def _entity_from_row(row: Any) -> dict[str, Any]:
    metadata = _load_json(row["metadata_json"], {})
    return {
        "entityId": row["entity_id"],
        "entityType": row["entity_type"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "subtype": row["subtype"],
        "lifecycleStatus": row["lifecycle_status"],
        "healthStatus": row["health_status"],
        "environment": row["environment"],
        "exposureScope": row["exposure_scope"],
        "location": row["location"],
        "endpoint": row["endpoint"],
        "port": row["port"],
        "protocol": row["protocol"],
        "path": row["path"],
        "hostEntityId": row["host_entity_id"],
        "metadata": metadata if isinstance(metadata, dict) else {},
        "lastSeenDate": row["last_seen_date"],
        "updatedAt": row["updated_at"],
    }


def infrastructure_events_for_date(paths: RuntimePaths, business_date: date | str) -> list[dict[str, Any]]:
    migrate(paths)
    date_str = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT ev.*, ent.name, ent.entity_type
            FROM infrastructure_events ev
            JOIN infrastructure_entities ent ON ent.entity_id = ev.entity_id
            WHERE ev.business_date = ?
            ORDER BY ev.created_at, ev.event_id
            """,
            (date_str,),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def _event_from_row(row: Any) -> dict[str, Any]:
    return {
        "eventId": row["event_id"],
        "entityId": row["entity_id"],
        "entityType": row["entity_type"],
        "name": row["name"],
        "businessDate": row["business_date"],
        "eventType": row["event_type"],
        "eventCategory": row["event_category"],
        "normalizedEventType": row["normalized_event_type"],
        "summary": row["summary"],
        "field": row["field"],
        "previousValue": row["previous_value"],
        "currentValue": row["current_value"],
        "evidence": _load_json(row["evidence_json"], []),
        "confidence": row["confidence"],
        "source": row["source"],
        "createdAt": row["created_at"],
    }


def recent_infrastructure_events(paths: RuntimePaths, *, limit: int = 50) -> list[dict[str, Any]]:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT ev.*, ent.name, ent.entity_type
            FROM infrastructure_events ev
            JOIN infrastructure_entities ent ON ent.entity_id = ev.entity_id
            ORDER BY ev.business_date DESC, ev.created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit or 50)),),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def render_infrastructure_graph_context(
    paths: RuntimePaths,
    *,
    max_entities: int | None = 40,
    max_events: int = 20,
) -> str:
    entities = list_infrastructure_entities(paths)
    if max_entities is not None:
        entities = entities[:max(0, int(max_entities))]
    events = recent_infrastructure_events(paths, limit=max_events)
    if not entities and not events:
        return "Infrastructure active graph is empty. Create new entity rows only with direct technical evidence."
    lines = [
        "Infrastructure Active Graph (redacted; prefer updating existing entityId when names/endpoints match):"
    ]
    for entity in entities:
        details = [
            f"entityId={entity['entityId']}",
            f"type={entity['entityType']}",
            f"name={entity['name']}",
        ]
        for key, label in (("kind", "kind"), ("status", "status"), ("location", "location"), ("endpoint", "endpoint"), ("port", "port")):
            if entity.get(key):
                details.append(f"{label}={entity[key]}")
        host = entity.get("metadata", {}).get("host") if isinstance(entity.get("metadata"), dict) else ""
        if host:
            details.append(f"host={host}")
        lines.append("- " + "; ".join(details))
    if events:
        lines.append("Recent infrastructure events:")
        for event in events[:max_events]:
            current = f" -> {event['currentValue']}" if event.get("currentValue") else ""
            lines.append(
                f"- {event['businessDate']} {event['entityId']} {event['eventType']}: {event['summary']}{current}"
            )
    return "\n".join(lines)


def dashboard_infrastructure_payload(paths: RuntimePaths) -> dict[str, Any]:
    entities = list_infrastructure_entities(paths)
    events = recent_infrastructure_events(paths, limit=80)
    by_entity: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_entity.setdefault(str(event["entityId"]), []).append(_dashboard_event(event))
    devices = []
    services = []
    for entity in entities:
        item = {
            "entityId": entity["entityId"],
            "name": entity["name"],
            "type": entity["kind"] or entity["entityType"],
            "status": entity["status"],
            "role": entity.get("metadata", {}).get("role", "") if isinstance(entity.get("metadata"), dict) else "",
            "location": entity["location"],
            "endpoint": entity["endpoint"],
            "port": entity["port"],
            "protocol": entity["protocol"],
            "path": entity["path"],
            "recentActivity": by_entity.get(str(entity["entityId"]), [])[:5],
        }
        if entity["entityType"] == "device":
            item["services"] = []
            devices.append(item)
        else:
            host = entity.get("metadata", {}).get("host", "") if isinstance(entity.get("metadata"), dict) else ""
            item["host"] = host
            services.append(item)
    devices_by_name = {_slug(device["name"]): device for device in devices}
    unassigned_services = []
    for service in services:
        host = _slug(service.get("host") or "")
        if host and host in devices_by_name:
            devices_by_name[host].setdefault("services", []).append(service)
        else:
            unassigned_services.append(service)
    return {
        "devices": devices,
        "services": unassigned_services,
        "recentActivity": [_dashboard_event(event) for event in events[:12]],
        "dataAuthority": "foundation-infrastructure-graph-v1",
        "redacted": True,
    }


def _dashboard_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": event["eventId"],
        "date": event["businessDate"],
        "entityId": event["entityId"],
        "entityType": event["entityType"],
        "name": event["name"],
        "type": event["eventType"],
        "summary": event["summary"],
        "field": event["field"],
        "current": event["currentValue"],
        "confidence": event["confidence"],
    }
