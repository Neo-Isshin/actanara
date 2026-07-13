"""Dashboard boundary for asynchronous Foundation projection refresh jobs."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from data_foundation.diary_markdown import DIARY_PERIOD_PAGE_PROJECTION
from data_foundation.paths import initialize_home, load_paths
from data_foundation.period_summary import DIARY_PERIOD_SUMMARY_PROJECTION
from data_foundation.refresh import (
    cancel_history_backfill,
    due_scheduled_history_backfills,
    plan_history_backfill,
    projection_refresh_status,
    queue_failed_history_backfill_retry,
    queue_history_backfill,
    queue_period_summary_refresh,
    queue_projection_refresh,
    recent_projection_refresh_jobs,
    run_history_backfill,
    run_due_scheduled_history_backfills,
    run_period_summary_refresh,
    run_projection_refresh,
)
from data_foundation.reports import read_period_projection
from data_foundation.settings import RUNTIME_SOURCE_FIELDS, is_nova_task_enabled, resolve_runtime_source
from data_foundation.snapshots import read_dashboard_snapshot, read_diary_tasks_snapshot
from data_foundation.tasks import record_authoritative_board_mutation


def _dashboard_paths():
    return load_paths()


def _dashboard_write_paths():
    selected = load_paths()
    initialize_home(selected.home, legacy_diary_root=selected.legacy_diary_root)
    return load_paths()


def queue_refresh(business_date: date, *, period_start: date | None = None) -> int:
    return queue_projection_refresh(_dashboard_write_paths(), business_date, period_start=period_start)


def execute_refresh(run_id: int, *, period_start: date | None = None, period_days: int | None = None) -> None:
    run_projection_refresh(_dashboard_write_paths(), run_id, period_start=period_start, period_days=period_days)
    from . import ai_assets

    ai_assets._cache["data"] = None
    ai_assets._cache["ts"] = 0


def queue_period_summary(business_date: date, *, period_start: date) -> int:
    return queue_period_summary_refresh(_dashboard_write_paths(), business_date, period_start=period_start)


def execute_period_summary(run_id: int, *, period_start: date, period_days: int) -> None:
    run_period_summary_refresh(_dashboard_write_paths(), run_id, period_start=period_start, period_days=period_days)


def plan_history_backfill_request(
    start_date: date,
    end_date: date,
    *,
    grain: str,
    include_summaries: bool,
    skip_ready: bool = True,
    periods: list[dict] | None = None,
) -> dict:
    return plan_history_backfill(
        start_date,
        end_date,
        grain=grain,
        include_summaries=include_summaries,
        skip_ready=skip_ready,
        periods=periods,
        paths=_dashboard_paths(),
    )


def queue_history_backfill_request(
    start_date: date,
    end_date: date,
    *,
    grain: str,
    include_summaries: bool,
    skip_ready: bool,
    overwrite_daily: bool = False,
    scheduled_at: str | None = None,
    periods: list[dict] | None = None,
) -> int:
    return queue_history_backfill(
        _dashboard_write_paths(),
        start_date,
        end_date,
        grain=grain,
        include_summaries=include_summaries,
        skip_ready=skip_ready,
        overwrite_daily=overwrite_daily,
        scheduled_at=scheduled_at,
        periods=periods,
        require_llm_ready=True,
    )


def execute_history_backfill(
    run_id: int,
    *,
    start_date: date,
    end_date: date,
    grain: str,
    include_summaries: bool,
    skip_ready: bool,
    overwrite_daily: bool = False,
    periods: list[dict] | None = None,
) -> None:
    run_history_backfill(
        _dashboard_write_paths(),
        run_id,
        start_date=start_date,
        end_date=end_date,
        grain=grain,
        include_summaries=include_summaries,
        skip_ready=skip_ready,
        overwrite_daily=overwrite_daily,
        periods=periods,
    )
    from . import ai_assets

    ai_assets._cache["data"] = None
    ai_assets._cache["ts"] = 0


def execute_due_scheduled_history_backfills() -> list[dict]:
    result = run_due_scheduled_history_backfills(_dashboard_write_paths())
    if result:
        from . import ai_assets

        ai_assets._cache["data"] = None
        ai_assets._cache["ts"] = 0
    return result


def list_due_scheduled_history_backfills() -> list[dict]:
    return due_scheduled_history_backfills(_dashboard_paths())


def queue_failed_history_backfill_retry_request(source_run_id: int) -> int:
    return queue_failed_history_backfill_retry(_dashboard_write_paths(), source_run_id)


def cancel_history_backfill_request(run_id: int) -> dict:
    return cancel_history_backfill(_dashboard_write_paths(), run_id)


def get_refresh_status(run_id: int) -> dict | None:
    return projection_refresh_status(_dashboard_paths(), run_id)


def list_refresh_jobs(*, limit: int = 20) -> dict:
    paths = _dashboard_paths()
    jobs = recent_projection_refresh_jobs(paths, limit=limit)
    return {
        "runtime": {
            "novaHome": str(paths.home),
            "database": str(paths.db_path),
            "databaseExists": paths.db_path.exists(),
        },
        "jobs": jobs,
        "latest": jobs[0] if jobs else None,
        "latestFailed": next((job for job in jobs if job["status"] == "failed"), None),
    }


def task_mutation_audit_enabled() -> bool:
    return resolve_runtime_source("TASK_AUDIT_SINK", _dashboard_paths()) == "foundation"


def nova_task_enabled() -> bool:
    return is_nova_task_enabled(_dashboard_paths())


def audit_task_board_mutation(*, board_path, content: str, done: bool, before_content: str, after_content: str) -> int | None:
    """Record enabled Dashboard user mutations without taking ownership of the Markdown writer."""
    if not task_mutation_audit_enabled():
        return None
    return record_authoritative_board_mutation(
        load_paths(),
        board_path,
        requested_content=content,
        requested_done=done,
        before_content=before_content,
        after_content=after_content,
    )


def _projection_readiness(loader, *, require_memory: bool = False) -> dict:
    try:
        projection = loader()
    except Exception as error:
        return {"ready": False, "status": "unavailable", "error": str(error)}
    if projection is None:
        return {"ready": False, "status": "missing"}
    result = {
        "ready": True,
        "status": projection["status"],
        "projectionType": projection["projectionType"],
        "generatedAt": projection["generatedAt"],
        "sourceRunId": projection["sourceRunId"],
    }
    if require_memory:
        metrics = projection.get("metrics") or {}
        result["memoryReady"] = (
            metrics.get("memoryStats") is not None
            and metrics.get("knowledgePeriodMemoryCurrent") is not None
        )
        result["ready"] = result["ready"] and result["memoryReady"]
        if not result["memoryReady"]:
            result["status"] = "memory_fields_missing"
    return result


def get_reader_readiness(*, period_start: date | None = None, period_days: int | None = None) -> dict:
    """Report whether current materializations are ready before a Foundation flag switch."""
    paths = _dashboard_paths()
    ai_assets = _projection_readiness(lambda: read_dashboard_snapshot(paths))
    period_assets = {"checked": False, "ready": None, "status": "not_requested"}
    period_page = {"checked": False, "ready": None, "status": "not_requested"}
    period_summary = {"checked": False, "ready": None, "status": "not_requested"}
    diary_tasks = {"checked": False, "ready": None, "status": "not_requested"}
    if period_start is not None and period_days is not None:
        period_end = period_start + timedelta(days=period_days - 1)
        period_assets = {
            "checked": True,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": period_days,
            **_projection_readiness(
                lambda: read_period_projection(paths, period_start, period_end),
                require_memory=True,
            ),
        }
        period_page = {
            "checked": True,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": period_days,
            **_projection_readiness(
                lambda: read_period_projection(
                    paths,
                    period_start,
                    period_end,
                    projection_type=DIARY_PERIOD_PAGE_PROJECTION,
                )
            ),
        }
        period_summary = {
            "checked": True,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "days": period_days,
            **_projection_readiness(
                lambda: read_period_projection(
                    paths,
                    period_start,
                    period_end,
                    projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
                )
            ),
        }
        diary_tasks = _diary_tasks_readiness(paths, period_end)
    configured_sources = {
        "aiAssets": resolve_runtime_source("DASHBOARD_READ_SOURCE", paths),
        "periodAssets": resolve_runtime_source("REPORT_READ_SOURCE", paths),
        "diaryMetrics": resolve_runtime_source("DIARY_METRICS_SOURCE", paths),
        "diaryMemory": resolve_runtime_source("DIARY_MEMORY_SOURCE", paths),
        "diaryTasks": resolve_runtime_source("DIARY_TASKS_SOURCE", paths),
        "taskAuditSink": resolve_runtime_source("TASK_AUDIT_SINK", paths),
    }
    source_env_names = {
        "aiAssets": "DASHBOARD_READ_SOURCE",
        "periodAssets": "REPORT_READ_SOURCE",
        "diaryMetrics": "DIARY_METRICS_SOURCE",
        "diaryMemory": "DIARY_MEMORY_SOURCE",
        "diaryTasks": "DIARY_TASKS_SOURCE",
        "taskAuditSink": "TASK_AUDIT_SINK",
    }
    source_settings_fields = {
        surface: RUNTIME_SOURCE_FIELDS[env_name]
        for surface, env_name in source_env_names.items()
    }
    source_values_valid = all(value in {"legacy", "foundation"} for value in configured_sources.values())
    report_ready = (
        bool(period_assets["ready"] and period_page["ready"])
        if period_assets["checked"] and period_page["checked"]
        else None
    )
    diary_metrics_ready = bool(ai_assets["ready"])
    diary_memory_ready = report_ready
    diary_tasks_ready = diary_tasks["ready"]
    task_audit_sink_ready = paths.db_path.exists()
    return {
        "status": "ready" if ai_assets["ready"] and (report_ready is not False) and (diary_tasks_ready is not False) else "not_ready",
        "runtime": {
            "novaHome": str(paths.home),
            "database": str(paths.db_path),
            "databaseExists": paths.db_path.exists(),
        },
        "configuredSources": configured_sources,
        "configuredSourceEnvNames": source_env_names,
        "configuredSourceFields": source_settings_fields,
        "configuredSourcesValid": source_values_valid,
        "preservedSources": {"rag": "v2"},
        "aiAssets": ai_assets,
        "periodAssets": period_assets,
        "periodPage": period_page,
        "periodSummary": period_summary,
        "diaryMetrics": {
            "ready": diary_metrics_ready,
            "status": "ready" if diary_metrics_ready else "dashboard_snapshot_missing",
            "source": "dashboard-snapshot-projection",
            "upstream": "aiAssets",
        },
        "diaryMemory": {
            "ready": diary_memory_ready,
            "status": (
                "ready"
                if diary_memory_ready is True
                else "not_requested"
                if diary_memory_ready is None
                else "period_memory_projection_missing"
            ),
            "source": "period-assets-projection",
            "upstream": "periodAssets",
        },
        "diaryTasks": {
            "ready": diary_tasks_ready,
            "status": diary_tasks["status"],
            "source": "diary-tasks-snapshot",
            "businessDate": diary_tasks.get("businessDate"),
            "upstream": "nova-task-v2-sqlite",
        },
        "taskAuditSink": {
            "ready": task_audit_sink_ready,
            "status": "ready" if task_audit_sink_ready else "database_missing",
            "source": "nova-task-v2-sqlite",
            "additive": True,
        },
        "canEnable": {
            "dashboardReadSourceFoundation": bool(ai_assets["ready"]),
            "reportReadSourceFoundation": report_ready,
            "diaryMetricsSourceFoundation": diary_metrics_ready,
            "diaryMemorySourceFoundation": diary_memory_ready,
            "diaryTasksSourceFoundation": diary_tasks_ready,
            "taskAuditSinkFoundation": task_audit_sink_ready,
        },
    }


def _diary_tasks_readiness(paths, business_date: date) -> dict:
    snapshot = read_diary_tasks_snapshot(paths, business_date)
    if snapshot is None:
        return {
            "checked": True,
            "ready": False,
            "status": "diary_tasks_snapshot_missing",
            "businessDate": business_date.isoformat(),
        }
    return {
        "checked": True,
        "ready": True,
        "status": "ready",
        "businessDate": business_date.isoformat(),
        "generatedAt": snapshot.get("generatedAt"),
        "sourceRunId": snapshot.get("sourceRunId"),
    }
