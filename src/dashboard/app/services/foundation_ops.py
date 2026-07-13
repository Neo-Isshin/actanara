"""Operational projections for Foundation snapshot status.

This module is intentionally mostly pure: callers can pass existing readiness,
refresh job and scheduler payloads without opening the Dashboard app or a DB.
The runtime wrapper at the bottom only composes existing Dashboard services.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from data_foundation.paths import load_paths
from data_foundation.settings import (
    DEFAULT_DASHBOARD_HOST,
    DEFAULT_DASHBOARD_PORT,
    resolve_dashboard_settings,
)
from data_foundation.time import business_now, resolve_timezone_name

from .ui_text import dashboard_language_profile, is_english_profile

PROJECTION_ROWS = (
    {
        "key": "aiAssets",
        "label": "AI assets",
        "requiredFor": ("dashboardReadSourceFoundation",),
        "optional": False,
    },
    {
        "key": "periodAssets",
        "label": "Period assets",
        "requiredFor": ("reportReadSourceFoundation",),
        "optional": False,
    },
    {
        "key": "periodPage",
        "label": "Period page",
        "requiredFor": ("reportReadSourceFoundation",),
        "optional": False,
    },
    {
        "key": "periodSummary",
        "label": "Period summary",
        "requiredFor": (),
        "optional": True,
    },
)

PRODUCTION_READER_SOURCE_FIELDS = (
    "DASHBOARD_READ_SOURCE",
    "REPORT_READ_SOURCE",
    "DIARY_METRICS_SOURCE",
    "DIARY_MEMORY_SOURCE",
    "DIARY_TASKS_SOURCE",
)

DAILY_REQUIRED_DOCUMENT_TYPES = ("narrative", "technical", "learning")
DAILY_REQUIRED_SECTION_PROFILES = {
    "narrative": {
        "zh": (
            {"id": "weather", "label": "天气", "aliases": ("天气",)},
            {"id": "daily_overview", "label": "今日概要", "aliases": ("今日概要",)},
            {"id": "daily_stats", "label": "本日统计", "aliases": ("本日统计",)},
            {"id": "agent_work", "label": "Agent工作", "aliases": ("Agent工作",)},
            {"id": "scheduled_jobs", "label": "定时任务情况", "aliases": ("定时任务情况",), "allowNone": True},
        ),
        "en": (
            {"id": "daily_overview", "label": "Daily Overview", "aliases": ("Daily Overview",)},
            {"id": "agent_work", "label": "Agent Work", "aliases": ("Agent Work",)},
            {"id": "important_notices", "label": "Important Notices", "aliases": ("Important Notices",), "allowNone": True},
            {"id": "scheduled_jobs", "label": "Scheduled Jobs", "aliases": ("Scheduled Jobs",), "allowNone": True},
            {"id": "notes", "label": "Notes", "aliases": ("Notes",), "allowNone": True},
        ),
    },
    "technical": {
        "shared": (
            {"id": "engineering_objectives", "label": "Engineering Objectives and Outcomes", "aliases": ("工程目标与完成结果", "Engineering Objectives and Outcomes")},
            {"id": "obstacles_root_causes", "label": "Obstacles, Root Causes, and Detours", "aliases": ("阻碍、根因与弯路", "Obstacles, Root Causes, and Detours")},
            {"id": "implementation_path", "label": "Implementation Path and Key Decisions", "aliases": ("实现路径与关键决策", "Implementation Path and Key Decisions")},
            {"id": "verification_evidence", "label": "Verification Evidence", "aliases": ("验证证据", "Verification Evidence")},
            {"id": "residual_risks", "label": "Residual Risks and Follow-up Observation", "aliases": ("残余风险与后续观察", "Residual Risks and Follow-up Observation"), "allowNone": True},
            {"id": "reusable_lessons", "label": "Reusable Lessons", "aliases": ("可沉淀经验", "Reusable Lessons"), "allowNone": True},
            {"id": "nova_task_hooks", "label": "Nova-Task Reconciliation Hooks", "aliases": ("Nova-Task Reconciliation Hooks",), "allowNone": True},
        ),
    },
    "learning": {
        "zh": (
            {"id": "lessons", "label": "黄金教训", "aliases": ("黄金教训",)},
            {"id": "infrastructure_updates", "label": "基建变动", "aliases": ("基建变动",)},
        ),
        "en": (
            {"id": "lessons", "label": "Lessons", "aliases": ("Lessons",)},
            {"id": "infrastructure_updates", "label": "Infrastructure Updates", "aliases": ("Infrastructure Updates",), "allowNone": True},
        ),
    },
}
DAILY_REQUIRED_EMBEDDED_KEYS = {
    "narrative": ("metrics", "tasks", "modelUsage"),
}

DASHBOARD_EXECUTABLE_REPAIR_ACTIONS = {
    "run-full-daily-pipeline": {
        "actionClass": "heavy-llm-pipeline",
        "lockPrefix": "daily-pipeline",
    },
    "retry-daily-pipeline": {
        "actionClass": "heavy-llm-pipeline",
        "lockPrefix": "daily-pipeline",
    },
}
REPAIR_OUTPUT_TAIL_CHARS = 12000


def _dashboard_api_base_url() -> str:
    try:
        settings = resolve_dashboard_settings(load_paths())
        public_base_url = str(settings.get("publicBaseUrl") or "").strip().rstrip("/")
        if public_base_url:
            return public_base_url
    except Exception:
        pass
    return f"http://{DEFAULT_DASHBOARD_HOST}:{DEFAULT_DASHBOARD_PORT}"


def build_projection_completeness_matrix(
    readiness: dict | None,
    *,
    require_period_projections: bool = True,
    language_profile: str = "zh",
) -> dict:
    """Normalize reader readiness into UI-friendly completeness rows."""
    readiness = readiness or {}
    rows = [
        _projection_row(
            definition,
            readiness.get(definition["key"]) or {},
            require_period_projection=require_period_projections,
            language_profile=language_profile,
        )
        for definition in PROJECTION_ROWS
    ]
    required_rows = [row for row in rows if not row["optional"]]
    complete_required = sum(1 for row in required_rows if row["complete"])
    complete_total = sum(1 for row in rows if row["complete"])
    return {
        "status": "complete" if required_rows and complete_required == len(required_rows) else "incomplete",
        "requiredComplete": complete_required,
        "requiredTotal": len(required_rows),
        "complete": complete_total,
        "total": len(rows),
        "rows": rows,
        "canEnable": readiness.get("canEnable", {}),
        "configuredSources": readiness.get("configuredSources", {}),
        "configuredSourcesValid": bool(readiness.get("configuredSourcesValid", True)),
        "runtime": readiness.get("runtime", {}),
    }


def build_scheduled_run_cadence(
    scheduler_status: dict | None,
    refresh_jobs: dict | list | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """Build an operator cadence summary from scheduler state and job history."""
    scheduler_status = scheduler_status or {}
    state = scheduler_status.get("state") if isinstance(scheduler_status.get("state"), dict) else {}
    timer = scheduler_status.get("systemTimer") if isinstance(scheduler_status.get("systemTimer"), dict) else {}
    jobs = _coerce_jobs(refresh_jobs)
    dashboard_job = _select_timer_job(timer.get("jobs"), "dashboard-aggregation")
    pipeline_job = _select_timer_job(timer.get("jobs"), "daily-pipeline")
    dashboard_time = dashboard_job.get("time") or _time_from_state(state, "dashboardAggregationTime") or "04:30"
    pipeline_time = pipeline_job.get("time") or _time_from_state(state, "dailyPipelineTime") or "04:00"
    timezone = _timezone_from_status(scheduler_status)
    current = now.astimezone(ZoneInfo(timezone)) if now else datetime.now(ZoneInfo(timezone))
    next_run = _next_local_run(current, dashboard_time)
    latest = jobs[0] if jobs else None
    latest_failed = _current_failed_job(refresh_jobs, jobs)
    latest_historical_failed = _latest_historical_failed(refresh_jobs, jobs)
    registered = bool(timer.get("registered"))
    supported = bool(timer.get("supported", True))
    running = bool(scheduler_status.get("running"))
    schedule_enabled = bool(scheduler_status.get("enabled"))
    enabled = bool(schedule_enabled or registered)
    return {
        "status": _cadence_status(enabled, supported, latest_failed),
        "enabled": enabled,
        "scheduleEnabled": schedule_enabled,
        "running": running,
        "timezone": timezone,
        "dashboardAggregationTime": dashboard_time,
        "dailyPipelineTime": pipeline_time,
        "nextDashboardAggregationAt": next_run.isoformat(),
        "lastDashboardAggregationDate": state.get("lastDashboardAggregationDate"),
        "lastDashboardAggregationAt": state.get("lastDashboardAggregationAt"),
        "lastDashboardAggregationRunIds": state.get("lastDashboardAggregationRunIds") or [],
        "lastError": state.get("lastError"),
        "systemTimer": {
            "provider": timer.get("provider"),
            "supported": supported,
            "registered": registered,
            "dashboardAggregationLabel": dashboard_job.get("label"),
            "dailyPipelineLabel": pipeline_job.get("label"),
        },
        "latestRefreshJob": latest,
        "latestFailedRefreshJob": latest_failed,
        "latestHistoricalFailedRefreshJob": latest_historical_failed,
    }


def build_snapshot_operations(
    *,
    readiness: dict | None,
    refresh_jobs: dict | list | None,
    scheduler_status: dict | None,
    now: datetime | None = None,
) -> dict:
    """Compose the Foundation snapshot operations payload."""
    language_profile = dashboard_language_profile()
    return {
        "projectionCompleteness": build_projection_completeness_matrix(readiness, language_profile=language_profile),
        "scheduledRunCadence": build_scheduled_run_cadence(scheduler_status, refresh_jobs, now=now),
        "refreshJobs": _coerce_jobs(refresh_jobs),
    }


def build_foundation_production_readiness(
    *,
    readiness: dict | None,
    refresh_jobs: dict | list | None,
    scheduler_status: dict | None,
    runtime_sources: dict | None,
    require_period_projections: bool = True,
    now: datetime | None = None,
) -> dict:
    """Summarize whether Foundation is ready to be the normal production data plane."""
    language_profile = dashboard_language_profile()
    projection = build_projection_completeness_matrix(
        readiness,
        require_period_projections=require_period_projections,
        language_profile=language_profile,
    )
    cadence = build_scheduled_run_cadence(scheduler_status, refresh_jobs, now=now)
    sources = runtime_sources or {}
    legacy_active = [
        {"envName": key, "effectiveSource": value, "targetSource": "foundation"}
        for key, value in sorted(sources.items())
        if key in PRODUCTION_READER_SOURCE_FIELDS and value != "foundation"
    ]
    supplemental_legacy = [
        {"envName": key, "effectiveSource": value, "targetSource": "foundation"}
        for key, value in sorted(sources.items())
        if key not in PRODUCTION_READER_SOURCE_FIELDS and value != "foundation"
    ]
    blockers: list[dict] = []
    if not projection.get("configuredSourcesValid", True):
        blockers.append({"key": "runtime-sources-invalid", "severity": "blocker"})
    if projection["status"] != "complete":
        blockers.append({"key": "required-projections-incomplete", "severity": "blocker"})
    if cadence["latestFailedRefreshJob"] is not None:
        blockers.append({"key": "latest-refresh-failed", "severity": "blocker"})
    daily_reports = (readiness or {}).get("dailyReadinessReports") if isinstance(readiness, dict) else None
    incomplete_daily_reports = [
        {"surface": key, **value}
        for key, value in sorted((daily_reports or {}).items())
        if isinstance(value, dict) and not value.get("ready")
    ]
    if incomplete_daily_reports:
        blockers.append(
            {
                "key": "daily-readiness-report-incomplete",
                "severity": "blocker",
                "count": len(incomplete_daily_reports),
                "details": incomplete_daily_reports,
            }
        )
    if legacy_active:
        blockers.append({"key": "legacy-sources-active", "severity": "migration", "count": len(legacy_active)})
    status = "ready" if not blockers else ("legacy_active" if all(item["key"] == "legacy-sources-active" for item in blockers) else "blocked")
    operator_findings = _annotate_operator_issues(blockers, language_profile=language_profile)
    return {
        "status": status,
        "generatedAt": (now or business_now()).astimezone().isoformat(),
        "runtimeSources": sources,
        "legacyNormalPaths": legacy_active,
        "supplementalLegacyPaths": supplemental_legacy,
        "blockers": operator_findings,
        "operatorFindings": operator_findings,
        "projectionCompleteness": projection,
        "dailyReadinessReports": daily_reports or {},
        "scheduledRunCadence": cadence,
        "materializationOwner": {
            "normalCommand": "python advanced/pipeline/run_daily_pipeline.py [YYYY-MM-DD]",
            "dashboardRefreshRole": "manual-repair-backfill",
            "requestTimeLegacyFallbackAllowed": False,
        },
        "boundaries": {
            "taskAuthority": "Nova-Task v2 SQLite",
            "taskBoard": "projection",
            "taskAuditSink": "optional-additive-cutover",
            "rag": "v2-independent",
            "defaultFlagsChanged": False,
        },
    }


def build_foundation_daily_qa_report(
    *,
    business_date: date,
    documents: list[dict] | None,
    diary_readiness: dict | None,
    ingestion_runs: list[dict] | None,
    latest_pipeline_failure: dict | None = None,
    production_readiness: dict | None = None,
    daily_completeness: dict | None = None,
    language_profile: str = "zh",
    now: datetime | None = None,
) -> dict:
    """Build a day-level, read-only QA summary for the generated business path."""
    docs = documents or []
    readiness = diary_readiness or {}
    runs = ingestion_runs or []
    generated_at = (now or business_now()).astimezone().isoformat()
    doc_summary = _daily_document_summary(docs, language_profile=language_profile)
    readiness_summary = _daily_readiness_summary(readiness)
    run_summary = _daily_ingestion_summary(runs, business_date)
    no_activity = _documents_mark_no_activity(docs) or bool((daily_completeness or {}).get("isBlankDay"))
    blockers: list[dict] = []
    warnings: list[dict] = []

    if not no_activity:
        for report_type, row in doc_summary["required"].items():
            if not row["present"]:
                blockers.append({"key": "diary-document-missing", "reportType": report_type, "severity": "blocker"})
                continue
            for section in row["missingSections"]:
                blockers.append(
                    {
                        "key": "diary-section-missing",
                        "reportType": report_type,
                        "section": section,
                        "severity": "blocker",
                    }
                )
            for key in row["missingEmbeddedKeys"]:
                blockers.append(
                    {
                        "key": "diary-embedded-json-key-missing",
                        "reportType": report_type,
                        "embeddedKey": key,
                        "severity": "blocker",
                    }
                )
            for issue in row["contentWarnings"]:
                warnings.append(
                    {
                        "key": "diary-section-content-weak",
                        "reportType": report_type,
                        "section": issue["section"],
                        "reason": issue["reason"],
                        "severity": "warning",
                    }
                )

        for key, row in readiness_summary.items():
            if row["checked"] and not row["ready"]:
                blockers.append({"key": "foundation-diary-readiness-blocked", "surface": key, "status": row["status"], "severity": "blocker"})
    if daily_completeness:
        for item in daily_completeness.get("missingItems") or []:
            blockers.append(
                {
                    "key": "daily-completeness-missing",
                    "itemKey": item.get("key"),
                    "label": item.get("label"),
                    "action": item.get("action"),
                    "severity": "blocker",
                }
            )

    if run_summary["latestFailedForDate"] is not None:
        blockers.append({"key": "foundation-ingestion-run-failed", "runId": run_summary["latestFailedForDate"].get("id"), "severity": "blocker"})
    if not run_summary["runsForDate"]:
        warnings.append({"key": "foundation-ingestion-run-missing", "severity": "warning"})

    if _pipeline_failure_matches(latest_pipeline_failure, business_date):
        recovered = _pipeline_failure_recovered(latest_pipeline_failure, run_summary)
        target = warnings if recovered else blockers
        target.append(
            {
                "key": "daily-pipeline-historical-failure" if recovered else "daily-pipeline-latest-failure",
                "failedStep": latest_pipeline_failure.get("failedStep"),
                "severity": "warning" if recovered else "blocker",
            }
        )

    if production_readiness and production_readiness.get("status") not in (None, "ready"):
        warnings.append(
            {
                "key": "foundation-production-readiness-not-ready",
                "status": production_readiness.get("status"),
                "details": production_readiness.get("blockers") or [],
                "severity": "warning",
            }
        )

    blockers = _annotate_operator_issues(blockers, business_date=business_date, language_profile=language_profile)
    warnings = _annotate_operator_issues(warnings, business_date=business_date, language_profile=language_profile)
    status = "ready" if not blockers else "blocked"
    if status == "ready" and warnings:
        status = "attention"
    return {
        "status": status,
        "businessDate": business_date.isoformat(),
        "generatedAt": generated_at,
        "blockers": blockers,
        "warnings": warnings,
        "documents": doc_summary,
        "diaryReadiness": readiness_summary,
        "dailyCompleteness": daily_completeness,
        "foundationIngestion": run_summary,
        "latestPipelineFailure": latest_pipeline_failure,
        "productionReadiness": production_readiness,
        "nextActions": _daily_qa_next_actions(blockers, warnings, business_date, language_profile=language_profile),
        "repairCommands": _daily_qa_repair_commands(blockers, warnings, business_date, language_profile=language_profile),
    }


def build_foundation_daily_qa_overview(
    *,
    start_date: date,
    days: int,
    reports: list[dict],
    now: datetime | None = None,
) -> dict:
    """Summarize multiple daily QA reports for operator scanning."""
    generated_at = (now or business_now()).astimezone().isoformat()
    rows = []
    for report in reports:
        documents = report.get("documents") if isinstance(report.get("documents"), dict) else {}
        readiness = report.get("diaryReadiness") if isinstance(report.get("diaryReadiness"), dict) else {}
        ready_inputs = sum(1 for item in readiness.values() if isinstance(item, dict) and item.get("ready"))
        total_inputs = len(readiness)
        rows.append(
            {
                "businessDate": report.get("businessDate"),
                "status": report.get("status") or "unknown",
                "blockers": len(report.get("blockers") or []),
                "warnings": len(report.get("warnings") or []),
                "documentStatus": documents.get("status") or "unknown",
                "documentCount": documents.get("count") or 0,
                "foundationInputsReady": ready_inputs,
                "foundationInputsTotal": total_inputs,
                "latestRunId": ((report.get("foundationIngestion") or {}).get("latestForDate") or {}).get("id"),
            }
        )
    counts = {
        "ready": sum(1 for row in rows if row["status"] == "ready"),
        "attention": sum(1 for row in rows if row["status"] == "attention"),
        "blocked": sum(1 for row in rows if row["status"] == "blocked"),
        "unknown": sum(1 for row in rows if row["status"] not in ("ready", "attention", "blocked")),
    }
    status = "ready"
    if counts["blocked"]:
        status = "blocked"
    elif counts["attention"] or counts["unknown"]:
        status = "attention"
    return {
        "status": status,
        "generatedAt": generated_at,
        "start": start_date.isoformat(),
        "days": days,
        "counts": counts,
        "rows": rows,
    }


def get_snapshot_operations(*, period_start=None, period_days: int | None = None, limit: int = 20) -> dict:
    """Runtime facade over existing Dashboard services."""
    from . import foundation, scheduler

    if period_start is not None and period_days is not None:
        readiness = foundation.get_reader_readiness(period_start=period_start, period_days=period_days)
    else:
        readiness = foundation.get_reader_readiness()
    jobs = foundation.list_refresh_jobs(limit=limit)
    status = scheduler.scheduler_status()
    return build_snapshot_operations(readiness=readiness, refresh_jobs=jobs, scheduler_status=status)


def get_foundation_production_readiness(*, period_start=None, period_days: int | None = None, limit: int = 20) -> dict:
    """Runtime facade for the Foundation-first production go/no-go summary."""
    from . import foundation, scheduler
    from data_foundation.paths import load_paths
    from data_foundation.settings import resolve_runtime_sources

    paths = load_paths()
    if period_start is not None and period_days is not None:
        readiness = foundation.get_reader_readiness(period_start=period_start, period_days=period_days)
        period_end = period_start + timedelta(days=period_days - 1)
        readiness = {
            **readiness,
            "dailyReadinessReports": _daily_source_readiness_reports(paths, period_end),
        }
    else:
        readiness = foundation.get_reader_readiness()
    jobs = foundation.list_refresh_jobs(limit=limit)
    status = scheduler.scheduler_status()
    return build_foundation_production_readiness(
        readiness=readiness,
        refresh_jobs=jobs,
        scheduler_status=status,
        runtime_sources=resolve_runtime_sources(paths),
        require_period_projections=period_days != 1,
    )


def _daily_source_readiness_reports(paths, business_date: date) -> dict:
    specs = {
        "diaryMetrics": ("diary-metrics-readiness", "diaryMetricsSourceFoundation"),
        "diaryMemory": ("diary-memory-readiness", "diaryMemorySourceFoundation"),
        "diaryTasks": ("diary-tasks-readiness", "diaryTasksSourceFoundation"),
    }
    result = {}
    for key, (prefix, can_enable_key) in specs.items():
        path = paths.state_dir / "migration" / f"{prefix}-{business_date.isoformat()}.json"
        report = _read_json(path)
        can_enable = (report.get("canEnable") or {}) if isinstance(report, dict) else {}
        ready = bool(report and can_enable.get(can_enable_key))
        result[key] = {
            "checked": True,
            "ready": ready,
            "status": report.get("status") if report else "missing",
            "businessDate": business_date.isoformat(),
            "path": str(path),
            "exists": path.exists(),
            "canEnable": can_enable,
        }
    return result


def _read_json(path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def get_foundation_daily_qa(*, business_date: date, limit: int = 20) -> dict:
    """Runtime facade for a read-only day-level Foundation QA report."""
    from data_foundation.aggregate import daily_diary_usage_metrics
    from data_foundation.daily_completeness import evaluate_daily_completeness
    from data_foundation.diary_markdown import read_diary_markdown_documents
    from data_foundation.jobs import list_ingestion_runs
    from data_foundation.paths import load_paths
    from data_foundation.pipeline import latest_pipeline_failure
    from data_foundation.settings import ensure_settings
    from data_foundation.snapshots import read_diary_memory_snapshot, read_diary_tasks_snapshot

    paths = load_paths()
    settings = ensure_settings(paths)
    pipeline_settings = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    language_profile = str(pipeline_settings.get("languageProfile") or "zh")
    documents = read_diary_markdown_documents(paths, business_date, business_date)
    metrics = daily_diary_usage_metrics(paths, business_date)
    memory = read_diary_memory_snapshot(paths, business_date)
    tasks = read_diary_tasks_snapshot(paths, business_date)
    readiness = {
        "metrics": _foundation_metrics_presence(metrics),
        "memory": _foundation_snapshot_presence(memory),
        "tasks": _foundation_snapshot_presence(tasks),
    }
    runs = list_ingestion_runs(paths, limit=limit)
    production = get_foundation_production_readiness(period_start=business_date, period_days=1, limit=limit)
    completeness = evaluate_daily_completeness(paths, business_date, documents=documents) if hasattr(paths, "diary_dir") else None
    return build_foundation_daily_qa_report(
        business_date=business_date,
        documents=documents,
        diary_readiness=readiness,
        ingestion_runs=runs,
        latest_pipeline_failure=latest_pipeline_failure(paths),
        production_readiness=production,
        daily_completeness=completeness,
        language_profile=language_profile,
    )


def get_foundation_daily_qa_overview(*, end_date: date, days: int = 7, limit: int = 20) -> dict:
    """Runtime facade for the recent daily QA overview."""
    days = max(1, min(int(days), 31))
    start_date = end_date - timedelta(days=days - 1)
    reports = [
        get_foundation_daily_qa(business_date=start_date + timedelta(days=offset), limit=limit)
        for offset in range(days)
    ]
    return build_foundation_daily_qa_overview(start_date=start_date, days=days, reports=reports)


def get_foundation_daily_pipeline_summary(*, business_date: date, limit: int = 20) -> dict:
    """Read-only operator metrics for one business date's latest generated path."""
    from data_foundation.db import migrate
    from data_foundation.diary_markdown import read_diary_markdown_documents
    from data_foundation.jobs import list_ingestion_runs
    from data_foundation.paths import load_paths
    from data_foundation.pipeline import latest_pipeline_failure
    from data_foundation.repair_runs import list_repair_runs

    paths = load_paths()
    migrate(paths)
    documents = read_diary_markdown_documents(paths, business_date, business_date)
    runs = list_ingestion_runs(paths, limit=limit)
    runs_for_date = [run for run in runs if run.get("business_date") == business_date.isoformat()]
    latest_run = runs_for_date[0] if runs_for_date else None
    latest_materialization = next(
        (
            run
            for run in runs_for_date
            if run.get("trigger_type") in {"pipeline-foundation-materialization", "pipeline-blank-day-materialization"}
        ),
        None,
    )
    latest_blank_inputs = next(
        (run for run in runs_for_date if run.get("trigger_type") == "pipeline-blank-day-inputs"),
        None,
    )
    repair_runs = [
        run
        for run in list_repair_runs(paths, limit=limit)
        if run.get("business_date") == business_date.isoformat()
    ]
    latest_repair = repair_runs[0] if repair_runs else None
    file_stats = _daily_generated_file_stats(paths, business_date, documents)
    lesson_stats = _daily_lesson_stats(documents)
    no_activity = _documents_mark_no_activity(documents)
    task_stats = _blank_day_task_stats() if no_activity else _nova_task_event_stats(paths, business_date)
    pipeline_failure = latest_pipeline_failure(paths)
    matching_failure = pipeline_failure if _pipeline_failure_matches(pipeline_failure, business_date) else None
    if matching_failure and _pipeline_failure_recovered(matching_failure, {"latestForDate": latest_run}):
        matching_failure = None
    return {
        "businessDate": business_date.isoformat(),
        "generatedAt": business_now().astimezone().isoformat(),
        "status": _daily_pipeline_summary_status(latest_run, matching_failure, documents),
        "activityState": "empty" if no_activity else "active",
        "latestRun": _run_summary(latest_run),
        "latestMaterializationRun": _run_summary(latest_materialization),
        "latestBlankInputsRun": _run_summary(latest_blank_inputs),
        "latestRepairRun": _public_repair_run(latest_repair) if latest_repair else None,
        "latestPipelineFailure": matching_failure,
        "documents": {
            "count": len(documents),
            "totalBytes": sum(int(document.get("byte_size") or 0) for document in documents),
            "byType": _document_type_stats(documents),
            "files": file_stats,
        },
        "lessons": lesson_stats,
        "tasks": task_stats,
    }


