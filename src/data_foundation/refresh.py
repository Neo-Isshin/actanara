"""Background-safe projection refresh orchestration for Dashboard requests."""

from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from .db import connect, migrate
from .aggregate import daily_diary_usage_metrics
from .daily_completeness import evaluate_daily_completeness
from .diary_paths import diary_markdown_paths, diary_report_paths, diary_report_prefix, period_report_path
from .jobs import (
    begin_ingestion_run,
    finish_ingestion_run,
    ingestion_run_status,
    list_ingestion_runs,
    set_ingestion_run_status,
    update_ingestion_run_metadata,
)
from .diary_markdown import DIARY_PERIOD_PAGE_PROJECTION, materialize_diary_markdown_period_documents, materialize_diary_period_page_snapshot
from .diary_markdown import materialize_diary_markdown_day
from .paths import RuntimePaths
from .period_summary import DIARY_PERIOD_SUMMARY_PROJECTION, materialize_period_summary_snapshot
from .reports import LEGACY_ASSET_PROJECTION, materialize_legacy_asset_projection
from .reports import read_period_projection
from .settings import ensure_settings, llm_provider_readiness_error
from .snapshots import materialize_ai_assets_non_rag_snapshot, read_dashboard_snapshot
from .time import business_now, resolve_timezone
from .weather import fetch_weather_for_date
from .workspace_attribution import materialize_workspace_attribution_catalog


HISTORY_BACKFILL_ACTIVE_STATUSES = {"scheduled", "queued", "running", "cancel_requested"}
STALE_QUEUED_HISTORY_BACKFILL_AFTER = timedelta(minutes=2)
STALE_RUNNING_HISTORY_BACKFILL_AFTER = timedelta(hours=24)
HISTORY_BACKFILL_OUTCOME_SCHEMA_VERSION = 2
HISTORY_BACKFILL_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}


class HistoryBackfillAlreadyActiveError(RuntimeError):
    """Raised when a historical backfill is already scheduled or running."""

    def __init__(self, active_run: dict):
        self.active_run = active_run
        super().__init__(f"history backfill already active: run #{active_run.get('id')}")


class HistoryBackfillCancelled(RuntimeError):
    """Raised internally when a historical backfill receives a cancel request."""


def _history_period_stage_id(period: dict) -> str:
    return f"period:{period['kind']}:{period['start']}:{period['end']}"


def _history_requested_stages(periods: list[dict]) -> list[dict]:
    stages = [
        {"id": f"daily:{day.isoformat()}", "kind": "daily", "date": day.isoformat()}
        for day in _history_backfill_dates(periods)
    ]
    stages.append({"id": "snapshot:ai-assets", "kind": "snapshot", "snapshot": "ai-assets"})
    for period in periods:
        if period.get("kind") == "day":
            continue
        descriptor = {
            "id": _history_period_stage_id(period),
            "kind": "period",
            "period": {
                key: period[key]
                for key in ("kind", "start", "end", "days", "label", "daily")
                if key in period
            },
        }
        stages.append(descriptor)
    return stages


