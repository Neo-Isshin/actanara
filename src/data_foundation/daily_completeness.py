"""Shared day-level completeness contract for history backfill and Daily QA."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from agentic_rag.rag_settings import is_rag_product_enabled, rag_product_disabled_reason

from .aggregate import daily_diary_usage_metrics
from .db import connect
from .diary_markdown import read_diary_markdown_documents
from .diary_paths import diary_report_paths
from .paths import RuntimePaths
from .settings import ensure_settings, is_nova_task_enabled
from .snapshots import read_rag_daily_status_snapshot
from .time import resolve_timezone

REQUIRED_DIARY_REPORTS = ("narrative", "technical", "learning")


def evaluate_daily_completeness(paths: RuntimePaths, business_date: date, *, documents: list[dict] | None = None) -> dict[str, Any]:
    """Evaluate the canonical daily readiness contract.

    A blank/no-activity day is complete when the no-activity marker is materialized;
    otherwise narrative, technical, learning, SQLite materialization, RAG sync, and
    enabled Nova-Task projection/evidence must all be present.
    """
    if documents is not None:
        docs = list(documents)
    else:
        try:
            docs = read_diary_markdown_documents(paths, business_date, business_date)
        except Exception:
            docs = []
    report_paths = _report_paths(paths, business_date)
    no_activity = _has_no_activity_marker(docs, report_paths)
    docs_ready = {report_type: bool(report_paths.get(report_type)) or _doc_present(docs, report_type) for report_type in REQUIRED_DIARY_REPORTS}
    materialized = bool(docs) and (_has_no_activity_doc(docs) if no_activity else all(_doc_present(docs, item) for item in REQUIRED_DIARY_REPORTS))
    foundation_materialized = _foundation_materialized(paths, business_date)
    sqlite_ready = bool(materialized and foundation_materialized)
    rag_required = _rag_required(paths)
    rag_disabled_reason = None if rag_required else _rag_disabled_reason(paths)
    rag_ready = _rag_sync_ready(paths, business_date) if rag_required else False
    nova_task_enabled = _nova_task_enabled(paths)
    nova_task_required = bool(nova_task_enabled and not no_activity)
    task_updated = False if no_activity else _nova_task_ready(paths, business_date)
    missing: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if not nova_task_required:
        reason = (
            "Blank/no-activity days do not require Nova-Task."
            if no_activity
            else "Nova-Task is disabled by features.novaTask."
        )
        skipped.append(_skipped("nova-task", "Nova-Task work graph/export", reason))
    if no_activity:
        if not _has_no_activity_doc(docs) and not any(path.name.endswith("-no-activity.md") for path in report_paths.get("narrative", [])):
            missing.append(_missing("blankday", "blankday/no-activity marker", 1, "daily-full"))
    else:
        for report_type, ready in docs_ready.items():
            if not ready:
                action = "daily-full" if report_type == "narrative" else f"{report_type}-pass"
                missing.append(_missing(f"diary-{report_type}", f"{report_type} diary", 1, action))
        if not sqlite_ready:
            missing.append(_missing("sqlite-materialization", "SQLite materialization", 0, "daily-materialization"))
        if rag_required and not rag_ready:
            missing.append(_missing("rag-sync", "RAG sync", 0, "rag-sync"))
        elif not rag_required:
            skipped.append(_skipped("rag-sync", "RAG sync", rag_disabled_reason or "nova-RAG is disabled or unavailable."))
        if nova_task_required and not task_updated:
            missing.append(_missing("nova-task", "Nova-Task work graph/export", 0, "nova-task-work-graph"))
    existing_items = _existing_items(docs, report_paths, sqlite_ready, rag_ready, task_updated, no_activity)
    ready = not missing
    return {
        "businessDate": business_date.isoformat(),
        "status": "ready" if ready else "incomplete",
        "ready": ready,
        "isBlankDay": no_activity,
        "documentsReady": docs_ready,
        "materialized": sqlite_ready,
        "ragRequired": rag_required,
        "ragSynced": rag_ready,
        "ragDisabledReason": rag_disabled_reason,
        "novaTaskRequired": nova_task_required,
        "novaTaskUpdated": task_updated,
        "missingItems": missing,
        "missingKeys": [item["key"] for item in missing],
        "skippedItems": skipped,
        "plannedActions": _dedupe([item["action"] for item in missing]),
        "existingItems": existing_items,
        "existingData": bool(existing_items),
        "llmCalls": _estimate_llm_calls(missing),
    }


def _report_paths(paths: RuntimePaths, business_date: date) -> dict[str, list]:
    try:
        language_profile = _pipeline_language_profile(paths)
    except Exception:
        language_profile = "zh"
    return {
        report_type: _safe_diary_report_paths(paths, business_date, report_type, language_profile)
        for report_type in REQUIRED_DIARY_REPORTS
    }


def _safe_diary_report_paths(paths: RuntimePaths, business_date: date, report_type: str, language_profile: str) -> list:
    try:
        return diary_report_paths(paths.diary_dir, business_date, report_type, language_profile=language_profile)
    except Exception:
        return []


def _pipeline_language_profile(paths: RuntimePaths) -> str:
    settings = ensure_settings(paths)
    pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    return str(pipeline.get("languageProfile") or "zh")


def _doc_present(documents: list[dict], report_type: str) -> bool:
    return any(document.get("report_type") == report_type for document in documents)


def _has_no_activity_doc(documents: list[dict]) -> bool:
    for document in documents:
        if document.get("report_type") != "narrative":
            continue
        embedded = document.get("embeddedJson") or document.get("embedded_json") or {}
        if isinstance(embedded, dict) and embedded.get("activityState") == "empty":
            return True
        if str(document.get("relative_path") or "").endswith("-no-activity.md"):
            return True
    return False


def _has_no_activity_marker(documents: list[dict], report_paths: dict[str, list]) -> bool:
    if _has_no_activity_doc(documents):
        return True
    return any(path.name.endswith("-no-activity.md") for path in report_paths.get("narrative", []))


def _foundation_materialized(paths: RuntimePaths, business_date: date) -> bool:
    try:
        return daily_diary_usage_metrics(paths, business_date) is not None
    except Exception:
        return False


def _rag_sync_ready(paths: RuntimePaths, business_date: date) -> bool:
    try:
        snapshot = read_rag_daily_status_snapshot(paths, business_date)
    except Exception:
        return False
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else {}
    if not payload:
        return False
    status = str(payload.get("status") or snapshot.get("status") or "").lower()
    if status in {"failed", "error", "blocked", "missing"}:
        return False
    return True


def _rag_required(paths: RuntimePaths) -> bool:
    try:
        return is_rag_product_enabled(paths=paths)
    except Exception:
        return False


def _rag_disabled_reason(paths: RuntimePaths) -> str | None:
    try:
        return rag_product_disabled_reason(paths=paths) or "nova-RAG is unavailable."
    except Exception as exc:
        return f"nova-RAG status unavailable: {exc}"


def _nova_task_enabled(paths: RuntimePaths) -> bool:
    try:
        return is_nova_task_enabled(paths)
    except Exception:
        # Preserve the existing fail-closed contract when settings cannot be read.
        return True


def _nova_task_ready(paths: RuntimePaths, business_date: date) -> bool:
    try:
        with connect(paths, read_only=True) as connection:
            event = connection.execute(
                """
                SELECT created_at
                FROM nova_task_events
                WHERE business_date = ?
                ORDER BY created_at DESC, event_id DESC
                LIMIT 1
                """,
                (business_date.isoformat(),),
            ).fetchone()
            export = connection.execute(
                """
                SELECT generated_at
                FROM nova_task_exports
                WHERE export_type = 'task_board_markdown'
                ORDER BY generated_at DESC, export_id DESC
                LIMIT 1
                """
            ).fetchone()
    except Exception:
        return False
    evidence_at = str(event["created_at"]) if event is not None else _nova_task_work_graph_applied_at(paths, business_date)
    export_at = str(export["generated_at"]) if export is not None else ""
    return bool(export_at and evidence_at and _is_same_or_after(export_at, evidence_at))


def _nova_task_work_graph_applied(paths: RuntimePaths, business_date: date) -> bool:
    return _nova_task_work_graph_applied_at(paths, business_date) is not None


def _nova_task_work_graph_applied_at(paths: RuntimePaths, business_date: date) -> str | None:
    directories = [
        paths.state_dir / "nova-task" / "work-graph",
        paths.state_dir / "nova-task" / "candidate-reconciliation",
    ]
    prefix = f"{business_date.isoformat()}-"
    for directory in directories:
        if not directory.exists():
            continue
        try:
            artifacts = sorted(directory.glob(f"{prefix}*.md"), reverse=True)
        except Exception:
            continue
        for artifact in artifacts:
            try:
                head = artifact.read_text(encoding="utf-8")[:500]
            except Exception:
                continue
            if "- applied: true" in head:
                return _artifact_timestamp(paths, artifact)
    return None


def _artifact_timestamp(paths: RuntimePaths, path) -> str:
    match = re.match(r"\d{4}-\d{2}-\d{2}-(\d{8})-(\d{6})", path.name)
    if match:
        stamp = f"{match.group(1)}{match.group(2)}"
        try:
            return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=resolve_timezone(paths)).isoformat()
        except Exception:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().isoformat()
    except Exception:
        return ""


def _is_same_or_after(left: str, right: str) -> bool:
    left_dt = _parse_iso_datetime(left)
    right_dt = _parse_iso_datetime(right)
    if left_dt is None or right_dt is None:
        return bool(left and right and left >= right)
    return left_dt >= right_dt


def _parse_iso_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _existing_items(
    documents: list[dict],
    report_paths: dict[str, list],
    sqlite_ready: bool,
    rag_ready: bool,
    task_ready: bool,
    no_activity: bool,
) -> list[str]:
    items: list[str] = []
    if no_activity:
        items.append("blankday")
    for report_type in REQUIRED_DIARY_REPORTS:
        if bool(report_paths.get(report_type)) or _doc_present(documents, report_type):
            items.append(f"diary-{report_type}")
    if sqlite_ready:
        items.append("sqlite-materialization")
    if rag_ready:
        items.append("rag-sync")
    if task_ready and not no_activity:
        items.append("nova-task")
    return _dedupe(items)


def _missing(key: str, label: str, llm_calls: int, action: str) -> dict[str, Any]:
    return {"key": key, "label": label, "llmCalls": llm_calls, "action": action}


def _skipped(key: str, label: str, reason: str) -> dict[str, Any]:
    return {"key": key, "label": label, "reason": reason}


def _estimate_llm_calls(missing: list[dict[str, Any]]) -> int:
    actions = {item["action"] for item in missing}
    if "daily-full" in actions:
        return 3
    return sum(int(item.get("llmCalls") or 0) for item in missing if item.get("action") != "daily-materialization")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
