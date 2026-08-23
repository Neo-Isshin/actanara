"""Authority-backed catalog for durable engineering and work products."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping

from .db import connect, migrate
from .paths import RuntimePaths


ARTIFACT_STATUSES = frozenset({"observed", "produced", "verified", "superseded", "archived"})
ARTIFACT_ACTIONS = frozenset({"update_existing", "create_new", "defer", "drop"})
ARTIFACT_CATALOG_ACTIONS = frozenset({"merge_existing", "archive_existing", "transition_state"})
ARTIFACT_EVENT_TYPES = frozenset({"observed", "produced", "verified", "updated", "superseded", "archived"})
CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})


class EngineeringArtifactError(ValueError):
    """Raised when an engineering artifact cannot receive catalog authority."""


def engineering_artifact_catalog(paths: RuntimePaths) -> dict[str, dict[str, Any]]:
    """Return the complete catalog, including archived rows for identity matching."""

    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT artifact_id, canonical_key, name, artifact_type, status, version,
                   locator, summary, project, source, first_seen_date, last_seen_date,
                   created_at, updated_at, archived_at
            FROM engineering_artifacts
            ORDER BY CASE status WHEN 'archived' THEN 1 ELSE 0 END,
                     last_seen_date DESC, name, artifact_id
            """
        ).fetchall()
        alias_rows = connection.execute(
            """
            SELECT artifact_id, alias
            FROM engineering_artifact_aliases
            ORDER BY artifact_id, normalized_alias
            """
        ).fetchall()
    aliases: dict[str, list[str]] = {}
    for row in alias_rows:
        aliases.setdefault(str(row["artifact_id"]), []).append(str(row["alias"]))
    return {
        str(row["artifact_id"]): {
            "artifactId": str(row["artifact_id"]),
            "canonicalKey": str(row["canonical_key"]),
            "name": str(row["name"]),
            "artifactType": str(row["artifact_type"]),
            "status": str(row["status"]),
            "version": str(row["version"]),
            "locator": str(row["locator"]),
            "summary": str(row["summary"]),
            "project": str(row["project"]),
            "source": str(row["source"]),
            "firstSeenDate": str(row["first_seen_date"]),
            "lastSeenDate": str(row["last_seen_date"]),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "archivedAt": str(row["archived_at"] or ""),
            "aliases": aliases.get(str(row["artifact_id"]), []),
        }
        for row in rows
    }