def queue_foundation_daily_qa_repair(
    *,
    action_id: str,
    business_date: date,
    confirmation_text: str,
    limit: int = 20,
) -> dict:
    """Queue a controlled Daily QA repair action after server-side validation."""
    from data_foundation.paths import load_paths
    from data_foundation.repair_runs import create_repair_run, digest_text, find_active_repair_run

    action = DASHBOARD_EXECUTABLE_REPAIR_ACTIONS.get(action_id)
    if action is None:
        raise ValueError("Unsupported repair action")
    expected = f"RUN {business_date.isoformat()}"
    if confirmation_text != expected:
        raise ValueError(f"confirmationText must be {expected!r}")

    paths = load_paths()
    active = find_active_repair_run(paths, action_id=action_id, business_date=business_date)
    if active is not None:
        return {"status": "already_running", "run": _public_repair_run(active)}

    qa_before = get_foundation_daily_qa(business_date=business_date, limit=limit)
    if not any(item.get("actionId") == action_id for item in qa_before.get("repairCommands") or []):
        raise ValueError("Repair action is not recommended by the current Daily QA payload")

    qa_audit = _qa_audit_summary(qa_before)
    source_pipeline_run_id = None
    if action_id == "retry-daily-pipeline":
        from data_foundation.pipeline_runs import latest_pipeline_run_for_date

        latest_pipeline_run = latest_pipeline_run_for_date(paths, business_date)
        if latest_pipeline_run and latest_pipeline_run.get("status") in {"failed", "partial"}:
            source_pipeline_run_id = int(latest_pipeline_run["id"])
            qa_audit["sourcePipelineRunId"] = source_pipeline_run_id

    lock_key = f"{action['lockPrefix']}-{business_date.isoformat()}.lock"
    command_spec = {
        "actionId": action_id,
        "businessDate": business_date.isoformat(),
        "resolver": "data_foundation.pipeline.run_daily_pipeline",
        "argv": [business_date.isoformat()],
        "retryOfRunId": source_pipeline_run_id,
    }
    run_id = create_repair_run(
        paths,
        action_id=action_id,
        action_class=action["actionClass"],
        business_date=business_date,
        lock_key=lock_key,
        command_digest=digest_text(json.dumps(command_spec, sort_keys=True)) or "",
        confirmation_digest=digest_text(confirmation_text),
        qa_before=qa_audit,
    )
    run = get_foundation_repair_run(run_id)
    return {"status": "queued", "run": run}