def _normalize_history_stage_descriptor(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("history backfill stage descriptor must be an object")
    kind = str(value.get("kind") or "")
    if kind == "daily":
        day = date.fromisoformat(str(value.get("date") or ""))
        expected = f"daily:{day.isoformat()}"
        if str(value.get("id") or "") != expected:
            raise ValueError("history backfill daily stage ID does not match its date")
        return {"id": expected, "kind": "daily", "date": day.isoformat()}
    if kind == "snapshot":
        if value.get("snapshot") != "ai-assets" or value.get("id") != "snapshot:ai-assets":
            raise ValueError("unsupported history backfill snapshot stage")
        return {"id": "snapshot:ai-assets", "kind": "snapshot", "snapshot": "ai-assets"}
    if kind == "period":
        raw_period = value.get("period")
        if not isinstance(raw_period, dict):
            raise ValueError("history backfill period stage requires a period descriptor")
        period = _normalize_history_periods([raw_period])[0]
        expected = _history_period_stage_id(period)
        if str(value.get("id") or "") != expected:
            raise ValueError("history backfill period stage ID does not match its period")
        result = {"id": expected, "kind": "period", "period": period}
        for key in ("retryArtifacts", "preservedArtifacts"):
            raw_items = value.get(key)
            if raw_items is None:
                continue
            if not isinstance(raw_items, list):
                raise ValueError(f"history backfill {key} must be a list")
            allowed = (
                {"page", "assets", "summary"}
                if key == "retryArtifacts"
                else {"diaryDocuments", "page", "assets", "summary"}
            )
            items = [str(item) for item in raw_items]
            if len(items) != len(set(items)) or any(item not in allowed for item in items):
                raise ValueError(f"history backfill {key} contains an unsupported artifact")
            result[key] = items
        return result
    raise ValueError(f"unsupported history backfill stage kind: {kind or 'missing'}")


def _normalize_history_stage_descriptors(values: object) -> list[dict]:
    if not isinstance(values, list):
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for value in values:
        descriptor = _normalize_history_stage_descriptor(value)
        if descriptor["id"] in seen:
            raise ValueError(f"duplicate history backfill stage: {descriptor['id']}")
        seen.add(descriptor["id"])
        normalized.append(descriptor)
    return normalized


def _decode_history_metadata(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _history_backfill_cas(
    paths: RuntimePaths,
    run_id: int,
    transform: Callable[[dict, dict], dict | None],
) -> dict | None:
    """Apply one status+metadata transition with compare-and-set semantics."""
    for _ in range(12):
        with connect(paths) as connection:
            row = connection.execute(
                """
                SELECT id, trigger_type, business_date, started_at, completed_at,
                       status, adapter_versions_json, error_summary
                FROM ingestion_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            current = dict(row)
            raw_metadata = current.get("adapter_versions_json")
            metadata = _decode_history_metadata(raw_metadata)
            change = transform(dict(current), dict(metadata))
            if change is None:
                current["metadata"] = metadata
                return current
            next_metadata = change.get("metadata") if isinstance(change.get("metadata"), dict) else metadata
            next_status = str(change.get("status") or current["status"])
            next_completed_at = change.get("completed_at", current.get("completed_at"))
            next_error = change.get("error_summary", current.get("error_summary"))
            encoded = json.dumps(next_metadata, ensure_ascii=False, sort_keys=True)
            cursor = connection.execute(
                """
                UPDATE ingestion_runs
                SET status = ?, adapter_versions_json = ?, completed_at = ?, error_summary = ?
                WHERE id = ? AND status = ?
                  AND COALESCE(adapter_versions_json, '') = COALESCE(?, '')
                """,
                (
                    next_status,
                    encoded,
                    next_completed_at,
                    next_error,
                    run_id,
                    current["status"],
                    raw_metadata,
                ),
            )
            if cursor.rowcount == 1:
                current.update(
                    {
                        "status": next_status,
                        "adapter_versions_json": encoded,
                        "completed_at": next_completed_at,
                        "error_summary": next_error,
                        "metadata": next_metadata,
                    }
                )
                return current
    raise RuntimeError("history backfill ledger changed concurrently too many times")


def _safe_history_backfill_error(error: object) -> str:
    text = str(error or "history backfill stage failed").replace("\x00", "")
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|authorization|secret|token)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    return text[:500]


def _initialize_history_backfill_run(paths: RuntimePaths, run_id: int, requested_stages: list[dict]) -> dict | None:
    now = datetime.now().astimezone().isoformat()

    def transform(row: dict, metadata: dict) -> dict | None:
        if row["status"] in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        normalized = _normalize_history_stage_descriptors(metadata.get("requestedStages"))
        if not normalized:
            normalized = _normalize_history_stage_descriptors(requested_stages)
        metadata.update(
            {
                "outcomeSchemaVersion": HISTORY_BACKFILL_OUTCOME_SCHEMA_VERSION,
                "outcomeProvenance": "native-v2",
                "requestedStages": normalized,
                "stageOutcomes": metadata.get("stageOutcomes") if isinstance(metadata.get("stageOutcomes"), dict) else {},
                "failedStages": metadata.get("failedStages") if isinstance(metadata.get("failedStages"), list) else [],
                "retryStages": metadata.get("retryStages") if isinstance(metadata.get("retryStages"), list) else [],
                "workerPid": os.getpid(),
                "workerStartedAt": metadata.get("workerStartedAt") or now,
                "heartbeatAt": now,
            }
        )
        if row["status"] == "cancel_requested" or metadata.get("cancelRequested"):
            return {"status": "cancel_requested", "metadata": metadata}
        return {"status": "running", "metadata": metadata}

    return _history_backfill_cas(paths, run_id, transform)


def _claim_history_backfill_stage(
    paths: RuntimePaths,
    run_id: int,
    descriptor: dict,
    *,
    progress: int,
    stage: str,
    stage_label: str,
    extra: dict | None = None,
) -> bool:
    normalized = _normalize_history_stage_descriptor(descriptor)
    now = datetime.now().astimezone().isoformat()

    def transform(row: dict, metadata: dict) -> dict | None:
        if row["status"] in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        if row["status"] == "cancel_requested" or metadata.get("cancelRequested"):
            return None
        metadata.update(
            {
                "progress": max(0, min(99, int(progress))),
                "currentStage": stage,
                "currentStageId": normalized["id"],
                "currentStageLabel": stage_label,
                "stageClaimedAt": now,
                "heartbeatAt": now,
                "workerPid": os.getpid(),
            }
        )
        if extra:
            metadata.update(extra)
        return {"status": "running", "metadata": metadata}

    updated = _history_backfill_cas(paths, run_id, transform)
    if updated is None:
        return False
    metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
    return updated.get("status") == "running" and metadata.get("currentStageId") == normalized["id"]


def _record_history_backfill_stage_outcome(
    paths: RuntimePaths,
    run_id: int,
    descriptor: dict,
    *,
    status: str,
    artifact_committed: bool,
    error: object | None = None,
    details: dict | None = None,
    extra: dict | None = None,
) -> None:
    normalized = _normalize_history_stage_descriptor(descriptor)
    now = datetime.now().astimezone().isoformat()
    clean_error = _safe_history_backfill_error(error) if error is not None else None

    def transform(row: dict, metadata: dict) -> dict | None:
        if row["status"] in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        outcomes = dict(metadata.get("stageOutcomes") or {}) if isinstance(metadata.get("stageOutcomes"), dict) else {}
        outcome = {
            "stageId": normalized["id"],
            "status": status,
            "artifactCommitted": bool(artifact_committed),
            "updatedAt": now,
        }
        if clean_error:
            outcome["error"] = clean_error
        if details:
            outcome["details"] = details
        outcomes[normalized["id"]] = outcome
        metadata.update(
            {
                "stageOutcomes": outcomes,
                "lastCompletedStageId": normalized["id"],
                "heartbeatAt": now,
            }
        )
        if extra:
            metadata.update(extra)
        return {"status": row["status"], "metadata": metadata}

    _history_backfill_cas(paths, run_id, transform)


def _history_backfill_retry_descriptor(descriptor: dict, outcome: dict) -> dict:
    retry = _normalize_history_stage_descriptor(descriptor)
    if retry["kind"] != "period":
        return retry
    details = outcome.get("details") if isinstance(outcome.get("details"), dict) else {}
    artifacts = details.get("artifacts") if isinstance(details.get("artifacts"), dict) else {}
    requested = details.get("requestedArtifacts") if isinstance(details.get("requestedArtifacts"), list) else []
    retry_artifacts = [name for name in requested if artifacts.get(name) not in {"completed", "reused"}]
    preserved = [name for name, state in artifacts.items() if state in {"completed", "reused"}]
    if retry_artifacts:
        retry["retryArtifacts"] = retry_artifacts
    if preserved:
        retry["preservedArtifacts"] = sorted(set(preserved))
    return retry


def _reduce_history_backfill_outcomes(metadata: dict) -> dict:
    requested = _normalize_history_stage_descriptors(metadata.get("requestedStages"))
    outcomes = metadata.get("stageOutcomes") if isinstance(metadata.get("stageOutcomes"), dict) else {}
    failed_ids: list[str] = []
    retry_stages: list[dict] = []
    failures: list[str] = []
    committed = False
    for descriptor in requested:
        stage_id = descriptor["id"]
        outcome = outcomes.get(stage_id) if isinstance(outcomes.get(stage_id), dict) else None
        if outcome and outcome.get("artifactCommitted") is True:
            committed = True
        if outcome and outcome.get("status") in {"completed", "skipped"}:
            continue
        failed_ids.append(stage_id)
        retry_stages.append(_history_backfill_retry_descriptor(descriptor, outcome or {}))
        error = str((outcome or {}).get("error") or f"{stage_id} did not complete")
        if descriptor["kind"] == "daily":
            error = f"daily pipeline {descriptor['date']}: {error}"
        elif descriptor["kind"] == "snapshot":
            error = f"AI Assets snapshot: {error}"
        else:
            error = f"period {descriptor['period']['start']}..{descriptor['period']['end']}: {error}"
        failures.append(error)
    orchestration_error = metadata.get("orchestrationError")
    if orchestration_error:
        failed_ids.append("orchestration")
        failures.append(_safe_history_backfill_error(orchestration_error))
    if not failed_ids:
        status = "completed"
    else:
        status = "partial" if committed else "failed"
    return {
        "status": status,
        "failedStages": failed_ids,
        "retryStages": retry_stages,
        "errorSummary": "; ".join(failures)[:1000] if failures else None,
        "artifactCommitted": committed,
    }


def _finalize_history_backfill(paths: RuntimePaths, run_id: int) -> dict | None:
    now = datetime.now().astimezone().isoformat()

    def transform(row: dict, metadata: dict) -> dict | None:
        if row["status"] in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        if row["status"] == "cancel_requested" or metadata.get("cancelRequested"):
            metadata.update(
                {
                    "cancelled": True,
                    "progress": 100,
                    "currentStage": "cancelled",
                    "currentStageLabel": "Cancelled by user request",
                    "heartbeatAt": now,
                }
            )
            return {
                "status": "cancelled",
                "metadata": metadata,
                "completed_at": now,
                "error_summary": "Cancelled by user request",
            }
        reduction = _reduce_history_backfill_outcomes(metadata)
        metadata.update(
            {
                "progress": 100,
                "currentStage": "completed" if reduction["status"] == "completed" else "completed-with-failures",
                "currentStageLabel": (
                    "History backfill completed"
                    if reduction["status"] == "completed"
                    else "History backfill completed with failures"
                ),
                "failedStages": reduction["failedStages"],
                "retryStages": reduction["retryStages"],
                "heartbeatAt": now,
            }
        )
        return {
            "status": reduction["status"],
            "metadata": metadata,
            "completed_at": now,
            "error_summary": reduction["errorSummary"],
        }

    return _history_backfill_cas(paths, run_id, transform)


def _update_history_backfill_metadata(paths: RuntimePaths, run_id: int, values: dict) -> None:
    now = datetime.now().astimezone().isoformat()

    def transform(row: dict, metadata: dict) -> dict | None:
        if row["status"] in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        metadata.update(values)
        metadata["heartbeatAt"] = now
        return {"status": row["status"], "metadata": metadata}

    _history_backfill_cas(paths, run_id, transform)


def _set_refresh_progress(
    paths: RuntimePaths,
    run_id: int,
    *,
    progress: int,
    stage: str,
    stage_label: str,
    extra: dict | None = None,
) -> None:
    metadata = {
        "progress": max(0, min(100, int(progress))),
        "currentStage": stage,
        "currentStageLabel": stage_label,
    }
    if extra:
        metadata.update(extra)
    update_ingestion_run_metadata(paths, run_id, metadata)


def _latest_ai_assets_usage_cache(paths: RuntimePaths) -> dict:
    snapshot = read_dashboard_snapshot(paths)
    payload = snapshot.get("payload") if isinstance(snapshot, dict) else {}
    usage_cache = payload.get("usageCache") if isinstance(payload, dict) else None
    return usage_cache if isinstance(usage_cache, dict) else {}


def queue_projection_refresh(paths: RuntimePaths, business_date: date, *, period_start: date | None = None) -> int:
    migrate(paths)
    metadata = {
        "projection": "legacy-compatible-v1",
        "scope": "ai-assets" if period_start is None else "period-assets",
    }
    if period_start is not None:
        metadata["periodStart"] = period_start.isoformat()
        metadata["periodEnd"] = business_date.isoformat()
        metadata["periodDays"] = (business_date - period_start).days + 1
    return begin_ingestion_run(
        paths,
        trigger_type="dashboard-projection-refresh",
        business_date=business_date,
        adapter_versions=metadata,
        status="queued",
    )


def queue_period_summary_refresh(paths: RuntimePaths, business_date: date, *, period_start: date) -> int:
    migrate(paths)
    period_days = (business_date - period_start).days + 1
    return begin_ingestion_run(
        paths,
        trigger_type="dashboard-period-summary-refresh",
        business_date=business_date,
        adapter_versions={
            "projection": "diary-period-summary-v1",
            "scope": "period-summary",
            "periodStart": period_start.isoformat(),
            "periodEnd": business_date.isoformat(),
            "periodDays": period_days,
            "workEstimate": {
                "periodDays": period_days,
                "llmCalls": 1,
                "longRunning": period_days >= 28,
            },
        },
        status="queued",
    )


def run_projection_refresh(
    paths: RuntimePaths,
    run_id: int,
    *,
    period_start: date | None = None,
    period_days: int | None = None,
    ai_assets_builder: Callable[[], dict] | None = None,
    period_builder: Callable[[date, int], dict] | None = None,
) -> None:
    set_ingestion_run_status(paths, run_id, status="running")
    run_status = ingestion_run_status(paths, run_id) or {}
    refresh_business_date = date.fromisoformat(str(run_status.get("business_date") or business_now(paths).date().isoformat()))
    _set_refresh_progress(
        paths,
        run_id,
        progress=15,
        stage="ai-assets-snapshot",
        stage_label="Refreshing AI Assets usage cache and snapshot",
    )
    try:
        materialize_ai_assets_non_rag_snapshot(
            paths,
            run_id,
            builder=ai_assets_builder,
            business_date=refresh_business_date,
        )
        workspace_catalog = materialize_workspace_attribution_catalog(paths)
        _set_refresh_progress(
            paths,
            run_id,
            progress=55 if period_start is not None and period_days is not None else 95,
            stage="ai-assets-snapshot-ready",
            stage_label="AI Assets snapshot ready",
            extra={"usageCache": _latest_ai_assets_usage_cache(paths), "workspaceAttribution": workspace_catalog.get("counts", {})},
        )
        if period_start is not None and period_days is not None:
            period_end = period_start + timedelta(days=period_days - 1)
            _set_refresh_progress(
                paths,
                run_id,
                progress=70,
                stage="period-assets",
                stage_label="Materializing period asset projection",
            )
            materialize_legacy_asset_projection(
                paths,
                period_start,
                period_end,
                run_id,
                builder=period_builder,
            )
            _set_refresh_progress(
                paths,
                run_id,
                progress=85,
                stage="period-markdown",
                stage_label="Materializing diary Markdown period projection",
            )
            materialize_diary_markdown_period_documents(paths, period_start, period_end, source_run_id=run_id)
            materialize_diary_period_page_snapshot(paths, period_start, period_end, source_run_id=run_id)
        _set_refresh_progress(paths, run_id, progress=100, stage="completed", stage_label="Refresh completed")
        finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise


def run_period_summary_refresh(
    paths: RuntimePaths,
    run_id: int,
    *,
    period_start: date,
    period_days: int,
    period_builder: Callable[[date, int], dict] | None = None,
) -> None:
    set_ingestion_run_status(paths, run_id, status="running")
    try:
        period_end = period_start + timedelta(days=period_days - 1)
        _ensure_period_summary_inputs(paths, run_id, period_start, period_end, period_builder=period_builder)
        _set_refresh_progress(
            paths,
            run_id,
            progress=85,
            stage="period-summary",
            stage_label="Materializing period summary snapshot",
        )
        materialize_period_summary_snapshot(paths, period_start, period_end, source_run_id=run_id)
        _set_refresh_progress(paths, run_id, progress=100, stage="completed", stage_label="Refresh completed")
        finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise


def _period_projection_ready(paths: RuntimePaths, start_date: date, end_date: date, *, projection_type: str) -> bool:
    projection = read_period_projection(paths, start_date, end_date, projection_type=projection_type)
    return bool(projection and projection.get("status") == "ready")


def _ensure_period_summary_inputs(
    paths: RuntimePaths,
    run_id: int,
    period_start: date,
    period_end: date,
    *,
    period_builder: Callable[[date, int], dict] | None = None,
) -> None:
    period_days = (period_end - period_start).days + 1
    assets_ready = _period_projection_ready(paths, period_start, period_end, projection_type=LEGACY_ASSET_PROJECTION)
    page_ready = _period_projection_ready(paths, period_start, period_end, projection_type=DIARY_PERIOD_PAGE_PROJECTION)
    if assets_ready and page_ready:
        _set_refresh_progress(
            paths,
            run_id,
            progress=75,
            stage="period-inputs-ready",
            stage_label="Reusing ready period projections",
        )
        return
    if not assets_ready:
        _set_refresh_progress(
            paths,
            run_id,
            progress=20,
            stage="period-assets",
            stage_label="Materializing period asset projection",
        )
        materialize_legacy_asset_projection(
            paths,
            period_start,
            period_end,
            run_id,
            builder=period_builder,
        )
    if not page_ready:
        _set_refresh_progress(
            paths,
            run_id,
            progress=45,
            stage="period-markdown",
            stage_label="Materializing diary Markdown period projection",
        )
        materialize_diary_markdown_period_documents(paths, period_start, period_end, source_run_id=run_id)
        _set_refresh_progress(
            paths,
            run_id,
            progress=65,
            stage="period-page",
            stage_label="Materializing period page projection",
        )
        materialize_diary_period_page_snapshot(paths, period_start, period_end, source_run_id=run_id)


def _last_day_of_month(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _previous_month_end(day: date) -> date:
    first = day.replace(day=1)
    return first - timedelta(days=1)


def completed_period_summary_targets(paths: RuntimePaths, business_date: date) -> list[dict]:
    """Return completed week/month summaries missing from the projection store."""
    week_end = business_date if business_date.weekday() == 6 else business_date - timedelta(days=business_date.weekday() + 1)
    week_start = week_end - timedelta(days=6)
    month_end = business_date if business_date == _last_day_of_month(business_date) else _previous_month_end(business_date)
    month_start = month_end.replace(day=1)
    candidates = [
        {"label": "completed-week", "start": week_start, "end": week_end},
        {"label": "completed-month", "start": month_start, "end": month_end},
    ]
    targets: list[dict] = []
    for candidate in candidates:
        if read_period_projection(
            paths,
            candidate["start"],
            candidate["end"],
            projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
        ):
            continue
        targets.append(candidate)
    return targets


def materialize_due_period_summaries(
    paths: RuntimePaths,
    business_date: date,
    *,
    period_builder: Callable[[date, int], dict] | None = None,
) -> list[dict]:
    """Generate missing completed-period summaries without owning daily pipeline success."""
    results: list[dict] = []
    try:
        targets = completed_period_summary_targets(paths, business_date)
    except Exception as error:
        return [
            {
                "label": "completed-period-detection",
                "start": None,
                "end": None,
                "days": 0,
                "runId": None,
                "status": "failed",
                "error": str(error),
            }
        ]
    for target in targets:
        period_start = target["start"]
        period_end = target["end"]
        period_days = (period_end - period_start).days + 1
        run_id = queue_period_summary_refresh(paths, period_end, period_start=period_start)
        result = {
            "label": target["label"],
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": period_days,
            "runId": run_id,
            "status": "queued",
        }
        try:
            run_period_summary_refresh(
                paths,
                run_id,
                period_start=period_start,
                period_days=period_days,
                period_builder=period_builder,
            )
            result["status"] = "completed"
        except Exception as error:
            result["status"] = "failed"
            result["error"] = str(error)
        results.append(result)
    return results


def plan_history_backfill(
    start_date: date,
    end_date: date,
    *,
    grain: str = "both",
    include_summaries: bool = False,
    skip_ready: bool = True,
    periods: list[dict] | None = None,
    paths: RuntimePaths | None = None,
) -> dict:
    """Build the bounded period list for a manual historical backfill."""
    periods = _normalize_history_periods(periods) if periods is not None else _history_backfill_periods(start_date, end_date, grain=grain)
    dates = _history_backfill_dates(periods)
    pending_items = _history_backfill_pending_items(paths, periods, dates, include_summaries=include_summaries, skip_ready=skip_ready)
    existing_diary_days = _history_backfill_existing_diary_days(paths, dates)
    daily_items = [item for item in pending_items if item["kind"] == "diary"]
    summary_items = [item for item in pending_items if item["kind"] in {"week-summary", "month-summary"}]
    overwrite_items = [item for item in pending_items if item.get("overwrite")]
    return {
        "mode": "dry-run",
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "grain": grain,
        "includeSummaries": bool(include_summaries),
        "skipReady": bool(skip_ready),
        "periodCount": len(periods),
        "llmCallCount": sum(int(item.get("llmCalls") or 0) for item in pending_items),
        "dailyPipelineDays": len(dates),
        "dailyPipelineDates": [day.isoformat() for day in dates],
        "existingDiaryDays": len(existing_diary_days),
        "existingDiaryDates": [day.isoformat() for day in existing_diary_days],
        "overwriteItems": overwrite_items,
        "overwriteItemCount": len(overwrite_items),
        "periods": periods,
        "pendingItems": pending_items,
        "pendingItemCount": len(pending_items),
        "pendingDiaryDays": len(daily_items),
        "pendingSummaryReports": len(summary_items),
        "warnings": [
            "历史数据生成会按每日完整性契约补齐缺失项，再生成周/月聚合。",
            "历史数据生成可能运行很久；周期和日期越多，耗时越长。",
            "勾选生成周/月总结会覆盖当前已有周/月总结，并调用当前 LLM Provider。",
        ],
    }


def queue_history_backfill(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    grain: str = "both",
    include_summaries: bool = False,
    skip_ready: bool = True,
    overwrite_daily: bool = False,
    scheduled_at: str | None = None,
    periods: list[dict] | None = None,
    source_run_id: int | None = None,
    require_llm_ready: bool = False,
    requested_stages: list[dict] | None = None,
) -> int:
    migrate(paths)
    active = active_history_backfill_run(paths)
    if active is not None:
        raise HistoryBackfillAlreadyActiveError(active)
    selected_periods = periods if periods is not None else None
    plan = plan_history_backfill(
        start_date,
        end_date,
        grain=grain,
        include_summaries=include_summaries,
        skip_ready=skip_ready,
        periods=selected_periods,
        paths=paths,
    )
    readiness_error = (
        llm_provider_readiness_error(paths, require_cross_process_secret=True)
        if require_llm_ready and int(plan.get("llmCallCount") or 0) > 0
        else None
    )
    if readiness_error:
        raise ValueError(readiness_error)
    effective_periods = _normalize_history_periods(selected_periods or plan.get("periods") or [])
    effective_requested_stages = (
        _normalize_history_stage_descriptors(requested_stages)
        if requested_stages is not None
        else _history_requested_stages(effective_periods)
    )
    if requested_stages is None:
        effective_daily_pipeline_days = len(_history_backfill_dates(effective_periods))
        effective_llm_call_count = int(plan["llmCallCount"])
    else:
        requested_daily_dates = {
            stage["date"]
            for stage in effective_requested_stages
            if stage["kind"] == "daily"
        }
        effective_daily_pipeline_days = len(requested_daily_dates)
        effective_llm_call_count = sum(
            int(item.get("llmCalls") or 0)
            for item in plan.get("pendingItems") or []
            if item.get("kind") == "diary" and item.get("date") in requested_daily_dates
        )
        effective_llm_call_count += sum(
            1
            for stage in effective_requested_stages
            if stage["kind"] == "period"
            and include_summaries
            and (
                "retryArtifacts" not in stage
                or "summary" in (stage.get("retryArtifacts") or [])
            )
        )
    return begin_ingestion_run(
        paths,
        trigger_type="dashboard-history-backfill",
        business_date=end_date,
        adapter_versions={
            "projection": "historical-backfill-v1",
            "scope": "history-backfill",
            "periodStart": start_date.isoformat(),
            "periodEnd": end_date.isoformat(),
            "periodDays": (end_date - start_date).days + 1,
            "grain": grain,
            "includeSummaries": bool(include_summaries),
            "skipReady": bool(skip_ready),
            "overwriteDaily": bool(overwrite_daily),
            "reuseFoundationInputsOnOverwrite": bool(overwrite_daily),
            "totalPeriods": plan["periodCount"],
            "llmCallCount": effective_llm_call_count,
            "dailyPipelineDays": effective_daily_pipeline_days,
            "scheduledAt": scheduled_at,
            "periods": effective_periods,
            "sourceRunId": source_run_id,
            "outcomeSchemaVersion": HISTORY_BACKFILL_OUTCOME_SCHEMA_VERSION,
            "outcomeProvenance": "native-v2",
            "requestedStages": effective_requested_stages,
            "stageOutcomes": {},
            "failedStages": [],
            "retryStages": [],
        },
        status="scheduled" if scheduled_at else "queued",
    )


def active_history_backfill_run(paths: RuntimePaths) -> dict | None:
    migrate(paths)
    for run in list_ingestion_runs(paths, trigger_types=("dashboard-history-backfill",), limit=100):
        run = _reconcile_orphaned_history_backfill(paths, run)
        if run.get("status") in HISTORY_BACKFILL_ACTIVE_STATUSES:
            return run
    return None


def cancel_history_backfill(paths: RuntimePaths, run_id: int) -> dict:
    migrate(paths)
    run = projection_refresh_status(paths, run_id)
    if run is None or run.get("trigger_type") != "dashboard-history-backfill":
        raise ValueError("history backfill run not found")
    run = _reconcile_orphaned_history_backfill(paths, run)
    now = datetime.now().astimezone().isoformat()
    did_request = [False]

    def transform(row: dict, metadata: dict) -> dict | None:
        did_request[0] = False
        status = str(row.get("status") or "")
        if status in HISTORY_BACKFILL_TERMINAL_STATUSES:
            return None
        did_request[0] = True
        metadata.update(
            {
                "cancelRequested": True,
                "cancelRequestedAt": now,
                "currentStage": "cancel-requested",
                "currentStageLabel": "Cancel requested",
                "heartbeatAt": now,
            }
        )
        if status in {"scheduled", "queued"}:
            metadata.update({"cancelled": True, "progress": 100})
            return {
                "status": "cancelled",
                "metadata": metadata,
                "completed_at": now,
                "error_summary": "Cancelled by user request",
            }
        return {"status": "cancel_requested", "metadata": metadata}

    updated = _history_backfill_cas(paths, run_id, transform)
    if updated is None:
        raise ValueError("history backfill run not found")
    final_status = str(updated.get("status") or "")
    return {"runId": run_id, "status": final_status, "cancelRequested": did_request[0]}


def _history_backfill_cancel_requested(paths: RuntimePaths, run_id: int) -> bool:
    run = ingestion_run_status(paths, run_id)
    if run is None:
        return False
    if run.get("status") in {"cancelled", "cancel_requested"}:
        return True
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    return bool(metadata.get("cancelRequested"))


def _raise_if_history_backfill_cancelled(paths: RuntimePaths, run_id: int) -> None:
    if _history_backfill_cancel_requested(paths, run_id):
        raise HistoryBackfillCancelled("history backfill cancelled")


def _finish_cancelled_history_backfill(paths: RuntimePaths, run_id: int, *, extra: dict | None = None) -> None:
    payload = {"cancelled": True, "cancelRequested": True}
    if extra:
        payload.update(extra)
    _update_history_backfill_metadata(paths, run_id, payload)
    _finalize_history_backfill(paths, run_id)


def _materialize_history_ai_assets_snapshot(
    paths: RuntimePaths,
    run_id: int,
    *,
    business_date: date,
    builder: Callable[[], dict] | None,
    skip_default_builder: bool = False,
) -> dict:
    if skip_default_builder:
        return {"status": "skipped", "reason": "custom daily pipeline runner without AI assets builder"}
    try:
        snapshot_key = materialize_ai_assets_non_rag_snapshot(
            paths,
            run_id,
            builder=builder,
            business_date=business_date,
        )
        return {"status": "ready", "snapshotKey": snapshot_key}
    except Exception as error:
        return {"status": "failed", "error": _safe_history_backfill_error(error)}


def _execute_history_period_stage(
    paths: RuntimePaths,
    run_id: int,
    descriptor: dict,
    *,
    include_summaries: bool,
    skip_ready: bool,
    period_builder: Callable[[date, int], dict] | None,
) -> tuple[dict, dict]:
    descriptor = _normalize_history_stage_descriptor(descriptor)
    period = descriptor["period"]
    period_start = date.fromisoformat(period["start"])
    period_end = date.fromisoformat(period["end"])
    period_days = (period_end - period_start).days + 1
    assets_ready = _period_projection_ready(paths, period_start, period_end, projection_type=LEGACY_ASSET_PROJECTION)
    page_ready = _period_projection_ready(paths, period_start, period_end, projection_type=DIARY_PERIOD_PAGE_PROJECTION)
    summary_ready = _period_projection_ready(paths, period_start, period_end, projection_type=DIARY_PERIOD_SUMMARY_PROJECTION)
    artifacts = {
        "diaryDocuments": "reused" if page_ready else "unstarted",
        "page": "reused" if page_ready else "unstarted",
        "assets": "reused" if assets_ready else "unstarted",
        "summary": "reused" if summary_ready else "not-requested",
    }
    preserved = set(descriptor.get("preservedArtifacts") or [])
    for name in preserved:
        artifacts[name] = "reused"
    if "retryArtifacts" in descriptor:
        requested_artifacts = list(descriptor.get("retryArtifacts") or [])
    else:
        requested_artifacts = []
        if not (skip_ready and page_ready):
            requested_artifacts.append("page")
        if not (skip_ready and assets_ready):
            requested_artifacts.append("assets")
        if include_summaries:
            requested_artifacts.append("summary")
    committed = False
    current_artifact = None
    try:
        for current_artifact in requested_artifacts:
            if current_artifact == "page":
                if "diaryDocuments" not in preserved:
                    materialize_diary_markdown_period_documents(paths, period_start, period_end, source_run_id=run_id)
                    artifacts["diaryDocuments"] = "completed"
                    committed = True
                materialize_diary_period_page_snapshot(paths, period_start, period_end, source_run_id=run_id)
                artifacts["page"] = "completed"
                committed = True
            elif current_artifact == "assets":
                materialize_legacy_asset_projection(
                    paths,
                    period_start,
                    period_end,
                    run_id,
                    builder=period_builder,
                )
                artifacts["assets"] = "completed"
                committed = True
            elif current_artifact == "summary":
                materialize_period_summary_snapshot(paths, period_start, period_end, source_run_id=run_id)
                artifacts["summary"] = "completed"
                committed = True
        status = "completed" if requested_artifacts else "skipped"
        return (
            {**period, "status": status},
            {
                "status": status,
                "artifactCommitted": committed,
                "details": {"requestedArtifacts": requested_artifacts, "artifacts": artifacts},
            },
        )
    except Exception as error:
        if current_artifact:
            artifacts[current_artifact] = "failed"
        clean_error = _safe_history_backfill_error(error)
        return (
            {**period, "status": "failed", "error": clean_error},
            {
                "status": "failed",
                "artifactCommitted": committed,
                "error": clean_error,
                "details": {"requestedArtifacts": requested_artifacts, "artifacts": artifacts},
            },
        )


def run_history_backfill(
    paths: RuntimePaths,
    run_id: int,
    *,
    start_date: date,
    end_date: date,
    grain: str = "both",
    include_summaries: bool = False,
    skip_ready: bool = True,
    overwrite_daily: bool = False,
    periods: list[dict] | None = None,
    daily_pipeline_runner: Callable[[str, RuntimePaths], object] | None = None,
    period_builder: Callable[[date, int], dict] | None = None,
    ai_assets_builder: Callable[[], dict] | None = None,
) -> dict:
    """Backfill historical period projections, optionally generating LLM summaries."""
    periods = _normalize_history_periods(periods) if periods is not None else _history_backfill_periods(start_date, end_date, grain=grain)
    current = ingestion_run_status(paths, run_id)
    if current and current.get("status") in HISTORY_BACKFILL_TERMINAL_STATUSES:
        return {
            "runId": run_id,
            "dailyPipeline": {"total": 0, "completed": [], "skipped": [], "failed": []},
            "periods": [],
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": current.get("status") == "cancelled",
            "status": current.get("status"),
        }
    requested_fallback = _history_requested_stages(periods)
    initialized = _initialize_history_backfill_run(paths, run_id, requested_fallback)
    if initialized is None:
        raise ValueError("history backfill run not found")
    initialized_metadata = initialized.get("metadata") if isinstance(initialized.get("metadata"), dict) else {}
    requested_stages = _normalize_history_stage_descriptors(initialized_metadata.get("requestedStages"))
    if initialized.get("status") == "cancel_requested" or initialized_metadata.get("cancelRequested"):
        _finish_cancelled_history_backfill(paths, run_id)
        return {
            "runId": run_id,
            "dailyPipeline": {"total": 0, "completed": [], "skipped": [], "failed": []},
            "periods": [],
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "cancelled": True,
            "status": "cancelled",
        }
    completed = 0
    skipped = 0
    failed = 0
    results: list[dict] = []
    daily_result = {"total": 0, "completed": [], "skipped": [], "failed": []}
    ai_assets_snapshot: dict = {"status": "not-requested"}
    try:
        _raise_if_history_backfill_cancelled(paths, run_id)
        daily_result = _run_history_daily_pipelines(
            paths,
            run_id,
            requested_stages,
            skip_ready=skip_ready,
            overwrite_daily=overwrite_daily,
            runner=daily_pipeline_runner,
        )
        if daily_result.get("cancelled"):
            raise HistoryBackfillCancelled("history backfill cancelled")
        snapshot_stage = next((stage for stage in requested_stages if stage.get("kind") == "snapshot"), None)
        if snapshot_stage is not None:
            if not _claim_history_backfill_stage(
                paths,
                run_id,
                snapshot_stage,
                progress=30,
                stage="history-ai-assets-snapshot",
                stage_label="Refreshing AI Assets snapshot",
                extra={"dailyPipeline": daily_result},
            ):
                raise HistoryBackfillCancelled("history backfill cancelled")
            ai_assets_snapshot = _materialize_history_ai_assets_snapshot(
                paths,
                run_id,
                business_date=end_date,
                builder=ai_assets_builder,
                skip_default_builder=daily_pipeline_runner is not None and ai_assets_builder is None,
            )
            snapshot_status = str(ai_assets_snapshot.get("status") or "failed")
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                snapshot_stage,
                status="completed" if snapshot_status == "ready" else snapshot_status,
                artifact_committed=snapshot_status == "ready",
                error=ai_assets_snapshot.get("error"),
                details=ai_assets_snapshot,
                extra={"dailyPipeline": daily_result, "aiAssetsSnapshot": ai_assets_snapshot},
            )
        _raise_if_history_backfill_cancelled(paths, run_id)
        period_stages = {
            stage["id"]: stage
            for stage in requested_stages
            if stage.get("kind") == "period"
        }
        for index, period in enumerate(periods, start=1):
            _raise_if_history_backfill_cancelled(paths, run_id)
            if period.get("kind") == "day":
                if any(stage.get("kind") == "daily" and stage.get("date") == period["start"] for stage in requested_stages):
                    results.append({**period, "status": "daily-only"})
                continue
            descriptor = period_stages.get(_history_period_stage_id(period))
            if descriptor is None:
                continue
            stage_prefix = f"{period['kind']} {period['start']}..{period['end']}"
            if not _claim_history_backfill_stage(
                paths,
                run_id,
                progress=_history_progress(index - 1, len(periods)),
                stage="history-backfill-period",
                stage_label=f"Backfilling {stage_prefix}",
                descriptor=descriptor,
                extra={
                    "dailyPipeline": daily_result,
                    "aiAssetsSnapshot": ai_assets_snapshot,
                    "currentPeriod": period,
                    "completedPeriods": completed,
                    "skippedPeriods": skipped,
                    "failedPeriods": failed,
                },
            ):
                raise HistoryBackfillCancelled("history backfill cancelled")
            period_result, outcome = _execute_history_period_stage(
                paths,
                run_id,
                descriptor,
                include_summaries=include_summaries,
                skip_ready=skip_ready,
                period_builder=period_builder,
            )
            results.append(period_result)
            if period_result["status"] == "completed":
                completed += 1
            elif period_result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                descriptor,
                status=outcome["status"],
                artifact_committed=outcome["artifactCommitted"],
                error=outcome.get("error"),
                details=outcome.get("details"),
                extra={
                    "completedPeriods": completed,
                    "skippedPeriods": skipped,
                    "failedPeriods": failed,
                    "dailyPipeline": daily_result,
                    "aiAssetsSnapshot": ai_assets_snapshot,
                    "failedPeriodDetails": [item for item in results if item.get("status") == "failed"],
                },
            )
            _raise_if_history_backfill_cancelled(paths, run_id)
        daily_failed = len(daily_result.get("failed") or [])
        _update_history_backfill_metadata(
            paths,
            run_id,
            {
                "completedPeriods": completed,
                "skippedPeriods": skipped,
                "failedPeriods": failed,
                "failedDailyPipelineDays": daily_failed,
                "dailyPipeline": daily_result,
                "aiAssetsSnapshot": ai_assets_snapshot,
                "failedPeriodDetails": [item for item in results if item.get("status") == "failed"],
            },
        )
        final = _finalize_history_backfill(paths, run_id)
        final_metadata = final.get("metadata") if isinstance(final, dict) and isinstance(final.get("metadata"), dict) else {}
        return {
            "runId": run_id,
            "dailyPipeline": daily_result,
            "periods": results,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "status": final.get("status") if isinstance(final, dict) else None,
            "stageOutcomes": final_metadata.get("stageOutcomes") or {},
            "retryStages": final_metadata.get("retryStages") or [],
        }
    except HistoryBackfillCancelled:
        _finish_cancelled_history_backfill(
            paths,
            run_id,
            extra={
                "completedPeriods": completed,
                "skippedPeriods": skipped,
                "failedPeriods": failed,
                "dailyPipeline": daily_result,
                "aiAssetsSnapshot": ai_assets_snapshot,
                "failedPeriodDetails": [item for item in results if item.get("status") == "failed"],
            },
        )
        return {
            "runId": run_id,
            "dailyPipeline": daily_result,
            "periods": results,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "cancelled": True,
            "status": "cancelled",
        }
    except Exception as error:
        _update_history_backfill_metadata(
            paths,
            run_id,
            {
                "orchestrationError": _safe_history_backfill_error(error),
                "dailyPipeline": daily_result,
                "aiAssetsSnapshot": ai_assets_snapshot,
                "failedPeriodDetails": [item for item in results if item.get("status") == "failed"],
            },
        )
        _finalize_history_backfill(paths, run_id)
        raise


def due_scheduled_history_backfills(paths: RuntimePaths, now: datetime | None = None) -> list[dict]:
    migrate(paths)
    timezone = resolve_timezone(paths)
    current = now.astimezone(timezone) if now else business_now(paths)
    due: list[dict] = []
    for run in list_ingestion_runs(paths, trigger_types=("dashboard-history-backfill",), limit=100):
        if run.get("status") != "scheduled":
            continue
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        scheduled_at = str(metadata.get("scheduledAt") or "")
        if not scheduled_at:
            continue
        try:
            scheduled_time = datetime.fromisoformat(scheduled_at)
        except ValueError:
            continue
        if scheduled_time.tzinfo is None:
            scheduled_time = scheduled_time.replace(tzinfo=timezone)
        else:
            scheduled_time = scheduled_time.astimezone(timezone)
        if scheduled_time <= current:
            due.append(run)
    return due


def run_due_scheduled_history_backfills(paths: RuntimePaths, now: datetime | None = None) -> list[dict]:
    results: list[dict] = []
    for run in due_scheduled_history_backfills(paths, now=now):
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        start_raw = metadata.get("periodStart")
        end_raw = metadata.get("periodEnd")
        if not start_raw or not end_raw:
            continue
        result = run_history_backfill(
            paths,
            int(run["id"]),
            start_date=date.fromisoformat(str(start_raw)),
            end_date=date.fromisoformat(str(end_raw)),
            grain=str(metadata.get("grain") or "both"),
            include_summaries=bool(metadata.get("includeSummaries")),
            skip_ready=metadata.get("skipReady", True) is not False,
            overwrite_daily=bool(metadata.get("overwriteDaily")),
            periods=metadata.get("periods") if isinstance(metadata.get("periods"), list) else None,
        )
        results.append(result)
    return results


def queue_failed_history_backfill_retry(paths: RuntimePaths, source_run_id: int) -> int:
    source = projection_refresh_status(paths, source_run_id)
    if source is None or source.get("trigger_type") != "dashboard-history-backfill":
        raise ValueError("history backfill run not found")
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    if metadata.get("outcomeSchemaVersion") == HISTORY_BACKFILL_OUTCOME_SCHEMA_VERSION:
        retry_stages = _normalize_history_stage_descriptors(metadata.get("retryStages"))
        if not retry_stages:
            raise ValueError("history backfill run has no retryable stages")
        retry_periods: list[dict] = []
        for stage in retry_stages:
            if stage["kind"] == "daily":
                retry_periods.append(
                    {
                        "kind": "day",
                        "start": stage["date"],
                        "end": stage["date"],
                        "label": stage["date"],
                        "daily": True,
                    }
                )
            elif stage["kind"] == "period":
                retry_periods.append(stage["period"])
        periods = _dedupe_history_periods(_normalize_history_periods(retry_periods))
    else:
        periods = _failed_history_retry_periods(metadata)
        if not periods:
            raise ValueError("history backfill run has no failed periods")
        retry_stages = _history_requested_stages(periods)
    start_raw = metadata.get("periodStart") or (periods[0]["start"] if periods else None)
    end_raw = metadata.get("periodEnd") or (periods[-1]["end"] if periods else None)
    if not start_raw or not end_raw:
        raise ValueError("history backfill retry is missing its source date envelope")
    start_date = date.fromisoformat(str(start_raw))
    end_date = date.fromisoformat(str(end_raw))
    return queue_history_backfill(
        paths,
        start_date,
        end_date,
        grain=str(metadata.get("grain") or "both"),
        include_summaries=bool(metadata.get("includeSummaries")),
        skip_ready=metadata.get("skipReady", True) is not False,
        overwrite_daily=bool(metadata.get("overwriteDaily")),
        periods=periods,
        source_run_id=source_run_id,
        requested_stages=retry_stages,
    )


def _failed_history_retry_periods(metadata: dict) -> list[dict]:
    failed_periods = metadata.get("failedPeriodDetails") if isinstance(metadata.get("failedPeriodDetails"), list) else []
    periods = _normalize_history_periods(failed_periods)
    source_periods = _normalize_history_periods(metadata.get("periods") if isinstance(metadata.get("periods"), list) else [])
    daily = metadata.get("dailyPipeline") if isinstance(metadata.get("dailyPipeline"), dict) else {}
    failed_days = daily.get("failed") if isinstance(daily.get("failed"), list) else []
    for item in failed_days:
        raw_day = item.get("date") if isinstance(item, dict) else item
        try:
            day = date.fromisoformat(str(raw_day))
        except (TypeError, ValueError):
            continue
        matching = [
            period
            for period in source_periods
            if date.fromisoformat(period["start"]) <= day <= date.fromisoformat(period["end"])
        ]
        periods.extend(matching or [{"kind": "day", "start": day.isoformat(), "end": day.isoformat(), "label": day.isoformat()}])
    return _dedupe_history_periods(periods)


def _dedupe_history_periods(periods: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for period in periods:
        key = (str(period.get("kind")), str(period.get("start")), str(period.get("end")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(period)
    return sorted(deduped, key=lambda item: (item["start"], item["end"], item["kind"]))


def _normalize_history_periods(periods: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in periods:
        if not isinstance(item, dict):
            continue
        start = date.fromisoformat(str(item.get("start")))
        end = date.fromisoformat(str(item.get("end")))
        if end < start:
            raise ValueError("period end must be on or after start")
        kind = str(item.get("kind") or ("month" if start.day == 1 and end == _last_day_of_month(start) else "week"))
        normalized.append(
            {
                "kind": kind,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": (end - start).days + 1,
                "label": str(item.get("label") or f"{start.isoformat()}..{end.isoformat()}"),
                "daily": item.get("daily", True) is not False,
            }
        )
    normalized.sort(key=lambda item: (item["end"], item["kind"]))
    return normalized


def _history_backfill_pending_items(
    paths: RuntimePaths | None,
    periods: list[dict],
    dates: list[date],
    *,
    include_summaries: bool,
    skip_ready: bool = True,
) -> list[dict]:
    if paths is None:
        return [
            {"kind": "diary", "label": _history_diary_label(day), "date": day.isoformat(), "llmCalls": 3}
            for day in dates
        ] + _history_summary_pending_items(None, periods, include_summaries=include_summaries)
    items: list[dict] = []
    for day in dates:
        status = evaluate_daily_completeness(paths, day)
        if skip_ready and status["ready"]:
            continue
        missing = [] if not skip_ready else status["missingItems"]
        actions = ["daily-full"] if not skip_ready else status["plannedActions"]
        llm_calls = 3 if not skip_ready and not status.get("isBlankDay") else int(status.get("llmCalls") or 0)
        if actions:
            items.append(
                {
                    "kind": "diary",
                    "label": _history_diary_label(day),
                    "date": day.isoformat(),
                    "llmCalls": llm_calls,
                    "missingItems": missing,
                    "missingLabels": [item["label"] for item in missing],
                    "plannedActions": actions,
                    "ready": status["ready"],
                    "isBlankDay": status["isBlankDay"],
                    "overwrite": not skip_ready and status.get("existingData"),
                }
            )
    items.extend(_history_summary_pending_items(paths.diary_dir if paths is not None else None, periods, include_summaries=include_summaries))
    return items


def _history_backfill_existing_diary_days(paths: RuntimePaths | None, dates: list[date]) -> list[date]:
    if paths is None:
        return []
    return [day for day in dates if evaluate_daily_completeness(paths, day).get("existingData")]


def _pipeline_language_profile(paths: RuntimePaths) -> str:
    settings = ensure_settings(paths)
    pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    return str(pipeline.get("languageProfile") or "zh")


def _daily_report_paths(paths: RuntimePaths, business_date: date, report_type: str) -> list:
    return diary_report_paths(paths.diary_dir, business_date, report_type, language_profile=_pipeline_language_profile(paths))


def _daily_diary_complete(paths: RuntimePaths, business_date: date) -> bool:
    return bool(evaluate_daily_completeness(paths, business_date).get("ready"))


def _daily_foundation_inputs_reusable(paths: RuntimePaths, business_date: date) -> bool:
    try:
        return daily_diary_usage_metrics(paths, business_date) is not None
    except Exception:
        return False


def _history_summary_pending_items(
    diary_root,
    periods: list[dict],
    *,
    include_summaries: bool,
) -> list[dict]:
    if not include_summaries:
        return []
    items: list[dict] = []
    for period in periods:
        if period.get("kind") not in {"week", "month"}:
            continue
        period_start = date.fromisoformat(period["start"])
        period_end = date.fromisoformat(period["end"])
        label = "月报" if period["kind"] == "month" else "周报"
        report_path = period_report_path(diary_root, period_start, period_end, label=label) if diary_root is not None else None
        items.append(
            {
                "kind": "month-summary" if period["kind"] == "month" else "week-summary",
                "label": _history_summary_label(period),
                "start": period["start"],
                "end": period["end"],
                "llmCalls": 1,
                "overwrite": bool(report_path is not None and report_path.is_file()),
            }
        )
    return items


def _history_diary_label(day: date) -> str:
    return f"diary-{day.month:02d}-{day.day:02d}"


def _history_summary_label(period: dict) -> str:
    if period["kind"] == "month":
        return f"{period.get('label') or period['start'][:7]}月报"
    match = str(period.get("label") or "").split("-")[-1]
    return f"{match}周报" if match.startswith("W") else f"{period.get('label') or period['start']}周报"


def _history_progress(done: int, total: int) -> int:
    if total <= 0:
        return 100
    return max(5, min(99, int(done * 90 / total) + 5))


def _run_history_daily_pipelines(
    paths: RuntimePaths,
    run_id: int,
    requested_stages: list[dict],
    *,
    skip_ready: bool,
    overwrite_daily: bool = False,
    runner: Callable[[str, RuntimePaths], object] | None = None,
) -> dict:
    daily_stages = [stage for stage in requested_stages if stage.get("kind") == "daily"]
    completed: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    if not daily_stages:
        return {"total": 0, "completed": [], "skipped": [], "failed": []}
    pipeline_runner = runner or _default_history_daily_pipeline_runner
    for index, descriptor in enumerate(daily_stages, start=1):
        day = date.fromisoformat(descriptor["date"])
        if _history_backfill_cancel_requested(paths, run_id):
            return {"total": len(daily_stages), "completed": completed, "skipped": skipped, "failed": failed, "cancelled": True}
        completeness = evaluate_daily_completeness(paths, day)
        has_complete_diary = bool(completeness.get("ready"))
        actions = ["daily-full"] if not skip_ready else list(completeness.get("plannedActions") or [])
        reuse_foundation_inputs = (bool(completeness.get("existingData")) and overwrite_daily) or _daily_foundation_inputs_reusable(paths, day)
        daily_payload = lambda: {
            "total": len(daily_stages),
            "completed": list(completed),
            "skipped": list(skipped),
            "failed": list(failed),
        }
        claimed = _claim_history_backfill_stage(
            paths,
            run_id,
            descriptor,
            progress=max(5, min(25, int(index * 20 / len(daily_stages)) + 5)),
            stage="history-daily-pipeline",
            stage_label=f"Running daily actions for {day.isoformat()}",
            extra={
                "currentDailyPipelineDate": day.isoformat(),
                "currentDailyPipelineActions": actions,
                "dailyPipeline": daily_payload(),
            },
        )
        if not claimed:
            return {**daily_payload(), "cancelled": True}
        if skip_ready and has_complete_diary:
            skipped.append(day.isoformat())
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                descriptor,
                status="skipped",
                artifact_committed=False,
                details={"date": day.isoformat(), "reason": "daily completeness already ready"},
                extra={"dailyPipeline": daily_payload()},
            )
            continue
        if not actions:
            skipped.append(day.isoformat())
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                descriptor,
                status="skipped",
                artifact_committed=False,
                details={"date": day.isoformat(), "reason": "no planned daily actions"},
                extra={"dailyPipeline": daily_payload()},
            )
            continue
        if bool(completeness.get("existingData")) and not skip_ready and not overwrite_daily:
            error = "daily diary already exists; overwrite confirmation required"
            failed.append({"date": day.isoformat(), "error": error})
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                descriptor,
                status="failed",
                artifact_committed=False,
                error=error,
                details={"date": day.isoformat(), "actions": actions},
                extra={"dailyPipeline": daily_payload()},
            )
            continue
        outcome_status = "completed"
        outcome_error = None
        try:
            result = (
                _run_history_daily_actions(
                    day,
                    paths,
                    actions,
                    reuse_foundation_inputs=reuse_foundation_inputs,
                    cancellation_requested=lambda: _history_backfill_cancel_requested(paths, run_id),
                )
                if runner is None
                else pipeline_runner(day.isoformat(), paths)
            )
            if getattr(result, "success", True) is False:
                outcome_error = _safe_history_backfill_error(
                    getattr(result, "failed_step", None) or "daily pipeline failed"
                )
                outcome_status = "cancelled" if _history_backfill_cancel_requested(paths, run_id) else "failed"
                if outcome_status == "failed":
                    failed.append({"date": day.isoformat(), "error": outcome_error})
            else:
                completed.append(day.isoformat())
        except Exception as error:
            outcome_error = _safe_history_backfill_error(error)
            outcome_status = "cancelled" if _history_backfill_cancel_requested(paths, run_id) else "failed"
            if outcome_status == "failed":
                failed.append({"date": day.isoformat(), "error": outcome_error})
        _record_history_backfill_stage_outcome(
            paths,
            run_id,
            descriptor,
            status=outcome_status,
            artifact_committed=outcome_status == "completed",
            error=outcome_error,
            details={"date": day.isoformat(), "actions": actions},
            extra={"dailyPipeline": daily_payload()},
        )
        if _history_backfill_cancel_requested(paths, run_id):
            return {**daily_payload(), "cancelled": True}
    return daily_payload()


def _default_history_daily_pipeline_runner(
    day: str,
    paths: RuntimePaths,
    *,
    reuse_foundation_inputs: bool = False,
    cancellation_requested: Callable[[], bool] | None = None,
) -> object:
    from .pipeline import run_daily_pipeline

    trigger = "history-backfill-frozen" if reuse_foundation_inputs else "history-backfill"
    return run_daily_pipeline(
        day,
        paths=paths,
        trigger=trigger,
        reuse_foundation_inputs=reuse_foundation_inputs,
        cancellation_requested=cancellation_requested,
    )


def _run_history_daily_actions(
    day: date,
    paths: RuntimePaths,
    actions: list[str],
    *,
    reuse_foundation_inputs: bool,
    cancellation_requested: Callable[[], bool] | None = None,
) -> object:
    from .pipeline import (
        materialize_blank_day_pipeline_outputs,
        materialize_nova_task_outputs,
        materialize_pipeline_foundation_outputs,
        production_steps_for_language,
        resolve_pipeline_settings,
        run_daily_pipeline,
    )

    action_set = set(actions)
    day_str = day.isoformat()
    nova_task_requested = "nova-task-work-graph" in action_set

    def finalize_result(result: object, *, already_attempted: bool = False) -> object:
        if getattr(result, "success", True) is False or not nova_task_requested:
            return result
        nova_task_ready = (
            bool(evaluate_daily_completeness(paths, day).get("novaTaskUpdated"))
            if already_attempted
            else materialize_nova_task_outputs(day_str, paths)
        )
        if nova_task_ready:
            return result
        return SimpleNamespace(success=False, failed_step="Nova-Task Work Graph")

    if "daily-full" in action_set:
        result = _default_history_daily_pipeline_runner(
            day_str,
            paths,
            reuse_foundation_inputs=reuse_foundation_inputs,
            cancellation_requested=cancellation_requested,
        )
        return finalize_result(result, already_attempted=True)
    if action_set == {"daily-blank-materialization"}:
        ok = materialize_blank_day_pipeline_outputs(day_str, paths)
        return SimpleNamespace(success=ok, failed_step=None if ok else "Blank-Day Foundation Materialization")
    if action_set == {"daily-materialization"}:
        ok = materialize_pipeline_foundation_outputs(day_str, paths)
        return SimpleNamespace(success=ok, failed_step=None if ok else "Pipeline Foundation Materialization")
    settings = resolve_pipeline_settings(paths)
    steps = production_steps_for_language(str(settings.get("languageProfile") or "zh"))
    selected = []
    if "technical-pass" in action_set:
        selected.extend(step for step in steps if step.script.name == "technical_pass.py")
    if "learning-pass" in action_set:
        selected.extend(step for step in steps if step.script.name == "learning_pass.py")
    if "daily-materialization" in action_set and "rag-sync" not in action_set:
        ok = materialize_pipeline_foundation_outputs(day_str, paths)
        result = SimpleNamespace(success=ok, failed_step=None if ok else "Pipeline Foundation Materialization")
        return finalize_result(result)
    if "rag-sync" in action_set or "daily-materialization" in action_set:
        selected.extend(step for step in steps if step.script.name == "rag_v2_sync.py")
    if not selected:
        return finalize_result(SimpleNamespace(success=True, failed_step=None))
    deduped = []
    seen = set()
    for step in selected:
        key = step.name
        if key in seen:
            continue
        seen.add(key)
        deduped.append(step)
    return finalize_result(
        run_daily_pipeline(
            day_str,
            paths=paths,
            trigger="history-backfill-partial",
            steps=tuple(deduped),
            reuse_foundation_inputs=reuse_foundation_inputs,
            cancellation_requested=cancellation_requested,
        ),
        already_attempted="technical-pass" in action_set,
    )


def _ensure_blank_day_weather(paths: RuntimePaths, business_date: date, markdown_paths: tuple[Path, ...]) -> None:
    missing: list[tuple[Path, str]] = []
    for path in markdown_paths:
        if not path.name.endswith("-no-activity.md") or not path.exists():
            continue
        markdown = path.read_text(encoding="utf-8")
        if "## 天气" not in markdown and "## Weather" not in markdown:
            missing.append((path, markdown))
    if not missing:
        return
    weather = fetch_weather_for_date(business_date.isoformat(), paths=paths)
    heading = "## Weather" if _pipeline_language_profile(paths) == "en" else "## 天气"
    for path, markdown in missing:
        lines = markdown.splitlines()
        insert_at = 1 if lines and lines[0].startswith("# ") else 0
        lines[insert_at:insert_at] = ["", heading, weather]
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _history_backfill_dates(periods: list[dict]) -> list[date]:
    days: set[date] = set()
    for period in periods:
        if period.get("daily") is False:
            continue
        start = date.fromisoformat(str(period["start"]))
        end = date.fromisoformat(str(period["end"]))
        current = start
        while current <= end:
            days.add(current)
            current += timedelta(days=1)
    return sorted(days)


def _history_backfill_periods(start_date: date, end_date: date, *, grain: str) -> list[dict]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if (end_date - start_date).days > 3660:
        raise ValueError("history backfill range is too large")
    normalized = str(grain or "both").lower()
    if normalized not in {"week", "month", "both"}:
        raise ValueError("grain must be week, month, or both")
    periods: list[dict] = []
    if normalized in {"week", "both"}:
        periods.extend(_weekly_history_periods(start_date, end_date))
    if normalized in {"month", "both"}:
        periods.extend(_monthly_history_periods(start_date, end_date))
    periods.sort(key=lambda item: (item["end"], item["kind"]))
    return periods


def _weekly_history_periods(start_date: date, end_date: date) -> list[dict]:
    first_week_end = start_date + timedelta(days=(6 - start_date.weekday()) % 7)
    periods: list[dict] = []
    current_end = first_week_end
    while current_end <= end_date:
        current_start = current_end - timedelta(days=6)
        periods.append(
            {
                "kind": "week",
                "start": current_start.isoformat(),
                "end": current_end.isoformat(),
                "days": 7,
                "label": f"{current_end.isocalendar().year}-W{current_end.isocalendar().week:02d}",
            }
        )
        current_end += timedelta(days=7)
    return periods


def _monthly_history_periods(start_date: date, end_date: date) -> list[dict]:
    periods: list[dict] = []
    current = start_date.replace(day=1)
    while current <= end_date:
        month_end = _last_day_of_month(current)
        if month_end <= end_date:
            periods.append(
                {
                    "kind": "month",
                    "start": current.isoformat(),
                    "end": month_end.isoformat(),
                    "days": month_end.day,
                    "label": current.strftime("%Y-%m"),
                }
            )
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return periods


def run_pipeline_daily_materialization(
    paths: RuntimePaths,
    business_date: date,
    *,
    ai_assets_builder: Callable[[], dict] | None = None,
    period_builder: Callable[[date, int], dict] | None = None,
) -> dict:
    """Materialize daily Dashboard/Foundation projections from the stable pipeline."""
    migrate(paths)
    week_start = business_date - timedelta(days=business_date.weekday())
    month_start = business_date.replace(day=1)
    run_id = begin_ingestion_run(
        paths,
        trigger_type="pipeline-foundation-materialization",
        business_date=business_date,
        adapter_versions={
            "projection": "pipeline-owned-foundation-materialization-v1",
            "scope": "daily-dashboard-foundation",
            "businessDate": business_date.isoformat(),
            "weekStart": week_start.isoformat(),
            "monthStart": month_start.isoformat(),
            "periodEnd": business_date.isoformat(),
        },
        status="running",
    )
    periods: list[dict] = []
    try:
        _set_refresh_progress(
            paths,
            run_id,
            progress=10,
            stage="diary-markdown-day",
            stage_label="Materializing daily diary Markdown",
        )
        day_markdown = materialize_diary_markdown_day(paths, business_date, source_run_id=run_id)
        _set_refresh_progress(
            paths,
            run_id,
            progress=30,
            stage="ai-assets-snapshot",
            stage_label="Refreshing AI Assets usage cache and snapshot",
        )
        dashboard_snapshot_key = materialize_ai_assets_non_rag_snapshot(
            paths,
            run_id,
            builder=ai_assets_builder,
            business_date=business_date,
        )
        workspace_catalog = materialize_workspace_attribution_catalog(paths)
        _set_refresh_progress(
            paths,
            run_id,
            progress=50,
            stage="ai-assets-snapshot-ready",
            stage_label="AI Assets snapshot ready",
            extra={"usageCache": _latest_ai_assets_usage_cache(paths), "workspaceAttribution": workspace_catalog.get("counts", {})},
        )
        for label, period_start in (("current-week", week_start), ("current-month", month_start)):
            period_days = (business_date - period_start).days + 1
            _set_refresh_progress(
                paths,
                run_id,
                progress=65 if label == "current-week" else 82,
                stage=f"{label}-period-assets",
                stage_label=f"Materializing {label} period projections",
            )
            materialize_legacy_asset_projection(
                paths,
                period_start,
                business_date,
                run_id,
                builder=period_builder,
            )
            period_markdown = materialize_diary_markdown_period_documents(
                paths,
                period_start,
                business_date,
                source_run_id=run_id,
            )
            page_key = materialize_diary_period_page_snapshot(
                paths,
                period_start,
                business_date,
                source_run_id=run_id,
            )
            periods.append(
                {
                    "label": label,
                    "start": period_start.isoformat(),
                    "end": business_date.isoformat(),
                    "days": period_days,
                    "markdownDocuments": period_markdown["documents"],
                    "pageProjection": page_key,
                }
            )
        completed_summaries = materialize_due_period_summaries(paths, business_date, period_builder=period_builder)
        _set_refresh_progress(paths, run_id, progress=100, stage="completed", stage_label="Refresh completed")
        finish_ingestion_run(paths, run_id, status="completed")
        return {
            "runId": run_id,
            "businessDate": business_date.isoformat(),
            "diaryMarkdownDocuments": day_markdown["documents"],
            "dashboardSnapshot": dashboard_snapshot_key,
            "periods": periods,
            "completedPeriodSummaries": completed_summaries,
        }
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise


def run_pipeline_blank_day_materialization(paths: RuntimePaths, business_date: date) -> dict:
    """Materialize only the daily diary document for a no-activity business day."""
    migrate(paths)
    run_id = begin_ingestion_run(
        paths,
        trigger_type="pipeline-blank-day-materialization",
        business_date=business_date,
        adapter_versions={
            "projection": "pipeline-owned-foundation-materialization-v1",
            "scope": "blank-day-diary-only",
            "businessDate": business_date.isoformat(),
        },
        status="running",
    )
    try:
        _set_refresh_progress(
            paths,
            run_id,
            progress=40,
            stage="diary-markdown-day",
            stage_label="Materializing blank-day diary Markdown",
        )
        diary_root = paths.diary_dir
        narrative_prefix = diary_report_prefix("narrative", _pipeline_language_profile(paths))
        markdown_paths = diary_markdown_paths(diary_root, business_date, f"{narrative_prefix}-*-no-activity.md") if diary_root else ()
        if not markdown_paths:
            raise RuntimeError(f"blank-day no-activity markdown not found for {business_date.isoformat()}")
        _ensure_blank_day_weather(paths, business_date, markdown_paths)
        day_markdown = materialize_diary_markdown_day(
            paths,
            business_date,
            source_run_id=run_id,
            markdown_paths=markdown_paths,
        )
        _set_refresh_progress(paths, run_id, progress=100, stage="completed", stage_label="Blank-day refresh completed")
        finish_ingestion_run(paths, run_id, status="completed")
        return {
            "runId": run_id,
            "businessDate": business_date.isoformat(),
            "diaryMarkdownDocuments": day_markdown["documents"],
            "dashboardSnapshot": "skipped:blank-day",
            "periods": [],
            "completedPeriodSummaries": [],
        }
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise


def projection_refresh_status(paths: RuntimePaths, run_id: int) -> dict | None:
    row = ingestion_run_status(paths, run_id)
    if row is None or row["trigger_type"] not in {
        "dashboard-projection-refresh",
        "dashboard-period-summary-refresh",
        "dashboard-history-backfill",
        "pipeline-foundation-materialization",
    }:
        return None
    if row["trigger_type"] == "dashboard-history-backfill":
        row = _reconcile_orphaned_history_backfill(paths, row)
    return row


def recent_projection_refresh_jobs(paths: RuntimePaths, *, limit: int = 20) -> list[dict]:
    migrate(paths)
    jobs = list_ingestion_runs(
        paths,
        trigger_types=(
            "dashboard-projection-refresh",
            "dashboard-period-summary-refresh",
            "dashboard-history-backfill",
            "pipeline-foundation-materialization",
        ),
        limit=limit,
    )
    return [
        _reconcile_orphaned_history_backfill(paths, job)
        if job.get("trigger_type") == "dashboard-history-backfill"
        else job
        for job in jobs
    ]


def _reconcile_orphaned_history_backfill(paths: RuntimePaths, run: dict) -> dict:
    if not _history_backfill_orphaned(paths, run):
        return run
    run_id = int(run["id"])
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    if metadata.get("outcomeSchemaVersion") == HISTORY_BACKFILL_OUTCOME_SCHEMA_VERSION:
        now = datetime.now().astimezone().isoformat()
        _update_history_backfill_metadata(
            paths,
            run_id,
            {
                "orphaned": True,
                "orphanedAt": now,
                "currentStage": "history-backfill-orphaned",
                "currentStageLabel": "History backfill worker exited unexpectedly",
            },
        )
        current_stage_id = str(metadata.get("currentStageId") or "")
        requested = {
            stage["id"]: stage
            for stage in _normalize_history_stage_descriptors(metadata.get("requestedStages"))
        }
        outcomes = metadata.get("stageOutcomes") if isinstance(metadata.get("stageOutcomes"), dict) else {}
        if current_stage_id in requested and current_stage_id not in outcomes:
            _record_history_backfill_stage_outcome(
                paths,
                run_id,
                requested[current_stage_id],
                status="failed",
                artifact_committed=False,
                error="history backfill worker exited without completing the claimed stage",
                details={"orphaned": True},
            )
        elif not current_stage_id:
            _update_history_backfill_metadata(
                paths,
                run_id,
                {"orchestrationError": "History backfill worker exited unexpectedly"},
            )
        _finalize_history_backfill(paths, run_id)
        refreshed = ingestion_run_status(paths, run_id)
        return refreshed if refreshed is not None else run
    failed_day = _history_backfill_current_daily_date(metadata)
    daily = metadata.get("dailyPipeline") if isinstance(metadata.get("dailyPipeline"), dict) else {}
    failed = list(daily.get("failed") or [])
    if failed_day and not any((item or {}).get("date") == failed_day for item in failed if isinstance(item, dict)):
        failed.append({"date": failed_day, "error": "daily pipeline worker exited without completing"})
    update_ingestion_run_metadata(
        paths,
        run_id,
        {
            "orphaned": True,
            "orphanedAt": datetime.now().astimezone().isoformat(),
            "currentStage": "history-backfill-orphaned",
            "currentStageLabel": "History backfill worker exited unexpectedly",
            "dailyPipeline": {**daily, "failed": failed},
            "failedDailyPipelineDays": len(failed),
        },
    )
    finish_ingestion_run(
        paths,
        run_id,
        status="partial" if failed else "failed",
        error_summary="History backfill worker exited unexpectedly",
    )
    refreshed = ingestion_run_status(paths, run_id)
    return refreshed if refreshed is not None else {**run, "status": "partial" if failed else "failed"}


def _history_backfill_orphaned(paths: RuntimePaths, run: dict) -> bool:
    status = str(run.get("status") or "")
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    if status == "queued":
        if metadata.get("scheduledAt"):
            return False
        if metadata.get("currentStage"):
            return False
        started_at = _parse_iso_datetime(str(run.get("started_at") or ""))
        if started_at is None:
            return False
        now = datetime.now(started_at.tzinfo).astimezone(started_at.tzinfo) if started_at.tzinfo else datetime.now()
        return now - started_at >= STALE_QUEUED_HISTORY_BACKFILL_AFTER
    if status not in {"running", "cancel_requested"}:
        return False
    if str(metadata.get("currentStage") or "") == "history-daily-pipeline":
        current_day = _history_backfill_current_daily_date(metadata)
        if current_day:
            lock_path = paths.state_dir / "locks" / f"daily-pipeline-{current_day}.lock"
            pid = _lock_pid(lock_path)
            if pid is not None and not _pid_running(pid):
                return True
    worker_pid = metadata.get("workerPid")
    try:
        parsed_worker_pid = int(worker_pid) if worker_pid is not None else None
    except (TypeError, ValueError):
        parsed_worker_pid = None
    if parsed_worker_pid is not None and not _pid_running(parsed_worker_pid):
        return True
    heartbeat = _parse_iso_datetime(
        str(metadata.get("heartbeatAt") or metadata.get("stageClaimedAt") or run.get("started_at") or "")
    )
    if heartbeat is None:
        return False
    now = datetime.now(heartbeat.tzinfo).astimezone(heartbeat.tzinfo) if heartbeat.tzinfo else datetime.now()
    return now - heartbeat >= STALE_RUNNING_HISTORY_BACKFILL_AFTER


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _history_backfill_current_daily_date(metadata: dict) -> str:
    current = str(metadata.get("currentDailyPipelineDate") or "")
    if current:
        return current
    label = str(metadata.get("currentStageLabel") or "")
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", label)
    return match.group(1) if match else ""


def _lock_pid(lock_path: Path) -> int | None:
    try:
        text = lock_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^pid=(\d+)\s*$", text, flags=re.MULTILINE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