def apply_engineering_artifact_reconciliation(
    paths: RuntimePaths,
    *,
    business_date: str,
    decisions: Iterable[Mapping[str, Any]],
    catalog_actions: Iterable[Mapping[str, Any]],
    evidence_by_ref: Mapping[str, str],
    source: str = "asset-reconciliation",
) -> dict[str, Any]:
    """Validate and apply independently parsed artifact decisions and maintenance."""

    migrate(paths)
    catalog = engineering_artifact_catalog(paths)
    accepted = 0
    rejected = 0
    rejection_reasons: Counter[str] = Counter()
    events = 0
    accepted_candidates: set[str] = set()
    now = datetime.now().astimezone().isoformat()
    normalized_evidence = {
        str(key): str(value)
        for key, value in evidence_by_ref.items()
        if re.fullmatch(r"E\d{6}", str(key)) and isinstance(value, str)
    }
    with connect(paths) as connection:
        for raw in decisions:
            try:
                decision = _validated_decision(raw, catalog, normalized_evidence)
            except EngineeringArtifactError as error:
                rejected += 1
                rejection_reasons[str(error)] += 1
                continue
            if decision["action"] not in {"update_existing", "create_new"}:
                continue
            candidate_id = decision["candidateId"]
            existing_id = decision["existingArtifactId"]
            if decision["action"] == "update_existing":
                artifact_id = existing_id
                current = catalog[artifact_id]
                canonical_key = current["canonicalKey"]
                name = decision["name"] or current["name"]
            else:
                name = decision["name"]
                canonical_key = _canonical_key(decision["project"], name)
                if any(item["canonicalKey"] == canonical_key for item in catalog.values()):
                    rejected += 1
                    continue
                artifact_id = "ART-" + hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:24]
                current = {}
            status = decision["status"]
            locator = decision["locator"] or str(current.get("locator") or "")
            version = decision["version"] or str(current.get("version") or "")
            project = decision["project"] or str(current.get("project") or "")
            artifact_type = decision["artifactType"] or str(current.get("artifactType") or "other")
            first_seen = str(current.get("firstSeenDate") or business_date)
            connection.execute(
                """
                INSERT INTO engineering_artifacts(
                    artifact_id, canonical_key, name, artifact_type, status, version,
                    locator, summary, project, source, first_seen_date, last_seen_date,
                    created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    name=excluded.name,
                    artifact_type=excluded.artifact_type,
                    status=excluded.status,
                    version=excluded.version,
                    locator=excluded.locator,
                    summary=excluded.summary,
                    project=excluded.project,
                    source=excluded.source,
                    last_seen_date=excluded.last_seen_date,
                    updated_at=excluded.updated_at,
                    archived_at=excluded.archived_at
                """,
                (
                    artifact_id, canonical_key, name, artifact_type, status, version,
                    locator, decision["summary"], project, source, first_seen,
                    business_date, now, now, now if status == "archived" else None,
                ),
            )
            event_type = _event_type(status, decision["action"])
            event_key = "|".join((business_date, artifact_id, event_type, decision["summary"]))
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO engineering_artifact_events(
                    event_id, event_key, artifact_id, business_date, event_type,
                    summary, evidence_refs_json, confidence, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "AE-" + hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:24],
                    hashlib.sha256(event_key.encode("utf-8")).hexdigest(),
                    artifact_id, business_date, event_type, decision["summary"],
                    json.dumps(decision["evidenceRefs"], ensure_ascii=False),
                    decision["confidence"], source, now,
                ),
            )
            events += int(bool(cursor.rowcount))
            accepted += 1
            accepted_candidates.add(candidate_id)
            catalog[artifact_id] = {
                "artifactId": artifact_id, "canonicalKey": canonical_key, "name": name,
                "artifactType": artifact_type, "status": status, "version": version,
                "locator": locator, "summary": decision["summary"], "project": project,
                "firstSeenDate": first_seen, "lastSeenDate": business_date,
            }

        maintained = 0
        deferred_actions = 0
        for raw in catalog_actions:
            try:
                action = _validated_catalog_action(raw, catalog, accepted_candidates)
            except EngineeringArtifactError:
                deferred_actions += 1
                continue
            if action["confidence"] != "high":
                deferred_actions += 1
                continue
            artifact_id = action["artifactId"]
            if action["action"] == "merge_existing":
                canonical_id = action["canonicalArtifactId"]
                duplicate = catalog[artifact_id]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO engineering_artifact_aliases(
                        artifact_id, alias, normalized_alias, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (canonical_id, duplicate["name"], _identity(duplicate["name"]), now),
                )
                connection.execute(
                    """
                    UPDATE engineering_artifacts
                    SET status='archived', archived_at=?, updated_at=?
                    WHERE artifact_id=?
                    """,
                    (now, now, artifact_id),
                )
            elif action["action"] == "archive_existing":
                connection.execute(
                    """
                    UPDATE engineering_artifacts
                    SET status='archived', archived_at=?, updated_at=?
                    WHERE artifact_id=?
                    """,
                    (now, now, artifact_id),
                )
            else:
                status = action["status"]
                connection.execute(
                    """
                    UPDATE engineering_artifacts
                    SET status=?, archived_at=?, updated_at=?
                    WHERE artifact_id=?
                    """,
                    (status, now if status == "archived" else None, now, artifact_id),
                )
            maintained += 1
    return {
        "accepted": accepted,
        "rejected": rejected,
        "events": events,
        "catalogActions": maintained,
        "catalogActionsDeferred": deferred_actions,
        "rejectionReasons": dict(sorted(rejection_reasons.items())),
    }


def active_engineering_artifacts(paths: RuntimePaths) -> list[dict[str, Any]]:
    return [
        item
        for item in engineering_artifact_catalog(paths).values()
        if item.get("status") != "archived"
    ]