def execute_foundation_daily_qa_repair(run_id: int) -> None:
    """Run the queued repair and persist a bounded execution audit."""
    from data_foundation.paths import load_paths
    from data_foundation.pipeline import run_daily_pipeline
    from data_foundation.repair_runs import finish_repair_run, get_repair_run, mark_repair_run_running

    paths = load_paths()
    run = get_repair_run(paths, run_id)
    if run is None:
        return
    business_date = date.fromisoformat(run["business_date"])
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 1
    status = "failed"
    error_summary = None
    qa_after = None
    mark_repair_run_running(paths, run_id)
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            pipeline_kwargs = {"paths": paths, "trigger": "dashboard-daily-qa-repair"}
            source_pipeline_run_id = (run.get("qaBefore") or {}).get("sourcePipelineRunId")
            if run.get("action_id") == "retry-daily-pipeline" and source_pipeline_run_id is not None:
                pipeline_kwargs["retry_of_run_id"] = int(source_pipeline_run_id)
            result = run_daily_pipeline(business_date, **pipeline_kwargs)
        exit_code = 0 if result.success else 1
        status = "completed" if result.success else "failed"
        error_summary = None if result.success else (result.failed_step or "Daily pipeline failed")
    except Exception as error:
        error_summary = str(error)
    try:
        qa_after = _qa_audit_summary(get_foundation_daily_qa(business_date=business_date, limit=20))
    except Exception as error:
        if error_summary:
            error_summary = f"{error_summary}; Daily QA rerun failed: {error}"
        else:
            error_summary = f"Daily QA rerun failed: {error}"
    finish_repair_run(
        paths,
        run_id,
        status=status,
        exit_code=exit_code,
        stdout_tail=_tail(stdout.getvalue()),
        stderr_tail=_tail(stderr.getvalue()),
        error_summary=error_summary,
        qa_after=qa_after,
    )


