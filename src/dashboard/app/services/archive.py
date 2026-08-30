"""Read-only data facade for the Living Archive Dashboard.

This module deliberately projects private Runtime authorities into a small,
path-free public contract.  It never writes to the Skill Pass ledger and never
returns evidence locators, evidence records, full Skill markdown, or local
filesystem locations.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from data_foundation.db import connect
from data_foundation.paths import RuntimePaths, load_paths
from data_foundation.snapshots import read_dashboard_snapshot
from data_foundation.time import resolve_timezone

from . import skill_assets


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "actanara.archive.v1"
SUPPORTED_PROMPT_VERSIONS = frozenset({21, 22})
ASSET_TYPES = frozenset({"experience", "practice", "skill"})
ASSET_STATUSES = frozenset({"documented", "verified", "draft"})
_ASSET_ID_RE = re.compile(r"^(?:experience|practice|skill)-[0-9a-f]{24}$")
_COMMON_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])(?:file://)?(?:~|/(?:Users|home|Volumes|private|var|tmp|opt|etc|root))"
    r"(?:[/\\][^\s,;，；。)\]}>'\"]+)*",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\(?:[^\s,;，；。)\]}>'\"]+\\?)*")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|private[_-]?key|"
    r"authorization|cookie)(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_STALE_AFTER_SECONDS = 48 * 60 * 60
_MAX_PUBLIC_TEXT = 1_200
_MAX_PAGE_SIZE = 200


class ArchiveQueryError(ValueError):
    """Raised for an invalid public Archive query."""


class ArchiveAssetNotFound(LookupError):
    """Raised when a stable public asset identifier does not exist."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _archive_today() -> date:
    return datetime.now(resolve_timezone()).date()


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _public_text(value: Any, *, paths: RuntimePaths | None = None, maximum: int = _MAX_PUBLIC_TEXT) -> str:
    """Return compact asset prose while removing recognizable local paths."""
    text = str(value or "").replace("\x00", " ").strip()
    if paths is not None:
        private_values = (
            paths.home,
            paths.db_path,
            paths.diary_dir,
            paths.reports_dir,
            paths.archives_dir,
            paths.state_dir,
        )
        for private_path in sorted(
            {str(item) for item in private_values if item}, key=len, reverse=True
        ):
            if private_path:
                text = text.replace(private_path, "[local path]")
    text = _COMMON_LOCAL_PATH_RE.sub("[local path]", text)
    text = _WINDOWS_PATH_RE.sub("[local path]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > maximum:
        text = text[: maximum - 1].rstrip() + "…"
    return text


def _stable_id(asset_type: str, business_date: str, prompt_version: str, review_id: str) -> str:
    identity = "\x1f".join((SCHEMA_VERSION, asset_type, business_date, prompt_version, review_id))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{asset_type}-{digest}"


def _run_id(business_date: str, prompt_version: str) -> str:
    identity = "\x1f".join((SCHEMA_VERSION, "run", business_date, prompt_version))
    return "skill-pass-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness(generated_at: str | None) -> dict[str, Any]:
    parsed = _parse_timestamp(generated_at)
    if parsed is None:
        return {
            "status": "unsupported",
            "generatedAt": None,
            "ageSeconds": None,
            "stale": None,
        }
    age = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    stale = age > _STALE_AFTER_SECONDS
    return {
        "status": "stale" if stale else "ready",
        "generatedAt": parsed.isoformat(),
        "ageSeconds": age,
        "stale": stale,
    }


def _ledger_generated_at(paths: RuntimePaths, filename: str) -> str | None:
    """Read only an already-validated ledger's timestamp; never return its path."""
    try:
        metadata = (skill_assets._ledger_directory(paths) / filename).lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            return None
        return datetime.fromtimestamp(metadata.st_mtime, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _source_contract(prompt_version: str) -> dict[str, Any]:
    version = int(prompt_version.rsplit("v", 1)[1])
    return {
        "kind": "skill-pass",
        "version": version,
        "promptVersion": prompt_version,
        "readOnly": True,
        "historical": version < max(SUPPORTED_PROMPT_VERSIONS),
    }


def _project_ledger_row(
    row: dict[str, Any],
    *,
    business_date: str,
    prompt_version: str,
    paths: RuntimePaths,
) -> list[dict[str, Any]]:
    """Project one adjudicated row into the Experience -> Practice -> Skill model."""
    asset_class = row.get("assetClass")
    completion_verified = row.get("completion") == "verified"
    practice_eligible = asset_class == "skill" or (
        row.get("originalDecision") == "skill" and completion_verified
    )
    # Reference/Discard remain audit dispositions, never public asset types.
    # A verified Skill decision that later became covered/conflicting/rejected
    # still contains an observed Practice instance, however, so preserve the
    # underlying Experience -> Practice chain without promoting a Skill draft.
    if asset_class not in {"lesson", "skill"} and not practice_eligible:
        return []

    review_id = str(row["reviewId"])
    source = _source_contract(prompt_version)
    experience_id = _stable_id("experience", business_date, prompt_version, review_id)
    practice_id = _stable_id("practice", business_date, prompt_version, review_id)
    skill_id = _stable_id("skill", business_date, prompt_version, review_id)
    title = _public_text(row.get("title"), paths=paths, maximum=500) or "Untitled experience"
    summary = _public_text(row.get("summary") or row.get("reason"), paths=paths)
    evidence_count = _nonnegative_int(row.get("evidenceCount")) or 0
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    safe_scores = {
        key: score
        for key in ("evidence", "value", "reuse", "program")
        if (score := _nonnegative_int(scores.get(key))) is not None
    }
    common = {
        "businessDate": business_date,
        "promptVersion": prompt_version,
        "source": source,
        "metrics": {"evidenceCount": evidence_count, "scores": safe_scores},
    }
    items: list[dict[str, Any]] = [
        {
            "id": experience_id,
            "type": "experience",
            "title": title,
            "summary": summary,
            "status": "verified" if practice_eligible else "documented",
            **common,
            "relations": {
                "validatedBy": [practice_id] if practice_eligible else [],
                "derivedFrom": [],
            },
        }
    ]
    if not practice_eligible:
        return items

    practice_summary = _public_text(
        row.get("completionReason") or row.get("summary") or row.get("reason"),
        paths=paths,
    )
    items.append(
        {
            "id": practice_id,
            "type": "practice",
            "title": title,
            "summary": practice_summary,
            "status": "verified",
            **common,
            "metrics": {
                **common["metrics"],
                "actionVerified": practice_eligible,
                "resultObserved": practice_eligible,
            },
            "relations": {"derivedFrom": [experience_id], "validates": [experience_id]},
        }
    )
    if asset_class != "skill" or row.get("libraryAction") not in {"create", "extend"}:
        return items

    skill_title = _public_text(row.get("skillName") or title, paths=paths, maximum=160)
    skill_summary = _public_text(row.get("skillDescription") or summary, paths=paths)
    items.append(
        {
            "id": skill_id,
            "type": "skill",
            "title": skill_title,
            "summary": skill_summary,
            "status": "draft",
            **common,
            "metrics": {
                **common["metrics"],
                "libraryAction": row.get("libraryAction"),
                "registrationRequested": False,
            },
            "relations": {"derivedFrom": [practice_id], "validatedBy": [practice_id]},
        }
    )
    return items


def _read_skill_pass_runs(paths: RuntimePaths) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    runs: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        candidates = skill_assets._ledger_candidates(paths, None)
    except Exception:
        logger.warning("Archive Skill Pass ledger discovery failed")
        return [], [], [{"source": "skill-pass-ledger", "code": "source-read-failed", "retryable": True}]

    selected = [item for item in candidates if item[1] in SUPPORTED_PROMPT_VERSIONS]
    for business_date, version, prompt_version, filename in selected:
        generated_at = _ledger_generated_at(paths, filename)
        run = {
            "id": _run_id(business_date, prompt_version),
            "businessDate": business_date,
            "promptVersion": prompt_version,
            "version": version,
            "readOnly": True,
            "historical": version < max(SUPPORTED_PROMPT_VERSIONS),
            "generatedAt": generated_at,
            "freshness": _freshness(generated_at),
        }
        try:
            raw = skill_assets._read_ledger_bytes(skill_assets._ledger_directory(paths), filename)
            private_rows = skill_assets._decode_private_rows(raw)
            public_rows: list[dict[str, Any]] = []
            class_counts: Counter[str] = Counter()
            seen: set[str] = set()
            for private_row in private_rows:
                projected_row = skill_assets._project_row(
                    private_row,
                    business_date=business_date,
                    prompt_version=prompt_version,
                )
                review_id = str(projected_row["reviewId"])
                if review_id in seen:
                    raise skill_assets.SkillAssetLedgerError("duplicate review identifier")
                seen.add(review_id)
                class_counts[str(projected_row["assetClass"])] += 1
                public_rows.extend(
                    _project_ledger_row(
                        projected_row,
                        business_date=business_date,
                        prompt_version=prompt_version,
                        paths=paths,
                    )
                )
            product_counts = Counter(item["type"] for item in public_rows)
            run.update(
                {
                    "status": "empty" if not private_rows else "ready",
                    "counts": {
                        "experience": product_counts.get("experience", 0),
                        "practice": product_counts.get("practice", 0),
                        "skill": product_counts.get("skill", 0),
                        "reference": class_counts.get("reference", 0),
                        "discard": class_counts.get("discard", 0),
                    },
                }
            )
            runs.append(run)
            assets.extend(public_rows)
        except Exception:
            logger.warning(
                "Archive Skill Pass ledger projection failed for %s/%s",
                business_date,
                prompt_version,
            )
            run.update(
                {
                    "status": "degraded",
                    "counts": {
                        "experience": None,
                        "practice": None,
                        "skill": None,
                        "reference": None,
                        "discard": None,
                    },
                }
            )
            runs.append(run)
            errors.append(
                {
                    "source": "skill-pass-ledger",
                    "code": "run-read-failed",
                    "retryable": True,
                    "runId": run["id"],
                }
            )
    return runs, assets, errors


def _asset_counts(items: list[dict[str, Any]], *, authoritative: bool) -> dict[str, int | None]:
    if not authoritative:
        return {"all": None, "experience": None, "practice": None, "skill": None}
    counts = Counter(str(item.get("type")) for item in items)
    return {
        "all": len(items),
        "experience": counts.get("experience", 0),
        "practice": counts.get("practice", 0),
        "skill": counts.get("skill", 0),
    }


def _response_status(*, has_data: bool, errors: list[dict[str, Any]], stale: bool = False) -> str:
    if errors:
        return "degraded"
    if stale:
        return "stale"
    return "ready" if has_data else "empty"


def get_assets(
    *,
    asset_type: str | None = None,
    status: str | None = None,
    business_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    if asset_type is not None and asset_type not in ASSET_TYPES:
        raise ArchiveQueryError("type must be experience, practice, or skill")
    if status is not None and status not in ASSET_STATUSES:
        raise ArchiveQueryError("status is not supported")
    if business_date is not None:
        try:
            date.fromisoformat(business_date)
        except (TypeError, ValueError) as exc:
            raise ArchiveQueryError("businessDate must be YYYY-MM-DD") from exc
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ArchiveQueryError(f"limit must be 1..{_MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ArchiveQueryError("offset must be non-negative")

    selected_paths = paths or load_paths()
    runs, all_items, errors = _read_skill_pass_runs(selected_paths)
    items = [
        item
        for item in all_items
        if (asset_type is None or item["type"] == asset_type)
        and (status is None or item["status"] == status)
        and (business_date is None or item["businessDate"] == business_date)
    ]
    items.sort(key=lambda item: (item["businessDate"], item["promptVersion"], item["type"], item["id"]), reverse=True)
    total = len(items)
    page_items = items[offset : offset + limit]
    any_successful_run = any(run.get("status") in {"ready", "empty"} for run in runs)
    counts = _asset_counts(items, authoritative=any_successful_run or not errors)
    stale = bool(runs) and all(run.get("freshness", {}).get("stale") is True for run in runs if run.get("status") != "degraded")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": _response_status(has_data=bool(items), errors=errors, stale=stale),
        "generatedAt": _now_iso(),
        "dataFreshness": _freshness(max((run.get("generatedAt") or "" for run in runs), default="") or None),
        "counts": counts,
        "items": page_items,
        "page": {
            "limit": limit,
            "offset": offset,
            "total": total if any_successful_run or not errors else None,
            "hasMore": offset + len(page_items) < total,
        },
        "sourceErrors": errors,
    }


def get_asset(asset_id: str, *, paths: RuntimePaths | None = None) -> dict[str, Any]:
    if not isinstance(asset_id, str) or _ASSET_ID_RE.fullmatch(asset_id) is None:
        raise ArchiveAssetNotFound("Archive asset was not found")
    selected_paths = paths or load_paths()
    runs, all_items, errors = _read_skill_pass_runs(selected_paths)
    item = next((candidate for candidate in all_items if candidate["id"] == asset_id), None)
    if item is None:
        raise ArchiveAssetNotFound("Archive asset was not found")
    latest_generated_at = max((run.get("generatedAt") or "" for run in runs), default="") or None
    freshness = _freshness(latest_generated_at)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": _response_status(
            has_data=True,
            errors=errors,
            stale=freshness.get("stale") is True,
        ),
        "generatedAt": _now_iso(),
        "dataFreshness": freshness,
        "item": item,
        "sourceErrors": errors,
    }


def get_skill_pass_runs(
    *,
    limit: int = 50,
    offset: int = 0,
    paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_PAGE_SIZE:
        raise ArchiveQueryError(f"limit must be 1..{_MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ArchiveQueryError("offset must be non-negative")
    selected_paths = paths or load_paths()
    runs, _assets, errors = _read_skill_pass_runs(selected_paths)
    total = len(runs)
    page_items = runs[offset : offset + limit]
    stale = bool(runs) and all(run.get("freshness", {}).get("stale") is True for run in runs if run.get("status") != "degraded")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": _response_status(has_data=bool(runs), errors=errors, stale=stale),
        "generatedAt": _now_iso(),
        "items": page_items,
        "page": {"limit": limit, "offset": offset, "total": total, "hasMore": offset + len(page_items) < total},
        "sourceErrors": errors,
    }


def _foundation_projection(paths: RuntimePaths) -> dict[str, Any]:
    try:
        snapshot = read_dashboard_snapshot(paths)
    except Exception:
        logger.warning("Archive Foundation snapshot read failed")
        return {
            "status": "degraded",
            "payload": None,
            "freshness": _freshness(None),
            "sourceErrors": [{"source": "foundation-snapshot", "code": "source-read-failed", "retryable": True}],
        }
    if snapshot is None:
        return {"status": "empty", "payload": None, "freshness": _freshness(None), "sourceErrors": []}
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else None
    if not isinstance(payload, dict):
        return {
            "status": "degraded",
            "payload": None,
            "freshness": _freshness(None),
            "sourceErrors": [{"source": "foundation-snapshot", "code": "invalid-projection", "retryable": True}],
        }
    freshness = _freshness(snapshot.get("generatedAt"))
    if freshness["stale"] is None:
        return {
            "status": "degraded",
            "payload": payload,
            "freshness": freshness,
            "sourceErrors": [
                {
                    "source": "foundation-snapshot",
                    "code": "freshness-unavailable",
                    "retryable": True,
                }
            ],
        }
    return {
        "status": "stale" if freshness["stale"] else "ready",
        "payload": payload,
        "freshness": freshness,
        "sourceErrors": [],
    }


def _list_count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _foundation_report_inventory(paths: RuntimePaths) -> dict[str, Any]:
    """Count ready Foundation report documents without reading their bodies."""
    try:
        with connect(paths, read_only=True) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM diary_markdown_documents WHERE status = 'ready'"
            ).fetchone()
        count = _nonnegative_int(row[0] if row is not None else None)
        if count is None:
            raise ValueError("invalid report inventory count")
    except Exception:
        logger.warning("Archive Foundation report inventory read failed")
        return {
            "status": "degraded",
            "count": None,
            "unit": "documents",
            "sourceErrors": [
                {
                    "source": "foundation-report-inventory",
                    "code": "source-read-failed",
                    "retryable": True,
                }
            ],
        }
    return {
        "status": "empty" if count == 0 else "ready",
        "count": count,
        "unit": "documents",
        "sourceErrors": [],
    }


def _inventory(
    payload: dict[str, Any] | None,
    *,
    reports: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if payload is None:
        inventory = {
            name: {"status": "unsupported", "count": None}
            for name in ("diary", "memory", "reports", "infrastructure", "skills", "agents")
        }
        inventory["reports"] = {
            key: value for key, value in reports.items() if key != "sourceErrors"
        }
        return inventory
    diary = payload.get("diary") if isinstance(payload.get("diary"), dict) else None
    memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
    skills = payload.get("skills") if isinstance(payload.get("skills"), dict) else None
    infrastructure = payload.get("infrastructure") if isinstance(payload.get("infrastructure"), dict) else None
    agents = payload.get("agents") if isinstance(payload.get("agents"), list) else None
    diary_count = _nonnegative_int(diary.get("count")) if diary is not None else None
    memory_count = _nonnegative_int(memory.get("sessionFiles")) if memory is not None else None
    skill_count = _nonnegative_int(skills.get("total")) if skills is not None else None
    device_count = _list_count(infrastructure.get("devices")) if infrastructure is not None else None
    service_count = _list_count(infrastructure.get("services")) if infrastructure is not None else None
    infrastructure_count = (
        (device_count or 0) + (service_count or 0)
        if device_count is not None or service_count is not None
        else None
    )
    identity_count = _nonnegative_int(payload.get("agentCount"))
    if identity_count is None and agents is not None:
        identity_count = len(agents)
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else None
    source_count = len(tools) if tools is not None else None

    def item(count: int | None, *, supported: bool) -> dict[str, Any]:
        if not supported or count is None:
            return {"status": "unsupported", "count": None}
        return {"status": "empty" if count == 0 else "ready", "count": count}

    diary_item = {**item(diary_count, supported=diary is not None), "unit": "days"}
    memory_item = {**item(memory_count, supported=memory is not None), "unit": "files"}
    infrastructure_item = {
        **item(infrastructure_count, supported=infrastructure is not None),
        "unit": "entities",
        "devices": device_count,
        "services": service_count,
    }
    skills_item = {
        **item(skill_count, supported=skills is not None),
        "unit": "installedInstances",
    }
    agents_item = {
        **item(source_count, supported=tools is not None),
        "unit": "runtimeSources",
        "identityCount": identity_count,
    }
    return {
        "diary": diary_item,
        "memory": memory_item,
        "reports": {key: value for key, value in reports.items() if key != "sourceErrors"},
        "infrastructure": infrastructure_item,
        "skills": skills_item,
        "agents": agents_item,
    }


def _safe_agents(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if payload is None:
        return []
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return []
    result = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        name = _public_text(tool.get("name"), maximum=100)
        if not name:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"source-{index + 1}"
        result.append(
            {
                "id": slug[:80],
                "name": name,
                "status": _public_text(tool.get("usageStatus") or "available", maximum=40),
                "sessionCount": _nonnegative_int(tool.get("sessionCount")),
                "messageCount": _nonnegative_int(tool.get("allTimeMessages")),
                "tokenCount": _nonnegative_int(tool.get("allTimeTokens")),
                "lastActive": _public_text(tool.get("lastActivity"), maximum=80) or None,
            }
        )
    return result


def _canonical_skill_inventory(paths: RuntimePaths) -> dict[str, Any]:
    """Count Actanara's canonical Skills, not cross-tool installed copies."""
    root = paths.home / "assets" / "skills"
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return {"status": "empty", "count": 0, "sourceErrors": []}
    except OSError:
        metadata = None
    if (
        metadata is None
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or not os.access(root, os.R_OK | os.X_OK)
    ):
        return {
            "status": "degraded",
            "count": None,
            "sourceErrors": [
                {
                    "source": "canonical-skill-library",
                    "code": "unsafe-or-unavailable-root",
                    "retryable": False,
                }
            ],
        }
    try:
        from diary_generator.skill_pass_minimal_harness import load_existing_skill_library

        count = len(load_existing_skill_library(paths))
    except Exception:
        logger.warning("Archive canonical Skill inventory read failed")
        return {
            "status": "degraded",
            "count": None,
            "sourceErrors": [
                {
                    "source": "canonical-skill-library",
                    "code": "source-read-failed",
                    "retryable": True,
                }
            ],
        }
    return {"status": "empty" if count == 0 else "ready", "count": count, "sourceErrors": []}


_ARCHIVE_QUOTES = (
    ("把今天的工作，变成明天可以复用的能力。", "Actanara · 每日摘录"),
    ("可靠的系统不仅保存结果，也记录结果为何可信。", "Actanara · 每日摘录"),
    ("经验只有经过验证，才能成为可复用的做法。", "Actanara · 每日摘录"),
    ("明确边界，系统才能持续演进。", "Actanara · 每日摘录"),
)


def _daily_quote(items: list[dict[str, Any]], *, quote_date: date | None = None) -> dict[str, Any]:
    """Select a stable daily excerpt from safe assets, with a curated fallback."""
    selected_date = quote_date or _archive_today()
    technical_quote_marker = re.compile(
        r"(?i)(?:`|\b(?:action|result|record|job|status|sha256|jsonl)\b|"
        r"\b\d{5,}\b|[_/]|[A-Za-z]{4,}|\[local path\]|本地路径|记录编号|状态字段)"
    )
    preferred = sorted(
        (
            item
            for item in items
            if item.get("type") in {"practice", "experience"}
            and 12 <= len(_public_text(item.get("summary"), maximum=220)) <= 100
            and technical_quote_marker.search(
                _public_text(item.get("summary"), maximum=220)
            ) is None
        ),
        key=lambda item: (
            0 if item.get("type") == "experience" else 1,
            str(item.get("businessDate") or ""),
            str(item.get("id") or ""),
        ),
    )
    seed = int(hashlib.sha256(selected_date.isoformat().encode("utf-8")).hexdigest(), 16)
    if preferred:
        item = preferred[seed % len(preferred)]
        full_text = _public_text(item.get("summary"), maximum=300)
        sentence = re.split(r"(?<=[。！？.!?])\s*", full_text, maxsplit=1)[0].strip()
        excerpt = sentence if len(sentence) >= 12 else full_text
        excerpt = _public_text(excerpt, maximum=180)
        source_label = {
            "experience": "Skill Pass · 经验",
            "practice": "Skill Pass · 实践",
        }.get(str(item.get("type")), "Skill Pass")
        return {
            "text": excerpt,
            "source": source_label,
            "date": selected_date.isoformat(),
            "assetId": item.get("id"),
        }
    text, source = _ARCHIVE_QUOTES[seed % len(_ARCHIVE_QUOTES)]
    return {
        "text": text,
        "source": source,
        "date": selected_date.isoformat(),
        "assetId": None,
    }


def get_activity(*, paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected_paths = paths or load_paths()
    foundation = _foundation_projection(selected_paths)
    payload = foundation["payload"]
    if payload is None:
        totals = {"tokens": None, "messages": None, "sessions": None, "activeDays": None}
        trend: list[dict[str, Any]] = []
        models: list[dict[str, Any]] = []
    else:
        totals = {
            "tokens": _nonnegative_int(payload.get("totalTokens")),
            "messages": _nonnegative_int(payload.get("totalMessages")),
            "sessions": _nonnegative_int(payload.get("totalSessions")),
            "activeDays": _nonnegative_int(payload.get("activeDayCount")),
        }
        trend = []
        for row in payload.get("trend30d", []) if isinstance(payload.get("trend30d"), list) else []:
            if not isinstance(row, dict):
                continue
            slots = row.get("slots") if isinstance(row.get("slots"), dict) else {}
            trend.append(
                {
                    "date": _public_text(row.get("date"), maximum=16),
                    "slots": {
                        _public_text(key, maximum=24): count
                        for key, value in slots.items()
                        if (count := _nonnegative_int(value)) is not None
                    },
                }
            )
        models = []
        for model in payload.get("models", []) if isinstance(payload.get("models"), list) else []:
            if not isinstance(model, dict):
                continue
            models.append(
                {
                    "name": _public_text(model.get("name"), maximum=120),
                    "tokens": _nonnegative_int(model.get("tokens")),
                    "messages": _nonnegative_int(model.get("messages")),
                    "sessions": _nonnegative_int(model.get("sessions")),
                }
            )
    source_errors = list(foundation["sourceErrors"])
    activity_status = foundation["status"]
    available_totals = [value is not None for value in totals.values()]
    if payload is not None and not any(available_totals) and not source_errors:
        activity_status = "unsupported"
    elif payload is not None and not all(available_totals):
        activity_status = "degraded"
        source_errors.append(
            {
                "source": "foundation-usage-projection",
                "code": "partial-projection",
                "retryable": True,
            }
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": activity_status,
        "generatedAt": _now_iso(),
        "dataFreshness": foundation["freshness"],
        "totals": totals,
        "trend30d": trend,
        "models": models,
        "agents": _safe_agents(payload),
        "sourceErrors": source_errors,
    }


def get_bootstrap(*, paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected_paths = paths or load_paths()
    assets = get_assets(limit=_MAX_PAGE_SIZE, paths=selected_paths)
    foundation = _foundation_projection(selected_paths)
    payload = foundation["payload"]
    canonical_skills = _canonical_skill_inventory(selected_paths)
    reports = _foundation_report_inventory(selected_paths)
    errors = [
        *assets["sourceErrors"],
        *foundation["sourceErrors"],
        *canonical_skills["sourceErrors"],
        *reports["sourceErrors"],
    ]
    statuses = {
        assets["status"],
        foundation["status"],
        canonical_skills["status"],
        reports["status"],
    }
    if errors:
        status = "degraded"
    elif "stale" in statuses:
        status = "stale"
    elif "ready" in statuses:
        status = "ready"
    else:
        status = "empty"
    counts = assets["counts"]
    inventory = _inventory(payload, reports=reports)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "generatedAt": _now_iso(),
        "dataFreshness": {
            "skillPass": assets["dataFreshness"],
            "foundation": foundation["freshness"],
        },
        "summary": {
            "assetCount": counts.get("all"),
            "experienceCount": counts.get("experience"),
            "practiceCount": counts.get("practice"),
            "skillCount": counts.get("skill"),
            "draftSkillCount": counts.get("skill"),
            "activeSkillCount": canonical_skills["count"],
            "installedSkillInstanceCount": inventory["skills"]["count"],
        },
        "inventory": inventory,
        "agents": _safe_agents(payload),
        "dailyQuote": _daily_quote(assets["items"]),
        "sourceErrors": errors,
    }