def _validated_decision(
    raw: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
    evidence: Mapping[str, str],
) -> dict[str, Any]:
    required = {
        "candidateId", "action", "existingArtifactId", "name", "artifactType",
        "summary", "evidenceRefs", "reason", "confidence", "normalized",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise EngineeringArtifactError("artifact decision has an invalid shape")
    action = str(raw.get("action") or "")
    existing_id = str(raw.get("existingArtifactId") or "")
    candidate_id = str(raw.get("candidateId") or "")
    confidence = str(raw.get("confidence") or "")
    refs = raw.get("evidenceRefs")
    normalized = raw.get("normalized")
    if action not in ARTIFACT_ACTIONS or confidence not in CONFIDENCE_VALUES:
        raise EngineeringArtifactError("artifact decision enum is invalid")
    if not re.fullmatch(r"A\d{6}", candidate_id):
        raise EngineeringArtifactError("artifact candidate id is invalid")
    if (action == "update_existing") != bool(existing_id):
        raise EngineeringArtifactError("artifact identity action is invalid")
    if existing_id and existing_id not in catalog:
        raise EngineeringArtifactError("artifact identity is unknown")
    if not isinstance(refs, list) or len(refs) != len(set(refs)):
        raise EngineeringArtifactError("artifact evidence refs are invalid")
    if any(not isinstance(ref, str) or ref not in evidence for ref in refs):
        raise EngineeringArtifactError("artifact cites unknown evidence")
    if not isinstance(normalized, Mapping) or set(normalized) != {"status", "version", "locator", "project"}:
        raise EngineeringArtifactError("artifact normalized fields are invalid")
    values = {
        "candidateId": candidate_id,
        "action": action,
        "existingArtifactId": existing_id,
        "name": str(raw.get("name") or "").strip(),
        "artifactType": str(raw.get("artifactType") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "evidenceRefs": list(refs),
        "reason": str(raw.get("reason") or "").strip(),
        "confidence": confidence,
        "status": str(normalized.get("status") or "").strip(),
        "version": str(normalized.get("version") or "").strip(),
        "locator": str(normalized.get("locator") or "").strip(),
        "project": str(normalized.get("project") or "").strip(),
    }
    if any("\x00" in value or len(value) > 8_000 for key, value in values.items() if isinstance(value, str)):
        raise EngineeringArtifactError("artifact text field is invalid")
    accepted = action in {"update_existing", "create_new"}
    if accepted and (
        not values["name"] or not values["artifactType"] or not values["summary"]
        or not refs or values["status"] not in ARTIFACT_STATUSES
    ):
        raise EngineeringArtifactError("accepted artifact is incomplete")
    if accepted and _obvious_non_artifact(values["name"], values["artifactType"]):
        raise EngineeringArtifactError("candidate is evidence rather than an engineering artifact")
    if action == "create_new" and (
        values["status"] not in {"produced", "verified"} or not values["locator"]
    ):
        raise EngineeringArtifactError("new artifact lacks a produced locator")
    if not accepted and any((values["status"], values["version"], values["locator"], values["project"])):
        raise EngineeringArtifactError("deferred artifact carries normalized authority")
    joined = "\n".join(evidence[ref] for ref in refs)
    # Names and project labels are semantic normalizations: exact string
    # matching would reject useful Chinese/English aliases and hand authority
    # back to a brittle parser.  Concrete locator/version claims remain exact.
    for key in ("version", "locator"):
        value = values[key]
        if accepted and value and action == "create_new" and value.casefold() not in joined.casefold():
            raise EngineeringArtifactError(f"artifact {key} is not supported by evidence")
    return values


def _validated_catalog_action(
    raw: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
    accepted_candidates: set[str],
) -> dict[str, Any]:
    required = {
        "action", "artifactId", "canonicalArtifactId", "status",
        "candidateIds", "reason", "confidence",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise EngineeringArtifactError("artifact catalog action shape is invalid")
    action = str(raw.get("action") or "")
    artifact_id = str(raw.get("artifactId") or "")
    canonical_id = str(raw.get("canonicalArtifactId") or "")
    status = str(raw.get("status") or "")
    candidate_ids = raw.get("candidateIds")
    confidence = str(raw.get("confidence") or "")
    if action not in ARTIFACT_CATALOG_ACTIONS or artifact_id not in catalog:
        raise EngineeringArtifactError("artifact catalog action identity is invalid")
    if not isinstance(candidate_ids, list) or not candidate_ids or not set(candidate_ids).issubset(accepted_candidates):
        raise EngineeringArtifactError("artifact catalog action evidence is invalid")
    if confidence not in CONFIDENCE_VALUES or not str(raw.get("reason") or "").strip():
        raise EngineeringArtifactError("artifact catalog action explanation is invalid")
    if action == "merge_existing" and (canonical_id not in catalog or canonical_id == artifact_id or status):
        raise EngineeringArtifactError("artifact merge action is invalid")
    if action == "archive_existing" and (canonical_id or status):
        raise EngineeringArtifactError("artifact archive action is invalid")
    if action == "transition_state" and (canonical_id or status not in ARTIFACT_STATUSES):
        raise EngineeringArtifactError("artifact transition action is invalid")
    return {
        "action": action,
        "artifactId": artifact_id,
        "canonicalArtifactId": canonical_id,
        "status": status,
        "candidateIds": list(candidate_ids),
        "reason": str(raw.get("reason") or "").strip(),
        "confidence": confidence,
    }


def _canonical_key(project: str, name: str) -> str:
    identity = "|".join((_identity(project), _identity(name)))
    return "engineering-artifact:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _identity(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", str(value or "").casefold()))


def _event_type(status: str, action: str) -> str:
    if action == "update_existing" and status == "observed":
        return "updated"
    return status if status in ARTIFACT_EVENT_TYPES else "updated"


def _obvious_non_artifact(name: str, artifact_type: str) -> bool:
    combined = _identity(f"{artifact_type} {name}")
    tokens = set(combined.split())
    blocked_tokens = {"commit", "日志", "log", "cache", "缓存", "lock"}
    blocked_phrases = {
        "git commit", "temporary file", "temp file", "临时文件",
        "每日技术报告", "技术进展报告", "daily technical report",
        "environment ledger", "环境账本", "local ledger", "stage contract",
        "change set", "run report", "dependency lockfile", "prompt module set",
    }
    return bool(tokens & blocked_tokens) or any(phrase in combined for phrase in blocked_phrases)


__all__ = [
    "ARTIFACT_ACTIONS",
    "ARTIFACT_CATALOG_ACTIONS",
    "ARTIFACT_STATUSES",
    "EngineeringArtifactError",
    "active_engineering_artifacts",
    "apply_engineering_artifact_reconciliation",
    "engineering_artifact_catalog",
]