def get_foundation_repair_run(run_id: int) -> dict | None:
    """Return a bounded repair run audit record."""
    from data_foundation.paths import load_paths
    from data_foundation.repair_runs import get_repair_run

    paths = load_paths()
    run = get_repair_run(paths, run_id)
    return _public_repair_run(run) if run else None


def list_foundation_repair_runs(*, limit: int = 20) -> dict:
    """List recent controlled repair runs."""
    from data_foundation.paths import load_paths
    from data_foundation.repair_runs import list_repair_runs

    paths = load_paths()
    return {"runs": [_public_repair_run(run) for run in list_repair_runs(paths, limit=limit)]}


def get_project_registry_status() -> dict:
    """Read-only project registry governance status."""
    from data_foundation.project_registry import project_registry_status

    return project_registry_status()


def get_system_registry_status() -> dict:
    """Read-only system component registry status."""
    from data_foundation.system_registry import register_default_system_components

    return register_default_system_components()


def _daily_document_summary(documents: list[dict], *, language_profile: str = "zh") -> dict:
    by_type: dict[str, list[dict]] = {}
    for document in documents:
        by_type.setdefault(document.get("report_type") or "unknown", []).append(document)
    required = {}
    for report_type in DAILY_REQUIRED_DOCUMENT_TYPES:
        selected = by_type.get(report_type) or []
        primary = selected[0] if selected else None
        headings = [section.get("heading", "") for section in (primary or {}).get("sections", [])]
        embedded = (primary or {}).get("embeddedJson") if primary else None
        embedded = embedded if isinstance(embedded, dict) else {}
        required[report_type] = {
            "present": primary is not None,
            "documentKey": primary.get("document_key") if primary else None,
            "title": primary.get("title") if primary else None,
            "sourceRunId": primary.get("source_run_id") if primary else None,
            "sectionCount": len((primary or {}).get("sections", [])),
            "headings": headings,
            "missingSections": _missing_required_sections(report_type, headings, language_profile=language_profile),
            "contentWarnings": _section_content_warnings(
                report_type,
                (primary or {}).get("sections", []),
                language_profile=language_profile,
            ),
            "embeddedJsonPresent": bool(embedded),
            "missingEmbeddedKeys": [key for key in DAILY_REQUIRED_EMBEDDED_KEYS.get(report_type, ()) if key not in embedded],
        }
    return {
        "status": "complete" if all(row["present"] and not row["missingSections"] and not row["missingEmbeddedKeys"] for row in required.values()) else "incomplete",
        "count": len(documents),
        "required": required,
        "extras": [
            {
                "reportType": document.get("report_type"),
                "documentKey": document.get("document_key"),
                "title": document.get("title"),
            }
            for document in documents
            if document.get("report_type") not in DAILY_REQUIRED_DOCUMENT_TYPES
        ],
    }


def _missing_required_sections(report_type: str, headings: list[str], *, language_profile: str = "zh") -> list[str]:
    missing = []
    for required in _required_section_profile(report_type, language_profile):
        if _find_heading_for_required(required, headings) is None:
            missing.append(required["label"])
    return missing


def _section_content_warnings(report_type: str, sections: list[dict], *, language_profile: str = "zh") -> list[dict]:
    warnings = []
    for required in _required_section_profile(report_type, language_profile):
        section = _find_section(required, sections)
        if section is None:
            continue
        reason = _section_content_warning_reason(
            required["id"],
            section.get("bodyMarkdown") or "",
            has_child_sections=_has_child_sections(section, sections),
            allow_none=bool(required.get("allowNone")),
        )
        if reason:
            warnings.append({"section": required["label"], "reason": reason})
    return warnings


def _required_section_profile(report_type: str, language_profile: str) -> tuple[dict, ...]:
    profiles = DAILY_REQUIRED_SECTION_PROFILES.get(report_type, {})
    if "shared" in profiles:
        return profiles["shared"]
    profile = "en" if str(language_profile or "").lower().startswith("en") else "zh"
    if profile in profiles:
        return profiles[profile]
    return profiles.get("zh", ())


def _find_heading_for_required(required: dict, headings: list[str]) -> str | None:
    aliases = tuple(required.get("aliases") or ())
    for heading in headings:
        text = str(heading or "")
        if any(alias in text for alias in aliases):
            return text
    return None


def _find_section(required: dict, sections: list[dict]) -> dict | None:
    for section in sections:
        if _find_heading_for_required(required, [str(section.get("heading") or "")]) is not None:
            return section
    return None


