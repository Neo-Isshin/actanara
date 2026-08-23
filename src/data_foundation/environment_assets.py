"""Private Technical observations, reconciled assets, and safe projections.

Technical emits only lightweight evidence-bound observations.  Environment
Recon owns classification, identity, state, and catalog decisions.  This
module validates both contracts, encrypts their ledgers, and exposes only
generalized Foundation/RAG projections.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from .infrastructure import apply_infrastructure_updates
from .paths import RuntimePaths
from .secret_store import SecretRef, default_secret_backend, read_secret, store_secret


LEDGER_SCHEMA = "actanara.environment-assets.v1"
ENVELOPE_SCHEMA = "actanara.environment-assets.envelope.v1"
LEGACY_OBSERVATION_SCHEMA = "actanara.environment-observations.v1"
LEGACY_OBSERVATION_ENVELOPE_SCHEMA = "actanara.environment-observations.envelope.v1"
OBSERVATION_SCHEMA = "actanara.environment-observations.v2"
OBSERVATION_ENVELOPE_SCHEMA = "actanara.environment-observations.envelope.v2"
RECONCILIATION_AUDIT_SCHEMA = "actanara.environment-reconciliation-audit.v1"
RECONCILIATION_AUDIT_ENVELOPE_SCHEMA = "actanara.environment-reconciliation-audit.envelope.v1"
PRIVATE_HEADING = "## 九、环境观察私有账本"
LEDGER_FILE_RE = re.compile(r"^environment-assets-v1-(\d{4}-\d{2}-\d{2})\.json$")
OBSERVATION_FILE_RE = re.compile(r"^environment-observations-v[12]-(\d{4}-\d{2}-\d{2})\.json$")
EVIDENCE_REF_RE = re.compile(r"^E\d{6}$")
# ``deliverable`` remains readable for ledgers produced by the short-lived v1
# experiment, but it is no longer an active infrastructure class.  Engineering
# outputs belong to Technical/Nova-Task, not to the device/service catalog.
ASSET_CLASSES = frozenset({"device", "service"})
LEGACY_ASSET_CLASSES = frozenset({"device", "service", "deliverable"})
ASSET_STATES = frozenset({"observed", "planned", "active", "suspended", "retired", "produced", "verified", "superseded"})
CHANGE_TYPES = frozenset({"observed", "planned", "created", "configured", "deployed", "updated", "started", "stopped", "recovered", "degraded", "retired", "produced", "verified", "superseded"})
CONFIDENCE_VALUES = frozenset({"high", "medium", "low"})
INFRASTRUCTURE_IDENTITY_MODES = frozenset({"existing", "new"})
MAX_PRIVATE_LEDGER_BYTES = 8 * 1024 * 1024
MAX_FIELD_CHARS = 8_000
PRIVATE_FIELDS = ("host", "location", "endpoint", "port", "path", "version")

SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|pwd|token|api[_-]?key|secret|credential|private[_-]?key|authorization|cookie)\s*[:=]"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>()\"'`,;]+", re.IGNORECASE)
IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_RE = re.compile(
    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])"
)
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w-])"
)
FQDN_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![\w.-])"
)
ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])(?:/[^\s]+|[A-Za-z]:\\[^\s]+)")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|credential|private[_-]?key|authorization|cookie)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;]+)"
)
PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
FILELIKE_SUFFIXES = frozenset(
    {
        "css", "csv", "html", "ini", "jpeg", "jpg", "js", "json", "jsonl",
        "log", "md", "pdf", "png", "py", "sh", "sql", "svg", "toml", "ts",
        "txt", "yaml", "yml", "zsh",
    }
)


class EnvironmentAssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class TechnicalEnvironmentResult:
    report_markdown: str
    assets: tuple[dict[str, Any], ...]
    rejected_asset_count: int = 0


@dataclass(frozen=True)
class EnvironmentObservationLedger:
    business_date: str
    observations: tuple[dict[str, Any], ...]
    evidence_by_ref: dict[str, str]


@dataclass(frozen=True)
class EnvironmentMemoryRecord:
    record_id: str
    business_date: str
    asset_class: str
    name: str
    text: str
    source_path: Path


def parse_technical_environment_output(
    raw_output: str,
    *,
    business_date: str,
    evidence_by_ref: Mapping[str, str],
) -> TechnicalEnvironmentResult:
    """Split a Technical report from its evidence-bound observation block."""

    marker = f"\n{PRIVATE_HEADING}\n"
    if raw_output.count(PRIVATE_HEADING) != 1 or marker not in raw_output:
        raise EnvironmentAssetError("Technical output is missing the private environment ledger")
    report, encoded = raw_output.rsplit(marker, 1)
    match = re.fullmatch(r"```json\s*\n([\s\S]*?)\n```\s*", encoded.strip())
    if match is None:
        raise EnvironmentAssetError("private environment ledger must be one JSON code block")
    payload = _load_llm_json_object(match.group(1))
    if not isinstance(payload, dict) or set(payload) != {"schema", "businessDate", "observations"}:
        raise EnvironmentAssetError("private environment ledger has an invalid top-level contract")
    if payload.get("schema") != OBSERVATION_SCHEMA or payload.get("businessDate") != business_date:
        raise EnvironmentAssetError("private environment ledger context does not match the run")
    rows = payload.get("observations")
    if not isinstance(rows, list):
        raise EnvironmentAssetError("private environment assets must be a list")
    normalized_evidence = _normalize_evidence_map(evidence_by_ref)
    normalized_rows: list[dict[str, Any]] = []
    rejected = 0
    identities: set[str] = set()
    for row in rows:
        try:
            item = _normalize_observation(
                row,
                business_date=business_date,
                allowed_refs=set(normalized_evidence),
            )
        except EnvironmentAssetError:
            rejected += 1
            continue
        if item["assetId"] in identities:
            rejected += 1
            continue
        identities.add(item["assetId"])
        normalized_rows.append(item)
    normalized = tuple(normalized_rows)
    report = report.strip()
    if not report.startswith("# "):
        raise EnvironmentAssetError("Technical report Markdown is missing its title")
    return TechnicalEnvironmentResult(
        report_markdown=_append_public_summary(_generalize(report), normalized),
        assets=normalized,
        rejected_asset_count=rejected,
    )


def _load_llm_json_object(raw: str) -> Any:
    """Load strict JSON, repairing only unambiguous trailing commas.

    LLMs occasionally emit ``[, ]`` or ``{, }``-style trailing commas even
    when the requested contract is otherwise exact.  Removing those commas is
    syntax-only and does not invent values.  All other malformed forms remain
    fail-closed, and duplicate object keys are still rejected.
    """

    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as first_error:
        repaired = _strip_json_trailing_commas(raw)
        if repaired == raw:
            raise EnvironmentAssetError(
                "private environment ledger is not strict JSON "
                f"({first_error.msg} at line {first_error.lineno} column {first_error.colno})"
            ) from first_error
        try:
            return json.loads(repaired, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                detail = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
            else:
                detail = "duplicate object key"
            raise EnvironmentAssetError(
                f"private environment ledger is not strict JSON ({detail})"
            ) from exc
    except ValueError as exc:
        raise EnvironmentAssetError(
            "private environment ledger is not strict JSON (duplicate object key)"
        ) from exc


def _strip_json_trailing_commas(raw: str) -> str:
    """Remove commas followed only by JSON whitespace and a closing token."""

    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    changed = False
    while index < len(raw):
        char = raw[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(raw) and raw[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead < len(raw) and raw[lookahead] in "}]":
                changed = True
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output) if changed else raw


def write_environment_asset_ledger(
    paths: RuntimePaths,
    *,
    business_date: str,
    assets: Iterable[dict[str, Any]],
) -> Path:
    """Encrypt and atomically write one reconciled date-bound authority ledger."""

    normalized = [
        _normalize_asset(
            _private_asset_payload(item),
            business_date=business_date,
            allowed_refs=None,
        )
        for item in assets
    ]
    payload = {
        "schema": LEDGER_SCHEMA,
        "businessDate": business_date,
        "assets": [_private_asset_payload(item) for item in normalized],
    }
    filename = f"environment-assets-v1-{business_date}.json"
    return _write_encrypted_payload(
        paths,
        business_date=business_date,
        payload_schema=LEDGER_SCHEMA,
        envelope_schema=ENVELOPE_SCHEMA,
        filename=filename,
        payload=payload,
    )


def read_environment_asset_ledger(
    paths: RuntimePaths,
    business_date: str,
) -> tuple[dict[str, Any], ...] | None:
    filename = f"environment-assets-v1-{business_date}.json"
    payload = _read_encrypted_payload(
        paths,
        business_date=business_date,
        payload_schema=LEDGER_SCHEMA,
        envelope_schema=ENVELOPE_SCHEMA,
        filename=filename,
    )
    if payload is None:
        return None
    if (
        set(payload) != {"schema", "businessDate", "assets"}
        or not isinstance(payload.get("assets"), list)
    ):
        return None
    try:
        return tuple(
            _normalize_asset(
                item,
                business_date=business_date,
                allowed_refs=None,
                evidence_by_ref=None,
                infrastructure_catalog=None,
                enforce_infrastructure_authority=False,
                allow_legacy_deliverable=True,
            )
            for item in payload["assets"]
        )
    except EnvironmentAssetError:
        return None


def write_environment_observation_ledger(
    paths: RuntimePaths,
    *,
    business_date: str,
    observations: Iterable[dict[str, Any]],
    evidence_by_ref: Mapping[str, str],
) -> Path:
    """Write Technical observations and their bounded evidence without granting authority."""

    rows = list(observations)
    required_refs = {
        str(ref)
        for row in rows
        for ref in (row.get("evidenceRefs") if isinstance(row.get("evidenceRefs"), list) else [])
    }
    normalized_evidence = _normalize_evidence_map(evidence_by_ref)
    if not required_refs.issubset(normalized_evidence):
        raise EnvironmentAssetError("environment observation evidence is incomplete")
    normalized_rows = [
        _normalize_observation(
            _private_observation_payload(row),
            business_date=business_date,
            allowed_refs=set(normalized_evidence),
        )
        for row in rows
    ]
    payload = {
        "schema": OBSERVATION_SCHEMA,
        "businessDate": business_date,
        "observations": [_private_observation_payload(item) for item in normalized_rows],
        "evidenceByRef": {
            ref: _sanitize_private_evidence(value)
            for ref, value in sorted(normalized_evidence.items())
        },
    }
    return _write_encrypted_payload(
        paths,
        business_date=business_date,
        payload_schema=OBSERVATION_SCHEMA,
        envelope_schema=OBSERVATION_ENVELOPE_SCHEMA,
        filename=f"environment-observations-v2-{business_date}.json",
        payload=payload,
    )


def read_environment_observation_ledger(
    paths: RuntimePaths,
    business_date: str,
) -> EnvironmentObservationLedger | None:
    v2_name = f"environment-observations-v2-{business_date}.json"
    v2_path = paths.home / "artifacts" / "environment" / v2_name
    if v2_path.exists() or v2_path.is_symlink():
        payload = _read_encrypted_payload(
            paths,
            business_date=business_date,
            payload_schema=OBSERVATION_SCHEMA,
            envelope_schema=OBSERVATION_ENVELOPE_SCHEMA,
            filename=v2_name,
        )
        normalize = _normalize_observation
    else:
        payload = _read_encrypted_payload(
            paths,
            business_date=business_date,
            payload_schema=LEGACY_OBSERVATION_SCHEMA,
            envelope_schema=LEGACY_OBSERVATION_ENVELOPE_SCHEMA,
            filename=f"environment-observations-v1-{business_date}.json",
        )
        normalize = None
    if payload is None or set(payload) != {
        "schema", "businessDate", "observations", "evidenceByRef"
    }:
        return None
    if not isinstance(payload.get("observations"), list) or not isinstance(payload.get("evidenceByRef"), dict):
        return None
    try:
        evidence = _normalize_evidence_map(payload["evidenceByRef"])
        if normalize is not None:
            observations = tuple(
                normalize(
                    item,
                    business_date=business_date,
                    allowed_refs=set(evidence),
                    allow_legacy_deliverable=True,
                )
                for item in payload["observations"]
            )
        else:
            observations = tuple(
                _normalize_asset(
                    item,
                    business_date=business_date,
                    allowed_refs=set(evidence),
                    allow_pending_identity=True,
                    allow_legacy_deliverable=True,
                )
                for item in payload["observations"]
            )
    except EnvironmentAssetError:
        return None
    required_refs = {
        ref
        for observation in observations
        for ref in observation["evidenceRefs"]
    }
    if not required_refs.issubset(evidence):
        return None
    return EnvironmentObservationLedger(
        business_date=business_date,
        observations=observations,
        evidence_by_ref=evidence,
    )


def write_environment_reconciliation_audit(
    paths: RuntimePaths,
    *,
    business_date: str,
    observation_decisions: Iterable[Mapping[str, Any]],
    catalog_actions: Iterable[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> Path:
    """Encrypt the complete accept/defer/drop and catalog-maintenance audit."""

    decisions = [dict(item) for item in observation_decisions]
    actions = [dict(item) for item in catalog_actions]
    result_payload = dict(result)
    if any(not isinstance(item, dict) for item in decisions + actions):
        raise EnvironmentAssetError("environment reconciliation audit rows are invalid")
    payload = {
        "schema": RECONCILIATION_AUDIT_SCHEMA,
        "businessDate": business_date,
        "observationDecisions": decisions,
        "catalogActions": actions,
        "result": result_payload,
    }
    return _write_encrypted_payload(
        paths,
        business_date=business_date,
        payload_schema=RECONCILIATION_AUDIT_SCHEMA,
        envelope_schema=RECONCILIATION_AUDIT_ENVELOPE_SCHEMA,
        filename=f"environment-reconciliation-audit-v1-{business_date}.json",
        payload=payload,
    )


def read_environment_reconciliation_audit(
    paths: RuntimePaths,
    business_date: str,
) -> dict[str, Any] | None:
    payload = _read_encrypted_payload(
        paths,
        business_date=business_date,
        payload_schema=RECONCILIATION_AUDIT_SCHEMA,
        envelope_schema=RECONCILIATION_AUDIT_ENVELOPE_SCHEMA,
        filename=f"environment-reconciliation-audit-v1-{business_date}.json",
    )
    if payload is None or set(payload) != {
        "schema", "businessDate", "observationDecisions", "catalogActions", "result"
    }:
        return None
    if (
        not isinstance(payload.get("observationDecisions"), list)
        or not isinstance(payload.get("catalogActions"), list)
        or not isinstance(payload.get("result"), dict)
    ):
        return None
    return payload


def environment_observation_ledger_ready(paths: RuntimePaths, business_date: str) -> bool:
    return read_environment_observation_ledger(paths, business_date) is not None


def environment_asset_ledger_ready(paths: RuntimePaths, business_date: str) -> bool:
    """An encrypted empty ledger is a valid Technical Pass result."""

    return read_environment_asset_ledger(paths, business_date) is not None


def bind_environment_asset_authority(
    observation: Mapping[str, Any],
    *,
    business_date: str,
    evidence_by_ref: Mapping[str, str],
    infrastructure_catalog: Mapping[str, Mapping[str, Any]],
    identity_mode: str = "",
    existing_entity_id: str = "",
) -> dict[str, Any]:
    """Bind one observation to current catalog identity and cited run evidence."""

    raw = _private_asset_payload(dict(observation))
    raw["identityMode"] = identity_mode
    raw["existingEntityId"] = existing_entity_id
    evidence = _normalize_evidence_map(evidence_by_ref)
    catalog = _normalize_infrastructure_catalog(infrastructure_catalog)
    raw_refs = raw.get("evidenceRefs")
    if not isinstance(raw_refs, list) or any(ref not in evidence for ref in raw_refs):
        raise EnvironmentAssetError("environment asset cites evidence outside this run")
    cited_evidence = "\n".join(evidence[ref] for ref in raw_refs)
    evidence_only_fields = ("project",)
    for field in evidence_only_fields:
        proposed = str(raw.get(field) or "").strip()
        if proposed and not _evidence_contains(proposed, cited_evidence):
            raise EnvironmentAssetError(
                f"environment asset {field} is not supported by cited evidence"
            )
    return _normalize_asset(
        raw,
        business_date=business_date,
        allowed_refs=set(evidence),
        evidence_by_ref=evidence,
        infrastructure_catalog=catalog,
        enforce_infrastructure_authority=True,
    )


def apply_environment_asset_projection(
    paths: RuntimePaths,
    *,
    business_date: str,
    assets: Iterable[dict[str, Any]],
    source: str = "environment-reconciliation",
) -> dict[str, int]:
    """Project generalized device/service state into Foundation."""

    rows = list(assets)
    if any(item.get("assetClass") not in ASSET_CLASSES for item in rows):
        raise EnvironmentAssetError("non-infrastructure asset cannot enter the environment projection")
    infrastructure = [_infrastructure_update(item) for item in rows]
    graph = apply_infrastructure_updates(paths, business_date, infrastructure, source=source)
    return {
        "entities": int(graph.get("entities") or 0),
        "infrastructureEvents": int(graph.get("events") or 0),
        "deliverables": 0,
        "deliverableEvents": 0,
    }


def collect_environment_memory_records(
    paths: RuntimePaths,
) -> tuple[tuple[EnvironmentMemoryRecord, ...], tuple[Path, ...]]:
    root = paths.home / "artifacts" / "environment"
    try:
        names = sorted(item.name for item in root.iterdir() if LEDGER_FILE_RE.fullmatch(item.name))
    except OSError:
        return (), ()
    records: list[EnvironmentMemoryRecord] = []
    sources: list[Path] = []
    for name in names:
        business_date = LEDGER_FILE_RE.fullmatch(name).group(1)  # type: ignore[union-attr]
        assets = read_environment_asset_ledger(paths, business_date)
        if assets is None:
            continue
        path = root / name
        active_assets = tuple(
            asset for asset in assets if asset["assetClass"] in ASSET_CLASSES
        )
        if not active_assets:
            continue
        sources.append(path)
        for asset in active_assets:
            public = _public_asset(asset)
            text = "\n".join(
                (
                    public["name"],
                    f"Asset class: {public['assetClass']}",
                    f"Kind: {public['kind']}",
                    f"State: {public['state']}",
                    f"Summary: {public['summary']}",
                )
            )
            records.append(
                EnvironmentMemoryRecord(
                    record_id=asset["assetId"],
                    business_date=business_date,
                    asset_class=asset["assetClass"],
                    name=public["name"],
                    text=text,
                    source_path=path,
                )
            )
    return tuple(records), tuple(sources)


def _normalize_evidence_map(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise EnvironmentAssetError("Technical evidence authority is unavailable")
    result: dict[str, str] = {}
    for raw_ref, raw_text in value.items():
        ref = str(raw_ref or "")
        if EVIDENCE_REF_RE.fullmatch(ref) is None or ref in result:
            raise EnvironmentAssetError("Technical evidence authority contains an invalid reference")
        if not isinstance(raw_text, str):
            raise EnvironmentAssetError("Technical evidence authority contains non-text evidence")
        result[ref] = raw_text
    return result


def _normalize_infrastructure_catalog(
    value: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise EnvironmentAssetError("Infrastructure entity catalog is unavailable")
    result: dict[str, dict[str, Any]] = {}
    for raw_id, raw_entity in value.items():
        entity_id = str(raw_id or "").strip()
        if not entity_id or not isinstance(raw_entity, Mapping):
            raise EnvironmentAssetError("Infrastructure entity catalog is malformed")
        entity = dict(raw_entity)
        if str(entity.get("entityId") or "") != entity_id:
            raise EnvironmentAssetError("Infrastructure entity catalog identity does not match its key")
        if str(entity.get("entityType") or "") not in {"device", "service"}:
            raise EnvironmentAssetError("Infrastructure entity catalog contains an invalid type")
        result[entity_id] = entity
    return result


def _evidence_contains(value: str, evidence: str) -> bool:
    needle = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    haystack = re.sub(r"\s+", " ", evidence).casefold()
    if not needle:
        return False
    if needle.isdigit():
        return re.search(rf"(?<!\d){re.escape(needle)}(?!\d)", haystack) is not None
    return needle in haystack


_IDENTITY_GENERIC_TERMS = frozenset(
    {
        "agent", "application", "daemon", "device", "host", "instance", "local",
        "machine", "managed", "remote", "runtime", "server", "service", "system",
        "worker",
    }
)


def _identity_terms(value: str) -> set[str]:
    latin = {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", value.casefold())
        if item not in _IDENTITY_GENERIC_TERMS
    }
    cjk_source = re.sub(
        r"(?:设备|服务|服务器|系统|实例|本地|远程|托管)",
        " ",
        value,
    )
    cjk = {
        item
        for item in re.findall(r"[\u3400-\u9fff]{2,}", cjk_source)
        if len(item) >= 2
    }
    return latin | cjk


def _identity_is_evidenced(names: Iterable[str], private: Mapping[str, str], evidence: str) -> bool:
    if any(_evidence_contains(value, evidence) for value in names if str(value or "").strip()):
        return True
    evidence_terms = _identity_terms(evidence)
    if any(_identity_terms(str(name or "")) & evidence_terms for name in names):
        return True
    return any(
        _evidence_contains(private.get(field, ""), evidence)
        for field in ("endpoint", "path", "host")
        if private.get(field)
    )


def _catalog_host(entity: Mapping[str, Any]) -> str:
    metadata = entity.get("metadata")
    if isinstance(metadata, Mapping):
        return str(metadata.get("host") or "")
    return ""


def _new_identity_collides(
    *,
    asset_class: str,
    name: str,
    private: Mapping[str, str],
    infrastructure_catalog: Mapping[str, Mapping[str, Any]],
) -> bool:
    normalized_name = re.sub(r"\s+", " ", name).strip().casefold()
    normalized_host = re.sub(r"\s+", " ", private.get("host", "")).strip().casefold()
    for entity in infrastructure_catalog.values():
        if str(entity.get("entityType") or "") != asset_class:
            continue
        aliases = entity.get("aliases") if isinstance(entity.get("aliases"), (list, tuple)) else ()
        known_names = {
            re.sub(r"\s+", " ", str(item or "")).strip().casefold()
            for item in (entity.get("name"), *aliases)
            if str(item or "").strip()
        }
        known_host = re.sub(r"\s+", " ", _catalog_host(entity)).strip().casefold()
        if normalized_name in known_names and not (
            normalized_host and known_host and normalized_host != known_host
        ):
            return True
        for field in ("endpoint", "path"):
            proposed = str(private.get(field) or "").strip().casefold()
            current = str(entity.get(field) or "").strip().casefold()
            if proposed and current and proposed == current:
                return True
    return False


def _bind_infrastructure_authority(
    *,
    asset_class: str,
    name: str,
    kind: str,
    private: dict[str, str],
    identity_mode: str,
    existing_entity_id: str,
    evidence_refs: list[str],
    evidence_by_ref: Mapping[str, str],
    infrastructure_catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, dict[str, str]]:
    try:
        evidence = "\n".join(evidence_by_ref[ref] for ref in evidence_refs)
    except KeyError as exc:
        raise EnvironmentAssetError(
            "infrastructure asset cites evidence outside this run"
        ) from exc

    identity_names = [name]
    catalog_entity: Mapping[str, Any] | None = None
    if identity_mode == "existing":
        catalog_entity = infrastructure_catalog.get(existing_entity_id)
        if catalog_entity is None:
            raise EnvironmentAssetError(
                "infrastructure asset references an unknown existingEntityId"
            )
        if str(catalog_entity.get("entityType") or "") != asset_class:
            raise EnvironmentAssetError(
                "infrastructure asset type does not match existingEntityId"
            )
        aliases = catalog_entity.get("aliases")
        alias_names = (
            [str(item or "") for item in aliases if str(item or "").strip()]
            if isinstance(aliases, (list, tuple))
            else []
        )
        identity_names = [str(catalog_entity.get("name") or ""), *alias_names]
        # The model proposes state and change prose, not catalog identity.
        name = str(catalog_entity.get("name") or name)
        catalog_kind = str(catalog_entity.get("kind") or "").strip()
        if catalog_kind and catalog_kind != "unknown":
            kind = catalog_kind
    elif _new_identity_collides(
        asset_class=asset_class,
        name=name,
        private=private,
        infrastructure_catalog=infrastructure_catalog,
    ):
        raise EnvironmentAssetError(
            "new infrastructure asset matches an existing catalog entity"
        )

    for field in ("endpoint", "port", "path", "version", "host", "location"):
        proposed = private.get(field, "")
        if not proposed:
            continue
        current = ""
        if catalog_entity is not None:
            current = _catalog_host(catalog_entity) if field == "host" else str(catalog_entity.get(field) or "")
        if proposed != current and not _evidence_contains(proposed, evidence):
            raise EnvironmentAssetError(
                f"infrastructure asset {field} is not supported by cited evidence"
            )
    if not _identity_is_evidenced(identity_names, private, evidence):
        raise EnvironmentAssetError(
            "infrastructure asset identity is not supported by cited evidence"
        )
    return name, kind, private


def _normalize_observation(
    value: Any,
    *,
    business_date: str,
    allowed_refs: set[str] | None,
    allow_legacy_deliverable: bool = False,
) -> dict[str, Any]:
    """Validate Technical's deliberately small discovery-only contract."""

    if not isinstance(value, dict) or set(value) != {
        "assetClass", "name", "summary", "evidenceRefs"
    }:
        raise EnvironmentAssetError("environment observation has an invalid shape")
    asset_class = str(value.get("assetClass") or "")
    allowed_classes = LEGACY_ASSET_CLASSES if allow_legacy_deliverable else ASSET_CLASSES
    if asset_class not in allowed_classes:
        raise EnvironmentAssetError("environment observation has an invalid asset class")
    name = _bounded_text(value.get("name"), required=True, maximum=500)
    summary = _bounded_text(value.get("summary"), required=True)
    evidence_refs = value.get("evidenceRefs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise EnvironmentAssetError("environment observation requires evidenceRefs")
    refs: list[str] = []
    for item in evidence_refs:
        ref = str(item or "")
        if EVIDENCE_REF_RE.fullmatch(ref) is None or ref in refs:
            raise EnvironmentAssetError("environment observation has invalid evidenceRefs")
        if allowed_refs is not None and ref not in allowed_refs:
            raise EnvironmentAssetError("environment observation cites evidence outside this run")
        refs.append(ref)
    for text in (name, summary):
        if _contains_secret(text):
            raise EnvironmentAssetError("environment observation contains a credential-like value")
    identity = "|".join((asset_class, name.casefold(), *sorted(refs)))
    return {
        "assetId": "ENV-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "schema": OBSERVATION_SCHEMA,
        "businessDate": business_date,
        "assetClass": asset_class,
        "name": name,
        "summary": summary,
        "evidenceRefs": refs,
        "privacyClass": "local-private-encrypted",
    }


def _normalize_asset(
    value: Any,
    *,
    business_date: str,
    allowed_refs: set[str] | None,
    evidence_by_ref: Mapping[str, str] | None = None,
    infrastructure_catalog: Mapping[str, Mapping[str, Any]] | None = None,
    enforce_infrastructure_authority: bool = False,
    allow_pending_identity: bool = False,
    allow_legacy_deliverable: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EnvironmentAssetError("environment asset must be an object")
    allowed_keys = {
        "assetClass", "name", "kind", "state", "changeType", "summary",
        "host", "location", "endpoint", "port", "path", "version", "project",
        "evidenceRefs", "confidence", "identityMode", "existingEntityId",
    }
    if set(value) - allowed_keys:
        raise EnvironmentAssetError("environment asset contains unknown fields")
    asset_class = str(value.get("assetClass") or "")
    state = str(value.get("state") or "")
    change_type = str(value.get("changeType") or "")
    confidence = str(value.get("confidence") or "")
    allowed_classes = LEGACY_ASSET_CLASSES if allow_legacy_deliverable else ASSET_CLASSES
    if asset_class not in allowed_classes or state not in ASSET_STATES or change_type not in CHANGE_TYPES:
        raise EnvironmentAssetError("environment asset contains an invalid classification")
    if confidence not in CONFIDENCE_VALUES:
        raise EnvironmentAssetError("environment asset contains an invalid confidence")
    name = _bounded_text(value.get("name"), required=True, maximum=500)
    kind = _bounded_text(value.get("kind"), required=True, maximum=120)
    summary = _bounded_text(value.get("summary"), required=True)
    project = _bounded_text(value.get("project"), maximum=500)
    private = {key: _bounded_text(value.get(key), maximum=MAX_FIELD_CHARS) for key in PRIVATE_FIELDS}
    identity_mode = _bounded_text(value.get("identityMode"), maximum=20)
    existing_entity_id = _bounded_text(value.get("existingEntityId"), maximum=160)
    evidence_refs = value.get("evidenceRefs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise EnvironmentAssetError("environment asset requires evidenceRefs")
    refs = []
    for item in evidence_refs:
        ref = str(item or "")
        if EVIDENCE_REF_RE.fullmatch(ref) is None or ref in refs:
            raise EnvironmentAssetError("environment asset has invalid evidenceRefs")
        if allowed_refs is not None and ref not in allowed_refs:
            raise EnvironmentAssetError("environment asset cites evidence outside this run")
        refs.append(ref)
    for text in (name, kind, summary, project, *private.values()):
        if _contains_secret(text):
            raise EnvironmentAssetError("environment asset contains a credential-like value")
    if asset_class in {"device", "service"}:
        if allow_pending_identity and not identity_mode and not existing_entity_id:
            pass
        elif identity_mode not in INFRASTRUCTURE_IDENTITY_MODES:
            raise EnvironmentAssetError(
                "infrastructure asset requires identityMode existing or new"
            )
        if identity_mode == "existing" and not existing_entity_id:
            raise EnvironmentAssetError(
                "existing infrastructure asset requires existingEntityId"
            )
        if identity_mode == "new" and existing_entity_id:
            raise EnvironmentAssetError(
                "new infrastructure asset must not provide existingEntityId"
            )
        if enforce_infrastructure_authority:
            if evidence_by_ref is None or infrastructure_catalog is None:
                raise EnvironmentAssetError(
                    "infrastructure authority context is unavailable"
                )
            name, kind, private = _bind_infrastructure_authority(
                asset_class=asset_class,
                name=name,
                kind=kind,
                private=private,
                identity_mode=identity_mode,
                existing_entity_id=existing_entity_id,
                evidence_refs=refs,
                evidence_by_ref=evidence_by_ref,
                infrastructure_catalog=infrastructure_catalog,
            )
    elif identity_mode or existing_entity_id:
        raise EnvironmentAssetError(
            "deliverable must not claim infrastructure catalog identity"
        )
    identity = "|".join(
        (
            asset_class,
            existing_entity_id or name.casefold(),
            private["host"].casefold(),
            project.casefold(),
        )
    )
    return {
        "assetId": "ENV-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "schema": LEDGER_SCHEMA,
        "businessDate": business_date,
        "assetClass": asset_class,
        "name": name,
        "kind": kind,
        "state": state,
        "changeType": change_type,
        "summary": summary,
        **private,
        "project": project,
        "identityMode": identity_mode,
        "existingEntityId": existing_entity_id,
        "evidenceRefs": refs,
        "confidence": confidence,
        "privacyClass": "local-private-encrypted",
    }


def _private_asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        key: asset.get(key, "")
        for key in (
            "assetClass", "name", "kind", "state", "changeType", "summary",
            "host", "location", "endpoint", "port", "path", "version", "project",
            "identityMode", "existingEntityId", "evidenceRefs", "confidence",
        )
    }


def _private_observation_payload(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "assetClass": observation.get("assetClass", ""),
        "name": observation.get("name", ""),
        "summary": observation.get("summary", ""),
        "evidenceRefs": observation.get("evidenceRefs", []),
    }


def _append_public_summary(report: str, assets: tuple[dict[str, Any], ...]) -> str:
    counts = {key: sum(1 for item in assets if item["assetClass"] == key) for key in ASSET_CLASSES}
    lines = [report.rstrip(), "", "## 九、环境资产摘要", ""]
    if not assets:
        lines.append("无")
    else:
        lines.append(
            f"待独立复核的基础设施观察：设备 {counts['device']}、常驻运行服务 {counts['service']}。"
        )
        for item in assets:
            public = _public_asset(item)
            lines.append(f"- **{public['name']}**（{public['assetClass']}）：{public['summary']}")
    return "\n".join(lines).rstrip() + "\n"


def _public_asset(asset: dict[str, Any]) -> dict[str, str]:
    return {
        "assetClass": asset["assetClass"],
        "name": _generalize(asset["name"]),
        "kind": _generalize(str(asset.get("kind") or "")),
        "state": str(asset.get("state") or ""),
        "summary": _generalize(asset["summary"]),
    }


def _infrastructure_update(asset: dict[str, Any]) -> dict[str, Any]:
    state = asset["state"]
    status = "retired" if state == "retired" else "active" if state in {"active", "verified", "produced"} else "unknown"
    return {
        "identityMode": asset["identityMode"],
        "entityId": asset["existingEntityId"],
        "entityType": asset["assetClass"],
        "name": _generalize(asset["name"]),
        "kind": _generalize(asset["kind"]),
        "status": status,
        "host": _generalize(asset["host"]),
        "location": _generalize(asset["location"]),
        "endpoint": "<private-network-endpoint>" if asset["endpoint"] else "",
        "port": "<private-port>" if asset["port"] else "",
        "path": "<private-path>" if asset["path"] else "",
        "eventType": asset["changeType"],
        "change": _generalize(asset["summary"]),
        "evidence": list(asset["evidenceRefs"]),
        "confidence": asset["confidence"],
    }


def _dek_ref() -> SecretRef:
    return SecretRef(
        backend=default_secret_backend(),
        service="actanara",
        account="environment-assets-dek-v1",
    )


def _ensure_dek(paths: RuntimePaths) -> bytes:
    ref = _dek_ref()
    encoded = read_secret(ref, runtime_home=paths.home)
    if not encoded:
        encoded = _b64(secrets.token_bytes(32))
        store_secret(ref, encoded, runtime_home=paths.home)
    key = _decode_key(encoded)
    if len(key) != 32:
        raise EnvironmentAssetError("environment ledger encryption key is invalid")
    return key


def _read_dek(paths: RuntimePaths) -> bytes | None:
    encoded = read_secret(_dek_ref(), runtime_home=paths.home)
    if not encoded:
        return None
    try:
        key = _decode_key(encoded)
    except Exception:
        return None
    return key if len(key) == 32 else None


def _write_encrypted_payload(
    paths: RuntimePaths,
    *,
    business_date: str,
    payload_schema: str,
    envelope_schema: str,
    filename: str,
    payload: Mapping[str, Any],
) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date):
        raise EnvironmentAssetError("environment ledger date is invalid")
    if filename not in {
        f"environment-assets-v1-{business_date}.json",
        f"environment-observations-v1-{business_date}.json",
        f"environment-observations-v2-{business_date}.json",
        f"environment-reconciliation-audit-v1-{business_date}.json",
    }:
        raise EnvironmentAssetError("environment ledger filename is invalid")
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_PRIVATE_LEDGER_BYTES:
        raise EnvironmentAssetError("environment ledger exceeds its size limit")

    root, root_fd = _secure_root(paths, create=True)
    if root_fd < 0:  # pragma: no cover - create=True guarantees the directory
        raise EnvironmentAssetError("environment ledger directory is unavailable")
    lock_fd = -1
    temporary = f".{filename}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        lock_fd = _open_key_lock(root_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        key = _ensure_dek(paths)
        nonce = secrets.token_bytes(12)
        ciphertext = _aesgcm()(key).encrypt(
            nonce,
            encoded,
            _aad(payload_schema, business_date),
        )
        envelope = json.dumps(
            {
                "schema": envelope_schema,
                "businessDate": business_date,
                "algorithm": "AES-256-GCM",
                "nonce": _b64(nonce),
                "ciphertext": _b64(ciphertext),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, envelope)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.rename(temporary, filename, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except OSError:
            pass
        raise
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        os.close(root_fd)
    return root / filename


def _read_encrypted_payload(
    paths: RuntimePaths,
    *,
    business_date: str,
    payload_schema: str,
    envelope_schema: str,
    filename: str,
) -> dict[str, Any] | None:
    try:
        _root, root_fd = _secure_root(paths, create=False)
    except EnvironmentAssetError:
        return None
    if root_fd < 0:
        return None
    try:
        raw = _read_private_file(root_fd, filename)
    finally:
        os.close(root_fd)
    if raw is None:
        return None
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema", "businessDate", "algorithm", "nonce", "ciphertext"
        }:
            return None
        if (
            envelope.get("schema") != envelope_schema
            or envelope.get("businessDate") != business_date
            or envelope.get("algorithm") != "AES-256-GCM"
        ):
            return None
        nonce = _unb64(envelope.get("nonce"))
        ciphertext = _unb64(envelope.get("ciphertext"))
        if len(nonce) != 12 or len(ciphertext) > MAX_PRIVATE_LEDGER_BYTES + 32:
            return None
        key = _read_dek(paths)
        if key is None:
            return None
        plaintext = _aesgcm()(key).decrypt(
            nonce,
            ciphertext,
            _aad(payload_schema, business_date),
        )
        if len(plaintext) > MAX_PRIVATE_LEDGER_BYTES:
            return None
        payload = json.loads(plaintext, object_pairs_hook=_unique_object)
    except (EnvironmentAssetError, UnicodeDecodeError, ValueError, TypeError):
        return None
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != payload_schema or payload.get("businessDate") != business_date:
        return None
    return payload


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise EnvironmentAssetError("cryptography is required for private environment ledgers") from exc
    return AESGCM


def _secure_root(paths: RuntimePaths, *, create: bool) -> tuple[Path, int]:
    root = paths.home / "artifacts" / "environment"
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return root, -1
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise EnvironmentAssetError("environment ledger directory is unsafe")
    descriptor = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(descriptor)
        raise EnvironmentAssetError("environment ledger directory changed during validation")
    return root, descriptor


def _read_private_file(root_fd: int, filename: str) -> bytes | None:
    try:
        descriptor = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=root_fd)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > MAX_PRIVATE_LEDGER_BYTES * 2
        ):
            return None
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 131_072))
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            return None
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            return None
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _open_key_lock(root_fd: int) -> int:
    name = ".environment-key.lock"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, os.O_RDWR | nofollow, dir_fd=root_fd)
    except FileNotFoundError:
        try:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=root_fd,
            )
            os.fchmod(descriptor, 0o600)
            return descriptor
        except FileExistsError:
            return os.open(name, os.O_RDWR | nofollow, dir_fd=root_fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bounded_text(value: Any, *, required: bool = False, maximum: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise EnvironmentAssetError("environment asset text fields must be strings")
    if (required and not text) or len(text) > maximum or "\x00" in text:
        raise EnvironmentAssetError("environment asset text field is invalid")
    return text


def _contains_secret(value: str) -> bool:
    return bool(SECRET_RE.search(value) or BEARER_RE.search(value) or PRIVATE_KEY_RE.search(value))


def _sanitize_private_evidence(value: str) -> str:
    text = PRIVATE_KEY_BLOCK_RE.sub("<redacted-private-key>", str(value or ""))
    text = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = text.strip()
    if not text or len(text) > MAX_FIELD_CHARS:
        raise EnvironmentAssetError("environment observation evidence is invalid")
    return text


def _generalize(value: str) -> str:
    text = PRIVATE_KEY_BLOCK_RE.sub("<redacted-private-key>", str(value or ""))
    text = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = URL_RE.sub("<network-endpoint>", text)
    text = EMAIL_RE.sub("<private-email>", text)
    text = IPV6_RE.sub("<network-address>", text)
    text = IPV4_RE.sub("<network-address>", text)
    text = FQDN_RE.sub(_redact_fqdn, text)
    text = ABSOLUTE_PATH_RE.sub("<local-path>", text)
    return text


def _redact_fqdn(match: re.Match[str]) -> str:
    value = match.group(0)
    suffix = value.rsplit(".", 1)[-1].casefold()
    return value if suffix in FILELIKE_SUFFIXES else "<network-host>"


def _aad(payload_schema: str, business_date: str) -> bytes:
    return f"{payload_schema}|{business_date}".encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid base64")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _decode_key(value: str) -> bytes:
    return _unb64(value)


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


__all__ = [
    "bind_environment_asset_authority",
    "EnvironmentAssetError",
    "EnvironmentMemoryRecord",
    "EnvironmentObservationLedger",
    "TechnicalEnvironmentResult",
    "apply_environment_asset_projection",
    "collect_environment_memory_records",
    "environment_asset_ledger_ready",
    "environment_observation_ledger_ready",
    "parse_technical_environment_output",
    "read_environment_asset_ledger",
    "read_environment_observation_ledger",
    "read_environment_reconciliation_audit",
    "write_environment_asset_ledger",
    "write_environment_observation_ledger",
    "write_environment_reconciliation_audit",
]