def _has_child_sections(parent: dict, sections: list[dict]) -> bool:
    parent_path = parent.get("headingPath") if isinstance(parent.get("headingPath"), list) else []
    if not parent_path:
        return False
    for section in sections:
        path = section.get("headingPath") if isinstance(section.get("headingPath"), list) else []
        if len(path) > len(parent_path) and path[: len(parent_path)] == parent_path:
            return True
    return False


def _section_content_warning_reason(
    section_id: str,
    body: str,
    *,
    has_child_sections: bool = False,
    allow_none: bool = False,
) -> str | None:
    text = body.strip()
    if not text:
        if has_child_sections:
            return None
        return "section body is empty"
    normalized = text.lower()
    hard_failure_markers = ("加载失败", "获取失败", "未获取", "未注入", "待补充")
    if any(marker in normalized for marker in hard_failure_markers):
        return "section body contains placeholder or failure text"
    compact = normalized.strip(" .。:：-—_*`[]()")
    if len(compact) <= 30 and compact in {"todo", "n/a", "na", "null", "undefined"}:
        return "section body contains placeholder or failure text"
    if text in {"—", "-", "无数据"}:
        return "section body has no concrete data"
    if not allow_none and text in {"无", "暂无", "无。", "None", "none"}:
        return "section body reports no data"
    return None


def _daily_readiness_summary(diary_readiness: dict) -> dict:
    result = {}
    for key, payload in diary_readiness.items():
        payload = payload if isinstance(payload, dict) else {}
        can_enable = payload.get("canEnable") if isinstance(payload.get("canEnable"), dict) else {}
        enable_values = [value for name, value in can_enable.items() if name.startswith("diary") and name.endswith("SourceFoundation")]
        checked = bool(payload)
        ready = bool(enable_values and all(enable_values))
        result[key] = {
            "checked": checked,
            "ready": ready,
            "status": payload.get("status") or ("missing" if checked else "not_checked"),
            "sourceRunId": payload.get("sourceRunId"),
            "snapshotGeneratedAt": payload.get("snapshotGeneratedAt"),
            "canEnable": can_enable,
        }
    return result


def _foundation_metrics_presence(metrics: dict | None) -> dict:
    if metrics is None:
        return {"status": "missing", "canEnable": {"diaryMetricsSourceFoundation": False}}
    total = metrics.get("total") if isinstance(metrics.get("total"), dict) else {}
    return {
        "status": "ready",
        "canEnable": {"diaryMetricsSourceFoundation": True},
        "totalTokens": total.get("total_tokens", 0),
        "modelUsageCount": len(metrics.get("model_usage_list") or []),
    }


def _foundation_snapshot_presence(snapshot: dict | None) -> dict:
    if snapshot is None:
        return {"status": "missing", "canEnable": {"diarySnapshotSourceFoundation": False}}
    return {
        "status": "ready",
        "sourceRunId": snapshot.get("sourceRunId"),
        "snapshotGeneratedAt": snapshot.get("generatedAt"),
        "projectionType": snapshot.get("projectionType"),
        "canEnable": {"diarySnapshotSourceFoundation": True},
    }


def _daily_ingestion_summary(ingestion_runs: list[dict], business_date: date) -> dict:
    target = business_date.isoformat()
    runs_for_date = [run for run in ingestion_runs if run.get("business_date") == target]
    failed = [run for run in runs_for_date if run.get("status") == "failed"]
    return {
        "runsForDate": runs_for_date,
        "latestForDate": runs_for_date[0] if runs_for_date else None,
        "latestFailedForDate": failed[0] if failed else None,
        "recentRuns": ingestion_runs,
    }


def _pipeline_failure_matches(latest_pipeline_failure: dict | None, business_date: date) -> bool:
    if not isinstance(latest_pipeline_failure, dict):
        return False
    return latest_pipeline_failure.get("businessDate") == business_date.isoformat()


def _pipeline_failure_recovered(latest_pipeline_failure: dict | None, run_summary: dict) -> bool:
    if not isinstance(latest_pipeline_failure, dict):
        return False
    latest = run_summary.get("latestForDate")
    if not isinstance(latest, dict) or latest.get("status") != "completed":
        return False
    failure_time = _parse_iso_datetime(latest_pipeline_failure.get("createdAt"))
    recovery_time = _parse_iso_datetime(latest.get("completed_at") or latest.get("started_at"))
    if failure_time is None or recovery_time is None:
        return False
    return recovery_time >= failure_time


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _daily_qa_next_actions(
    blockers: list[dict],
    warnings: list[dict],
    business_date: date,
    *,
    language_profile: str = "zh",
) -> list[str]:
    if not blockers and not warnings:
        return []
    actions = []
    keys = {item.get("key") for item in blockers + warnings}
    if "diary-document-missing" in keys or "diary-section-missing" in keys or "diary-embedded-json-key-missing" in keys:
        actions.append(_foundation_ui("rerunDailyPipelineAction", language_profile).format(day=business_date.isoformat()))
    if "foundation-diary-readiness-blocked" in keys or "foundation-ingestion-run-missing" in keys or "foundation-ingestion-run-failed" in keys:
        actions.append(_foundation_ui("foundationRepairAction", language_profile).format(day=business_date.isoformat()))
    if "daily-pipeline-latest-failure" in keys:
        actions.append(_foundation_ui("inspectPipelineFailureAction", language_profile))
    if "foundation-production-readiness-not-ready" in keys:
        actions.append(_foundation_ui("checkProductionReadinessAction", language_profile))
    return actions


def _daily_qa_repair_commands(
    blockers: list[dict],
    warnings: list[dict],
    business_date: date,
    *,
    language_profile: str = "zh",
) -> list[dict]:
    day = business_date.isoformat()
    keys = {item.get("key") for item in blockers + warnings}
    commands = []
    if keys & {"diary-document-missing", "diary-section-missing", "diary-embedded-json-key-missing", "diary-section-content-weak"}:
        commands.append(
            _repair_command(
                action_id="run-full-daily-pipeline",
                label=_foundation_ui("runFullDailyPipeline", language_profile),
                action_class="heavy-llm-pipeline",
                command=f"python advanced/pipeline/run_daily_pipeline.py {day}",
                confirmation=f"RUN {day}",
                language_profile=language_profile,
            )
        )
        commands.append(
            _repair_command(
                action_id="rematerialize-diary-markdown",
                label=_foundation_ui("rematerializeDiaryMarkdown", language_profile),
                action_class="safe-foundation-projection-refresh",
                command=(
                    "PYTHONPATH=src python3 - <<'PY'\n"
                    "from datetime import date\n"
                    "from data_foundation.paths import load_paths\n"
                    "from data_foundation.diary_markdown import materialize_diary_markdown_day\n"
                    f"print(materialize_diary_markdown_day(load_paths(), date.fromisoformat('{day}'), source_run_id=None))\n"
                    "PY"
                ),
                language_profile=language_profile,
            )
        )
    if "daily-pipeline-latest-failure" in keys:
        commands.append(
            _repair_command(
                action_id="retry-daily-pipeline",
                label=_foundation_ui("retryDailyPipeline", language_profile),
                action_class="heavy-llm-pipeline",
                command=f"python advanced/pipeline/run_daily_pipeline.py {day}",
                confirmation=f"RUN {day}",
                language_profile=language_profile,
            )
        )
    if "foundation-production-readiness-not-ready" in keys:
        commands.append(
            _repair_command(
                action_id="inspect-production-readiness",
                label=_foundation_ui("inspectProductionReadiness", language_profile),
                action_class="read-only",
                command=f"curl '{_dashboard_api_base_url()}/api/foundation/ops/production-readiness?start={day}&days=1&limit=20'",
                language_profile=language_profile,
            )
        )
    return _dedupe_repair_commands(commands)


def _repair_command(
    *,
    action_id: str,
    label: str,
    action_class: str,
    command: str,
    confirmation: str | None = None,
    language_profile: str = "zh",
) -> dict:
    executable = action_id in DASHBOARD_EXECUTABLE_REPAIR_ACTIONS
    policy = {
        "dashboardExecutable": executable,
        "executionState": "dashboard-executable" if executable else "manual-only",
        "reason": (
            _foundation_ui("dashboardExecutableReason", language_profile)
            if executable
            else _foundation_ui("manualOnlyReason", language_profile)
        ),
        "requiresAudit": True,
        "requiresLock": action_class != "read-only",
        "requiresTypedConfirmation": action_class == "heavy-llm-pipeline",
        "confirmationPhrase": confirmation,
    }
    if action_class == "read-only":
        policy["reason"] = _foundation_ui("readOnlyAuditReason", language_profile)
        policy["requiresLock"] = False
    return {
        "actionId": action_id,
        "label": label,
        "risk": action_class,
        "actionClass": action_class,
        "command": command,
        "executionPolicy": policy,
    }


def _qa_audit_summary(payload: dict | None) -> dict:
    payload = payload or {}
    return {
        "status": payload.get("status"),
        "businessDate": payload.get("businessDate"),
        "generatedAt": payload.get("generatedAt"),
        "blockers": len(payload.get("blockers") or []),
        "warnings": len(payload.get("warnings") or []),
        "repairActionIds": [item.get("actionId") for item in payload.get("repairCommands") or [] if item.get("actionId")],
    }


def _daily_pipeline_summary_status(latest_run: dict | None, pipeline_failure: dict | None, documents: list[dict]) -> str:
    if latest_run and latest_run.get("status") == "failed":
        return "failed"
    if latest_run and latest_run.get("status") in {"queued", "running"}:
        return latest_run["status"]
    if pipeline_failure:
        return "attention"
    if _documents_mark_no_activity(documents):
        return "ready"
    required = {document.get("report_type") for document in documents}
    return "ready" if {"narrative", "technical", "learning"}.issubset(required) else "incomplete"


def _documents_mark_no_activity(documents: list[dict]) -> bool:
    for document in documents:
        if document.get("report_type") != "narrative":
            continue
        embedded = document.get("embeddedJson") or document.get("embedded_json") or {}
        if isinstance(embedded, dict) and embedded.get("activityState") == "empty":
            return True
        relative = str(document.get("relative_path") or "")
        if relative.endswith("-no-activity.md"):
            return True
    return False


def _run_summary(run: dict | None) -> dict | None:
    if not run:
        return None
    started = _parse_iso_datetime(run.get("started_at"))
    completed = _parse_iso_datetime(run.get("completed_at"))
    duration = (completed - started).total_seconds() if started and completed else None
    return {
        "id": run.get("id"),
        "triggerType": run.get("trigger_type"),
        "businessDate": run.get("business_date"),
        "status": run.get("status"),
        "startedAt": run.get("started_at"),
        "completedAt": run.get("completed_at"),
        "durationSeconds": duration,
        "errorSummary": run.get("error_summary"),
        "metadata": run.get("metadata") or {},
    }


def _document_type_stats(documents: list[dict]) -> dict:
    result: dict[str, dict] = {}
    for document in documents:
        report_type = document.get("report_type") or "unknown"
        row = result.setdefault(report_type, {"count": 0, "totalBytes": 0, "sections": 0})
        row["count"] += 1
        row["totalBytes"] += int(document.get("byte_size") or 0)
        row["sections"] += len(document.get("sections") or [])
    return result


def _daily_generated_file_stats(paths, business_date: date, documents: list[dict]) -> list[dict]:
    root = paths.diary_dir
    rows = []
    for document in documents:
        relative = document.get("relative_path") or ""
        file_path = (root / relative) if root else None
        exists = bool(file_path and file_path.exists())
        byte_size = int(document.get("byte_size") or 0)
        if exists and file_path is not None:
            with contextlib.suppress(OSError):
                byte_size = file_path.stat().st_size
        rows.append(
            {
                "reportType": document.get("report_type"),
                "relativePath": relative,
                "exists": exists,
                "byteSize": byte_size,
                "sectionCount": len(document.get("sections") or []),
            }
        )
    if rows:
        return rows
    return []


def _daily_lesson_stats(documents: list[dict]) -> dict:
    from data_foundation.diary_markdown import _period_lessons

    lessons = []
    for document in documents:
        if document.get("report_type") == "learning":
            lessons.extend(_period_lessons(document))
    return {
        "count": len(lessons),
        "agents": sorted({lesson.get("agent") or "unknown" for lesson in lessons}),
        "items": lessons[:10],
    }


def _nova_task_event_stats(paths, business_date: date) -> dict:
    from data_foundation.db import connect

    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT event_type, confidence, matched_node_id, summary
                FROM nova_task_events
                WHERE business_date = ?
                ORDER BY created_at DESC
                """,
                (business_date.isoformat(),),
            ).fetchall()
            candidates = connection.execute(
                """
                SELECT candidate_type, status, proposed_title
                FROM nova_task_candidates
                WHERE candidate_type = 'parent_task'
                  AND source_event_id IN (
                    SELECT event_id FROM nova_task_events WHERE business_date = ?
                  )
                ORDER BY updated_at DESC
                """,
                (business_date.isoformat(),),
            ).fetchall()
    except Exception as error:
        return {"status": "unavailable", "error": str(error), "eventCount": 0, "matchedUpdates": 0, "candidateCount": 0}
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row["event_type"]] = by_type.get(row["event_type"], 0) + 1
    candidate_by_status: dict[str, int] = {}
    for row in candidates:
        candidate_by_status[row["status"]] = candidate_by_status.get(row["status"], 0) + 1
    return {
        "status": "ready",
        "eventCount": len(rows),
        "matchedUpdates": sum(1 for row in rows if row["matched_node_id"]),
        "candidateCount": len(candidates),
        "byType": by_type,
        "candidatesByStatus": candidate_by_status,
        "recentEvents": [dict(row) for row in rows[:8]],
        "recentCandidates": [dict(row) for row in candidates[:8]],
    }


def _blank_day_task_stats() -> dict:
    return {
        "status": "ready",
        "skipped": True,
        "eventCount": 0,
        "matchedUpdates": 0,
        "candidateCount": 0,
        "byType": {},
        "candidatesByStatus": {},
        "recentEvents": [],
        "recentCandidates": [],
    }


def _tail(value: str) -> str:
    if len(value) <= REPAIR_OUTPUT_TAIL_CHARS:
        return value
    return value[-REPAIR_OUTPUT_TAIL_CHARS:]


def _public_repair_run(run: dict) -> dict:
    return {
        "id": run["id"],
        "actionId": run["action_id"],
        "actionClass": run["action_class"],
        "businessDate": run["business_date"],
        "requestedAt": run["requested_at"],
        "startedAt": run["started_at"],
        "completedAt": run["completed_at"],
        "status": run["status"],
        "exitCode": run["exit_code"],
        "lockKey": run["lock_key"],
        "commandDigest": run["command_digest"],
        "stdoutTail": run["stdout_tail"],
        "stderrTail": run["stderr_tail"],
        "errorSummary": run["error_summary"],
        "qaBefore": run.get("qaBefore") or {},
        "qaAfter": run.get("qaAfter") or {},
    }


def _dedupe_repair_commands(commands: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    result = []
    for command in commands:
        key = command.get("command")
        if not key:
            continue
        existing_index = seen.get(key)
        if existing_index is not None:
            existing = result[existing_index]
            if (
                command.get("actionId") == "retry-daily-pipeline"
                and existing.get("actionId") == "run-full-daily-pipeline"
            ):
                result[existing_index] = command
            continue
        seen[key] = len(result)
        result.append(command)
    return result


def _annotate_operator_issues(
    issues: list[dict],
    *,
    business_date: date | None = None,
    language_profile: str = "zh",
) -> list[dict]:
    return [_operator_issue(item, business_date=business_date, language_profile=language_profile) for item in issues]


def _operator_issue(issue: dict, *, business_date: date | None = None, language_profile: str = "zh") -> dict:
    item = dict(issue)
    key = item.get("key") or "unknown"
    day = business_date.isoformat() if business_date else "selected date"
    defaults = _operator_issue_defaults(language_profile)
    catalog = _operator_issue_catalog(item, day, language_profile)
    item.update({**defaults, **catalog.get(key, {})})
    return item


def _operator_issue_defaults(language_profile: str) -> dict:
    if is_english_profile(language_profile):
        return {
            "title": "Needs review",
            "summary": "The system reported a state that needs operator judgment.",
            "impact": "unknown",
            "action": "Review the details and handle according to context.",
        }
    return {
        "title": "需要检查",
        "summary": "系统报告了一个需要人工判断的状态。",
        "impact": "unknown",
        "action": "查看详情并按上下文处理。",
    }


def _operator_issue_catalog(item: dict, day: str, language_profile: str) -> dict[str, dict]:
    report_type = item.get("reportType") or ("document type" if is_english_profile(language_profile) else "某类")
    section = item.get("section") or ("required" if is_english_profile(language_profile) else "关键")
    embedded_key = item.get("embeddedKey") or ("required field" if is_english_profile(language_profile) else "关键字段")
    surface = item.get("surface") or "Foundation input"
    failed_step = item.get("failedStep") or "unknown"
    completeness_text = _daily_completeness_operator_text(item, day, language_profile)
    if is_english_profile(language_profile):
        return {
            "runtime-sources-invalid": {
                "title": "Runtime source configuration is invalid",
                "summary": "Dashboard or pipeline runtime source settings are outside the allowed values.",
                "impact": "Foundation-first read paths may be ambiguous.",
                "action": "Check runtime settings and restore Foundation source configuration.",
            },
            "required-projections-incomplete": {
                "title": "Required projections are incomplete",
                "summary": "Required Foundation projections for the current period are not all ready.",
                "impact": "Weekly/monthly reports or Dashboard history pages may miss Foundation views.",
                "action": "Run Foundation refresh or daily pipeline materialization for the matching date range.",
            },
            "latest-refresh-failed": {
                "title": "Latest refresh job failed",
                "summary": "The most recent Foundation refresh job did not complete successfully.",
                "impact": "The page may still be using an older snapshot/projection.",
                "action": "Review the failed job error summary, fix dependencies, then rerun refresh.",
            },
            "legacy-sources-active": {
                "title": "Production read path still has a legacy source",
                "summary": "At least one normal production source flag is not set to foundation.",
                "impact": "The Foundation-first cutover conclusion is invalid or running under diagnostic override.",
                "action": "Confirm whether this is an explicit diagnostic override; otherwise restore the source flag to foundation.",
            },
            "diary-document-missing": {
                "title": "Diary artifact is missing",
                "summary": f"{day} is missing the {report_type} markdown document.",
                "impact": "The single-day diary page or period summary cannot render completely.",
                "action": "Rerun the matching pass or daily pipeline, then materialize diary markdown again.",
            },
            "diary-section-missing": {
                "title": "Diary section is missing",
                "summary": f"{day} {report_type} is missing the {section} section.",
                "impact": "The diary content structure is incomplete and may affect Dashboard QA or period summaries.",
                "action": "Rerun the matching generation pass; if it repeats, check prompt and input completeness.",
            },
            "diary-embedded-json-key-missing": {
                "title": "Diary embedded JSON field is missing",
                "summary": f"{day} embedded JSON is missing {embedded_key}.",
                "impact": "Dashboard structured reads may miss stats or task information.",
                "action": "Rerun narrative and confirm assemble_final_markdown output structure is unchanged.",
            },
            "foundation-diary-readiness-blocked": {
                "title": "Foundation diary input is unavailable",
                "summary": f"{day} {surface} is not ready.",
                "impact": "Diary generation or QA cannot confirm complete Foundation authority.",
                "action": "Run Foundation shadow/materialization repair and inspect readiness details.",
            },
            "daily-completeness-missing": completeness_text,
            "foundation-ingestion-run-failed": {
                "title": "Foundation ingestion run failed",
                "summary": f"{day} has a failed Foundation ingestion/materialization run.",
                "impact": "The read model for this day may not be refreshed to the latest state.",
                "action": "Review the run error_summary, fix the issue, then rerun shadow/materialization.",
            },
            "foundation-ingestion-run-missing": {
                "title": "Foundation run record is missing",
                "summary": f"{day} has no matching Foundation ingestion/materialization run.",
                "impact": "This day may not have entered the Foundation read model yet.",
                "action": "Run Foundation shadow or daily pipeline materialization.",
            },
            "daily-pipeline-latest-failure": {
                "title": "Latest daily pipeline run failed",
                "summary": f"{day} latest daily pipeline record failed at step: {failed_step}.",
                "impact": "The generation path for this day may not be fully closed.",
                "action": "Inspect the failed step logs first, then rerun the pipeline or matching repair command.",
            },
            "daily-pipeline-historical-failure": {
                "title": "Daily pipeline had a recovered historical failure",
                "summary": f"{day} previously failed at {failed_step}, and a later successful Foundation run has covered it.",
                "impact": "This is not currently blocking, but remains as an audit note.",
                "action": "Usually no action is needed; if the page is still abnormal, inspect historical failure logs.",
            },
            "foundation-production-readiness-not-ready": {
                "title": "Production readiness still has attention items",
                "summary": "Foundation production-readiness is not currently ready.",
                "impact": "This usually affects period projection or ops go/no-go decisions, not necessarily a single-day diary.",
                "action": "Review Projection Completeness or production-readiness blockers below and rebuild missing projections.",
            },
            "diary-section-content-weak": {
                "title": "Diary section content looks weak",
                "summary": f"{day} {report_type} section {section} is empty or looks like placeholder text.",
                "impact": "The diary structure exists, but business content may not have been injected.",
                "action": "Check the corresponding data source and generation pass; rerun that day's pipeline if needed.",
            },
        }
    return {
        "runtime-sources-invalid": {
            "title": "运行 source 配置无效",
            "summary": "Dashboard 或 pipeline 的 runtime source 配置不在允许值内。",
            "impact": "可能导致 Foundation-first 读路径不确定。",
            "action": "检查 runtime settings，并恢复 Foundation source 配置。",
        },
        "required-projections-incomplete": {
            "title": "必要 projection 不完整",
            "summary": "当前 period 的必要 Foundation projection 尚未全部 ready。",
            "impact": "周报/月报或 Dashboard 历史页面可能缺少 Foundation 视图。",
            "action": "运行对应日期范围的 Foundation refresh 或 daily pipeline materialization。",
        },
        "latest-refresh-failed": {
            "title": "最新 refresh job 失败",
            "summary": "最近一次 Foundation refresh job 没有成功完成。",
            "impact": "页面可能仍在使用上一版 snapshot/projection。",
            "action": "查看失败 job 的 error summary，修复依赖后重跑 refresh。",
        },
        "legacy-sources-active": {
            "title": "生产读路径仍有 legacy source",
            "summary": "至少一个正常生产 source flag 当前不是 foundation。",
            "impact": "Foundation-first cutover 结论不成立或处于诊断 override 模式。",
            "action": "确认是否为显式诊断；若不是，恢复对应 source flag 为 foundation。",
        },
        "diary-document-missing": {
            "title": "日记产物缺失",
            "summary": f"{day} 缺少 {report_type} markdown 文档。",
            "impact": "单日日记页面或 period 汇总无法完整展示。",
            "action": "重跑对应 pass 或 daily pipeline，然后重新 materialize diary markdown。",
        },
        "diary-section-missing": {
            "title": "日记 section 缺失",
            "summary": f"{day} 的 {item.get('reportType') or '文档'} 缺少 {item.get('section') or '关键'} section。",
            "impact": "日记内容结构不完整，可能影响 Dashboard QA 和 period 摘要。",
            "action": "重跑对应生成 pass；若反复出现，检查 prompt 或输入数据完整性。",
        },
        "diary-embedded-json-key-missing": {
            "title": "日记嵌入 JSON 缺字段",
            "summary": f"{day} 的 embedded JSON 缺少 {embedded_key}。",
            "impact": "Dashboard 结构化读取可能缺失统计或任务信息。",
            "action": "重跑 narrative，并确认 assemble_final_markdown 输出结构未变。",
        },
        "foundation-diary-readiness-blocked": {
            "title": "Foundation 日记输入不可用",
            "summary": f"{day} 的 {surface} 不是 ready。",
            "impact": "日记生成或 QA 不能确认 Foundation authority 完整。",
            "action": "运行 Foundation shadow/materialization repair，并查看对应 readiness 详情。",
        },
        "daily-completeness-missing": completeness_text,
        "foundation-ingestion-run-failed": {
            "title": "Foundation ingestion run 失败",
            "summary": f"{day} 存在失败的 Foundation ingestion/materialization run。",
            "impact": "该日 read model 可能未刷新到最新状态。",
            "action": "查看 run error_summary，修复后重跑 shadow/materialization。",
        },
        "foundation-ingestion-run-missing": {
            "title": "缺少 Foundation run 记录",
            "summary": f"{day} 没有找到对应 Foundation ingestion/materialization run。",
            "impact": "该日可能尚未进入 Foundation read model。",
            "action": "运行 Foundation shadow 或 daily pipeline materialization。",
        },
        "daily-pipeline-latest-failure": {
            "title": "Daily pipeline 最新失败",
            "summary": f"{day} 的 daily pipeline 最近记录为失败，失败步骤：{failed_step}。",
            "impact": "该日生成链路可能未完整闭环。",
            "action": "先检查失败步骤日志，再重跑 pipeline 或对应 repair 命令。",
        },
        "daily-pipeline-historical-failure": {
            "title": "Daily pipeline 曾失败但已恢复",
            "summary": f"{day} 曾在 {item.get('failedStep') or '某步骤'} 失败，后续已有成功 Foundation run 覆盖。",
            "impact": "当前不是阻塞，但保留为审计提示。",
            "action": "通常无需处理；若页面仍异常，再查看历史失败日志。",
        },
        "foundation-production-readiness-not-ready": {
            "title": "Production readiness 仍有提醒",
            "summary": "Foundation production-readiness 当前不是 ready。",
            "impact": "通常影响 period projection 或运维 go/no-go 判断，不一定影响单日日记。",
            "action": "查看下方 Projection Completeness 或 production-readiness blockers，按缺失 projection 重建。",
        },
        "diary-section-content-weak": {
            "title": "日记 section 内容可疑",
            "summary": f"{day} 的 {item.get('reportType') or '文档'} 中，{item.get('section') or '某个'} section 内容为空或像占位文本。",
            "impact": "日记结构存在但业务内容可能未真正注入。",
            "action": "检查对应数据源和生成 pass；必要时重跑该日 pipeline。",
        },
    }


def _daily_completeness_operator_text(item: dict, day: str, language_profile: str) -> dict:
    item_key = str(item.get("itemKey") or "")
    label = str(item.get("label") or item_key or "missing item")
    if is_english_profile(language_profile):
        labels = {
            "diary-narrative": "narrative diary",
            "diary-technical": "technical diary",
            "diary-learning": "learning diary",
            "sqlite-materialization": "SQLite materialization",
            "rag-sync": "RAG sync",
            "nova-task": "Nova-Task work graph/export",
            "blankday": "blank/no-activity marker",
        }
        actions = {
            "diary-narrative": "Run the daily pipeline for this date; narrative is the first generated diary artifact.",
            "diary-technical": "Run the technical pass or historical backfill for this date.",
            "diary-learning": "Run the learning pass or historical backfill for this date.",
            "sqlite-materialization": "Run daily Foundation materialization for this date.",
            "rag-sync": "Run RAG sync, or use historical backfill so the RAG step is replayed for this date.",
            "nova-task": "Run Nova-Task work-graph reconciliation after the technical pass, then refresh the TASK_BOARD projection.",
            "blankday": "Generate the blank/no-activity marker for this date.",
        }
        readable = labels.get(item_key, label)
        return {
            "title": f"Daily completeness missing: {readable}",
            "summary": f"{day} is missing {readable} under the daily completeness contract.",
            "impact": "This date is not considered complete by History Backfill and Daily QA.",
            "action": actions.get(item_key, "Run History Backfill for this date and inspect the generated plan."),
        }
    labels = {
        "diary-narrative": "叙事日记",
        "diary-technical": "技术日记",
        "diary-learning": "学习沉淀日记",
        "sqlite-materialization": "SQLite materialization",
        "rag-sync": "RAG sync",
        "nova-task": "Nova-Task work graph/export",
        "blankday": "blank/no-activity 标志",
    }
    actions = {
        "diary-narrative": "运行该日期的 daily pipeline；叙事日记是每日产物的第一步。",
        "diary-technical": "运行 technical pass，或使用历史数据补全让系统补跑该日期。",
        "diary-learning": "运行 learning pass，或使用历史数据补全让系统补跑该日期。",
        "sqlite-materialization": "运行该日期的 Foundation daily materialization。",
        "rag-sync": "运行 RAG sync，或使用历史数据补全让系统重放该日期的 RAG 步骤。",
        "nova-task": "运行 technical pass 后的 Nova-Task work-graph reconciliation，然后刷新 TASK_BOARD projection。",
        "blankday": "为该日期生成 blank/no-activity 标志。",
    }
    readable = labels.get(item_key, label)
    return {
        "title": f"每日完整性缺失：{readable}",
        "summary": f"{day} 缺少 {readable}，不满足每日完整性契约。",
        "impact": "该日期在历史补全和 Daily QA 中都会被视为未完整。",
        "action": actions.get(item_key, "对该日期运行历史数据补全，并按计划预览中的缺失项处理。"),
    }


def _projection_row(
    definition: dict,
    payload: dict,
    *,
    require_period_projection: bool = True,
    language_profile: str = "zh",
) -> dict:
    if definition["key"] in {"periodAssets", "periodPage"} and not require_period_projection:
        payload = {
            **payload,
            "checked": False,
            "ready": None,
            "status": "not_applicable_single_day",
        }
        definition = {
            **definition,
            "optional": True,
            "requiredFor": (),
        }
    checked = bool(payload.get("checked", True))
    ready = payload.get("ready")
    status = payload.get("status") or ("not_checked" if not checked else "unknown")
    complete = bool(ready) if checked else False
    return {
        "key": definition["key"],
        "label": _projection_label(definition["key"], language_profile, definition["label"]),
        "checked": checked,
        "complete": complete,
        "optional": definition["optional"],
        "status": status,
        "requiredFor": list(definition["requiredFor"]),
        "generatedAt": payload.get("generatedAt"),
        "sourceRunId": payload.get("sourceRunId"),
        "projectionType": payload.get("projectionType"),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "days": payload.get("days"),
        "memoryReady": payload.get("memoryReady"),
        "error": payload.get("error"),
    }


def _projection_label(key: str, language_profile: str, default: str) -> str:
    labels = _FOUNDATION_UI["en" if is_english_profile(language_profile) else "zh"]["projectionLabels"]
    return labels.get(key, default)


def _foundation_ui(key: str, language_profile: str) -> str:
    return _FOUNDATION_UI["en" if is_english_profile(language_profile) else "zh"][key]


_FOUNDATION_UI = {
    "zh": {
        "projectionLabels": {
            "aiAssets": "AI assets",
            "periodAssets": "Period assets",
            "periodPage": "Period page",
            "periodSummary": "Period summary",
        },
        "rerunDailyPipelineAction": "Re-run daily pipeline or affected pass for {day}, then materialize Foundation outputs.",
        "foundationRepairAction": "Run Foundation shadow/materialization repair for {day} and inspect readiness details.",
        "inspectPipelineFailureAction": "Inspect the recorded daily pipeline failure step before retrying.",
        "checkProductionReadinessAction": "Check production readiness blockers for snapshot/projection completeness.",
        "runFullDailyPipeline": "Run full daily pipeline",
        "rematerializeDiaryMarkdown": "Re-materialize generated diary markdown",
        "retryDailyPipeline": "Retry daily pipeline after inspecting failure",
        "inspectProductionReadiness": "Inspect production readiness details",
        "dashboardExecutableReason": "Dashboard can execute this allowlisted action after typed confirmation and audit logging.",
        "manualOnlyReason": "Dashboard execution is disabled for this action; use the copyable command manually.",
        "readOnlyAuditReason": "Read-only execution still waits for repair action audit storage.",
    },
    "en": {
        "projectionLabels": {
            "aiAssets": "AI Assets",
            "periodAssets": "Period Assets",
            "periodPage": "Period Page",
            "periodSummary": "Period Summary",
        },
        "rerunDailyPipelineAction": "Re-run the daily pipeline or affected pass for {day}, then materialize Foundation outputs.",
        "foundationRepairAction": "Run Foundation shadow/materialization repair for {day} and inspect readiness details.",
        "inspectPipelineFailureAction": "Inspect the recorded daily pipeline failure step before retrying.",
        "checkProductionReadinessAction": "Check production readiness blockers for snapshot/projection completeness.",
        "runFullDailyPipeline": "Run Full Daily Pipeline",
        "rematerializeDiaryMarkdown": "Re-materialize Generated Diary Markdown",
        "retryDailyPipeline": "Retry Daily Pipeline After Inspecting Failure",
        "inspectProductionReadiness": "Inspect Production Readiness Details",
        "dashboardExecutableReason": "Dashboard can execute this allowlisted action after typed confirmation and audit logging.",
        "manualOnlyReason": "Dashboard execution is disabled for this action; use the copyable command manually.",
        "readOnlyAuditReason": "Read-only execution still waits for repair action audit storage.",
    },
}


def _coerce_jobs(refresh_jobs: dict | list | None) -> list[dict]:
    if isinstance(refresh_jobs, dict):
        jobs = refresh_jobs.get("jobs")
    else:
        jobs = refresh_jobs
    return jobs if isinstance(jobs, list) else []


def _current_failed_job(refresh_jobs: dict | list | None, jobs: list[dict]) -> dict | None:
    latest = refresh_jobs.get("latest") if isinstance(refresh_jobs, dict) else None
    if not isinstance(latest, dict) and jobs:
        latest = jobs[0]
    if isinstance(latest, dict) and latest.get("status") == "failed":
        return latest
    return None


def _latest_historical_failed(refresh_jobs: dict | list | None, jobs: list[dict]) -> dict | None:
    if isinstance(refresh_jobs, dict) and isinstance(refresh_jobs.get("latestFailed"), dict):
        return refresh_jobs["latestFailed"]
    return next((job for job in jobs if job.get("status") == "failed"), None)


def _select_timer_job(jobs: object, kind: str) -> dict:
    if not isinstance(jobs, list):
        return {}
    return next((job for job in jobs if isinstance(job, dict) and job.get("kind") == kind), {})


def _time_from_state(state: dict, key: str) -> str | None:
    value = state.get(key)
    return value if isinstance(value, str) and len(value) == 5 else None


def _timezone_from_status(scheduler_status: dict) -> str:
    value = scheduler_status.get("timezone")
    if isinstance(value, str):
        try:
            ZoneInfo(value)
            return value
        except Exception:
            pass
    return resolve_timezone_name(group="schedule")


def _next_local_run(now: datetime, hhmm: str) -> datetime:
    hour, minute = _parse_hhmm(hhmm, fallback=(4, 30))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _parse_hhmm(value: str, *, fallback: tuple[int, int]) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (AttributeError, ValueError):
        pass
    return fallback


def _cadence_status(enabled: bool, supported: bool, latest_failed: dict | None) -> str:
    if not supported:
        return "unsupported"
    if latest_failed is not None:
        return "attention"
    if enabled:
        return "scheduled"
    return "manual"
