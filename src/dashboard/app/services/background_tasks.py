"""Read-only Dashboard background task monitor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agentic_rag.rag_server_lifecycle import read_server_process_state
from agentic_rag.rag_settings import resolve_rag_settings

from . import external_rag_skill_registration, foundation, foundation_ops, rag_index_jobs, scheduler
from .ui_text import dashboard_language_profile, is_english_profile


ACTIVE_STATUSES = {"scheduled", "queued", "running", "starting", "stopping", "cancel_requested"}


def get_background_tasks(*, limit: int = 30) -> dict[str, Any]:
    """Return a read-only snapshot of active and recent background work."""
    limit = max(1, min(int(limit), 100))
    profile = dashboard_language_profile()
    degraded: list[dict[str, Any]] = []
    refresh_jobs: list[dict[str, Any]]
    repair_jobs: list[dict[str, Any]]
    try:
        refresh_payload = foundation.list_refresh_jobs(limit=limit)
        refresh_jobs = [
            _normalize_refresh_job(job, profile=profile)
            for job in (refresh_payload.get("jobs") or [])
            if isinstance(job, dict)
        ]
    except Exception as exc:
        degraded.append(_degraded_source("foundationRefreshJobs", "foundation-refresh", exc))
        refresh_jobs = [_source_failure_task("foundation-refresh-status", "foundation-refresh", _ui("foundationRefreshUnavailable", profile), exc)]
    try:
        repair_payload = foundation_ops.list_foundation_repair_runs(limit=limit)
        repair_jobs = [
            _normalize_repair_run(run, profile=profile)
            for run in (repair_payload.get("runs") or [])
            if isinstance(run, dict)
        ]
    except Exception as exc:
        degraded.append(_degraded_source("foundationRepairRuns", "foundation-repair", exc))
        repair_jobs = [_source_failure_task("foundation-repair-status", "foundation-repair", _ui("foundationRepairUnavailable", profile), exc)]
    scheduler_tasks = _scheduler_tasks(profile=profile)
    rag_index_tasks = _rag_index_tasks(limit, profile=profile)
    rag_skill_tasks = _rag_skill_registration_tasks(limit, profile=profile)
    tasks = refresh_jobs + repair_jobs + rag_index_tasks + rag_skill_tasks + scheduler_tasks
    service_statuses = [service for service in [_rag_lifecycle_service(profile=profile)] if service]
    tasks.sort(key=lambda item: item.get("sortAt") or "", reverse=True)
    active = [task for task in tasks if task.get("status") in ACTIVE_STATUSES]
    task_summary = _task_summary(tasks, active, service_statuses)
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "activeCount": len(active),
        "tasks": tasks[:limit],
        "active": active[:limit],
        "services": service_statuses,
        "summary": task_summary,
        "degraded": degraded,
        "degradedCount": len(degraded),
        "sources": {
            "foundationRefreshJobs": sum(1 for job in refresh_jobs if job.get("source") == "foundation-refresh" and not job.get("degraded")),
            "historyBackfillJobs": sum(1 for job in refresh_jobs if job.get("source") == "history-backfill" and not job.get("degraded")),
            "foundationRepairRuns": sum(1 for job in repair_jobs if not job.get("degraded")),
            "ragCandidateRefreshJobs": len(rag_index_tasks),
            "ragSkillRegistrationJobs": len(rag_skill_tasks),
            "schedulerJobs": len(scheduler_tasks),
            "ragLifecycle": bool(service_statuses),
        },
    }


def _degraded_source(source_id: str, source: str, error: Exception) -> dict[str, Any]:
    return {
        "id": source_id,
        "source": source,
        "status": "degraded",
        "error": str(error),
    }


def _source_failure_task(task_id: str, source: str, title: str, error: Exception) -> dict[str, Any]:
    return {
        "id": task_id,
        "source": source,
        "title": title,
        "subtitle": str(error),
        "status": "failed",
        "progress": 100,
        "sortAt": datetime.now().astimezone().isoformat(),
        "errorSummary": str(error),
        "metadata": {},
        "actions": [],
        "degraded": True,
    }


def _task_summary(tasks: list[dict[str, Any]], active: list[dict[str, Any]], services: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for task in tasks:
        source = str(task.get("source") or "unknown")
        status = str(task.get("status") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "activeTasks": len(active),
        "recentTasks": len(tasks),
        "services": len(services),
        "bySource": by_source,
        "byStatus": by_status,
    }


def _normalize_refresh_job(job: dict[str, Any], *, profile: str = "zh") -> dict[str, Any]:
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    status = str(job.get("status") or "unknown")
    trigger_type = str(job.get("trigger_type") or "")
    period = _period_label(metadata, job.get("business_date"))
    progress = metadata.get("progress")
    usage_cache = metadata.get("usageCache") if isinstance(metadata.get("usageCache"), dict) else {}
    is_history_backfill = trigger_type == "dashboard-history-backfill"
    actions: list[dict[str, Any]] = []
    if is_history_backfill and status in {"scheduled", "queued", "running"}:
        actions.append(
            {
                "kind": "apiPost",
                "label": _ui("cancel", profile),
                "url": f"/api/foundation/history-backfill/{job.get('id')}/cancel",
                "confirm": _ui("cancelHistoryConfirm", profile),
                "refreshBackgroundTasks": True,
            }
        )
    elif is_history_backfill and status == "cancel_requested":
        actions.append({"kind": "disabled", "label": _ui("cancelling", profile)})
    elif is_history_backfill and status in {"partial", "failed"} and _history_backfill_has_retryable_failures(metadata):
        actions.append(
            {
                "kind": "apiPost",
                "label": _ui("retryFailed", profile),
                "url": f"/api/foundation/history-backfill/{job.get('id')}/retry-failed",
                "confirm": _ui("retryFailedConfirm", profile),
                "refreshBackgroundTasks": True,
                "successMessage": _ui("retryFailedSubmitted", profile),
            }
        )
    return {
        "id": f"history-backfill-{job.get('id')}" if is_history_backfill else f"foundation-refresh-{job.get('id')}",
        "source": "history-backfill" if is_history_backfill else "foundation-refresh",
        "title": _refresh_title(trigger_type, period, profile=profile),
        "subtitle": _history_backfill_subtitle(period, metadata, profile=profile) if is_history_backfill else _refresh_subtitle(period, metadata, profile=profile),
        "status": status,
        "progress": int(progress) if isinstance(progress, int | float) else _status_progress(status),
        "startedAt": job.get("started_at"),
        "completedAt": job.get("completed_at"),
        "sortAt": job.get("started_at") or job.get("completed_at") or "",
        "errorSummary": job.get("error_summary"),
        "metadata": {**metadata, "usageCacheSummary": _usage_cache_summary(usage_cache, profile=profile)},
        "actions": actions,
    }


def _normalize_repair_run(run: dict[str, Any], *, profile: str = "zh") -> dict[str, Any]:
    status = str(run.get("status") or "unknown")
    action_id = str(run.get("actionId") or run.get("action_id") or "repair")
    business_date = run.get("businessDate") or run.get("business_date")
    return {
        "id": f"foundation-repair-{run.get('id')}",
        "source": "foundation-repair",
        "title": _ui("dailyQaRepair", profile) + str(action_id),
        "subtitle": _ui("businessDatePrefix", profile) + str(business_date or "-"),
        "status": status,
        "progress": _status_progress(status),
        "startedAt": run.get("startedAt") or run.get("started_at") or run.get("requestedAt") or run.get("requested_at"),
        "completedAt": run.get("completedAt") or run.get("completed_at"),
        "sortAt": run.get("startedAt") or run.get("started_at") or run.get("requestedAt") or run.get("requested_at") or "",
        "errorSummary": run.get("errorSummary") or run.get("error_summary"),
        "metadata": {
            "actionClass": run.get("actionClass") or run.get("action_class"),
            "exitCode": run.get("exitCode") or run.get("exit_code"),
        },
    }


def _scheduler_tasks(*, profile: str = "zh") -> list[dict[str, Any]]:
    try:
        status = scheduler.scheduler_status()
    except Exception as exc:
        return [
            {
                "id": "scheduler-status",
                "source": "scheduler",
                "title": _ui("schedulerUnavailable", profile),
                "subtitle": str(exc),
                "status": "failed",
                "progress": 100,
                "sortAt": datetime.now().astimezone().isoformat(),
                "errorSummary": str(exc),
                "metadata": {},
            }
        ]
    timer = status.get("systemTimer") if isinstance(status.get("systemTimer"), dict) else {}
    tasks: list[dict[str, Any]] = []
    for job in timer.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        registered = bool(timer.get("registered"))
        tasks.append(
            {
                "id": f"scheduler-{job.get('kind') or job.get('label')}",
                "source": "scheduler",
                "title": f"LaunchAgent: {job.get('label') or job.get('kind')}",
                "subtitle": _scheduler_subtitle(job, profile=profile),
                "status": "configured" if registered else "not-registered",
                "progress": 100 if registered else 0,
                "sortAt": (status.get("state") or {}).get("lastDashboardAggregationAt") or "",
                "metadata": {
                    "provider": timer.get("provider"),
                    "registered": registered,
                    "plistPath": job.get("plistPath"),
                    "time": job.get("time"),
                },
            }
        )
    return tasks


def _rag_index_tasks(limit: int, *, profile: str = "zh") -> list[dict[str, Any]]:
    try:
        jobs = rag_index_jobs.list_candidate_refresh_jobs(limit=limit)
    except Exception as exc:
        return [
            {
                "id": "rag-candidate-refresh-status",
                "source": "rag",
                "title": _ui("ragCandidateUnavailable", profile),
                "subtitle": str(exc),
                "status": "failed",
                "progress": 100,
                "sortAt": datetime.now().astimezone().isoformat(),
                "errorSummary": str(exc),
                "metadata": {},
            }
        ]
    tasks: list[dict[str, Any]] = []
    for job in jobs:
        status = str(job.get("status") or "unknown")
        job_type = str(job.get("type") or "")
        tasks.append(
            {
                "id": str(job.get("id") or "rag-candidate-refresh"),
                "source": "rag",
                "title": _ui("ragProfileMigration", profile) if job_type == "rag-profile-migration" else _ui("ragCandidateRefresh", profile),
                "subtitle": _rag_index_subtitle(job, profile=profile),
                "status": status,
                "progress": int(job.get("progress") or _status_progress(status)),
                "startedAt": job.get("startedAt") or job.get("requestedAt"),
                "completedAt": job.get("completedAt"),
                "sortAt": job.get("completedAt") or job.get("startedAt") or job.get("requestedAt") or "",
                "errorSummary": job.get("errorSummary"),
                "metadata": job,
            }
        )
    return tasks


def _rag_lifecycle_service(*, profile: str = "zh") -> dict[str, Any] | None:
    try:
        settings = resolve_rag_settings()
        lifecycle = read_server_process_state(settings, probe_health=False)
    except Exception as exc:
        return {
            "id": "rag-server-lifecycle",
            "source": "rag",
            "title": _ui("ragLifecycleUnavailable", profile),
            "subtitle": str(exc),
            "status": "failed",
            "progress": 100,
            "sortAt": datetime.now().astimezone().isoformat(),
            "errorSummary": str(exc),
            "metadata": {},
        }
    status = str(lifecycle.get("status") or "unknown")
    if status in {"unknown", "missing"}:
        return None
    return {
        "id": "rag-server-lifecycle",
        "source": "rag",
        "kind": "service",
        "title": "nova-RAG server",
        "subtitle": lifecycle.get("logPath") or lifecycle.get("statePath") or "",
        "status": status,
        "progress": _status_progress(status),
        "startedAt": lifecycle.get("startedAt") or lifecycle.get("requestedAt"),
        "completedAt": lifecycle.get("stoppedAt"),
        "sortAt": lifecycle.get("startedAt") or lifecycle.get("requestedAt") or lifecycle.get("updatedAt") or "",
        "errorSummary": lifecycle.get("lastError"),
        "metadata": lifecycle,
    }


def _rag_skill_registration_tasks(limit: int, *, profile: str = "zh") -> list[dict[str, Any]]:
    try:
        jobs = external_rag_skill_registration.list_rag_skill_registration_jobs(limit=limit)
    except Exception as exc:
        return [
            {
                "id": "rag-skill-registration-status",
                "source": "rag",
                "title": _ui("ragSkillRegistrationUnavailable", profile),
                "subtitle": str(exc),
                "status": "failed",
                "progress": 100,
                "sortAt": datetime.now().astimezone().isoformat(),
                "errorSummary": str(exc),
                "metadata": {},
            }
        ]
    return [
        {
            "id": str(job.get("id") or "rag-skill-registration"),
            "source": "rag",
            "title": _ui("ragExternalSkillRegistration", profile),
            "subtitle": _targets_subtitle(len(job.get("operations") or []), profile=profile),
            "status": str(job.get("status") or "unknown"),
            "progress": int(job.get("progress") or _status_progress(str(job.get("status") or ""))),
            "startedAt": job.get("startedAt") or job.get("requestedAt"),
            "completedAt": job.get("completedAt"),
            "sortAt": job.get("completedAt") or job.get("requestedAt") or "",
            "errorSummary": job.get("errorSummary"),
            "metadata": job,
        }
        for job in jobs
    ]


def _refresh_title(trigger_type: str | None, period: str, *, profile: str = "zh") -> str:
    if trigger_type == "pipeline-foundation-materialization":
        return _ui("dailyPipelineMaterialization", profile) + period
    if trigger_type == "dashboard-period-summary-refresh":
        return _ui("periodSummaryRefresh", profile) + period
    if trigger_type == "dashboard-history-backfill":
        return _ui("historyDataBackfill", profile) + period
    return _ui("foundationSnapshotRefresh", profile) + period


def _history_backfill_subtitle(period: str, metadata: dict[str, Any], *, profile: str = "zh") -> str:
    stage = metadata.get("currentStageLabel") or metadata.get("currentStage")
    daily = metadata.get("dailyPipeline") if isinstance(metadata.get("dailyPipeline"), dict) else {}
    parts = [period]
    if stage:
        parts.append(str(stage))
    daily_total = daily.get("total")
    if daily_total is not None:
        completed = len(daily.get("completed") or [])
        skipped = len(daily.get("skipped") or [])
        failed = len(daily.get("failed") or [])
        parts.append(_daily_progress(completed + skipped, daily_total, failed, profile=profile))
    period_counts = []
    for key, label in (
        ("completedPeriods", "completed"),
        ("skippedPeriods", "skipped"),
        ("failedPeriods", "failed"),
    ):
        if metadata.get(key) is not None:
            period_counts.append(f"{_ui(label, profile)}={metadata.get(key)}")
    if period_counts:
        parts.append(_ui("periodsPrefix", profile) + ", ".join(period_counts))
    return " | ".join(parts)


def _history_backfill_has_retryable_failures(metadata: dict[str, Any]) -> bool:
    if metadata.get("outcomeSchemaVersion") == 2:
        retry_stages = metadata.get("retryStages") if isinstance(metadata.get("retryStages"), list) else []
        return bool(retry_stages)
    failed_periods = metadata.get("failedPeriodDetails") if isinstance(metadata.get("failedPeriodDetails"), list) else []
    if failed_periods:
        return True
    daily = metadata.get("dailyPipeline") if isinstance(metadata.get("dailyPipeline"), dict) else {}
    failed_days = daily.get("failed") if isinstance(daily.get("failed"), list) else []
    return bool(failed_days)


def _period_label(metadata: dict[str, Any], business_date: object) -> str:
    start = metadata.get("periodStart")
    end = metadata.get("periodEnd") or business_date
    if start and end:
        return f"{start}..{end}"
    return str(business_date or "-")


def _refresh_subtitle(period: str, metadata: dict[str, Any], *, profile: str = "zh") -> str:
    stage = metadata.get("currentStageLabel") or metadata.get("currentStage")
    usage_cache = metadata.get("usageCache") if isinstance(metadata.get("usageCache"), dict) else {}
    usage_summary = _usage_cache_summary(usage_cache, profile=profile)
    work_summary = _work_estimate_summary(
        metadata.get("workEstimate") if isinstance(metadata.get("workEstimate"), dict) else {},
        profile=profile,
    )
    parts = [period]
    if stage:
        parts.append(str(stage))
    if work_summary:
        parts.append(work_summary)
    if usage_summary:
        parts.append(usage_summary)
    return " | ".join(parts)


def _work_estimate_summary(work_estimate: dict[str, Any], *, profile: str = "zh") -> str:
    if not work_estimate:
        return ""
    parts = []
    if work_estimate.get("periodDays") is not None:
        parts.append(f"{_ui('periodDays', profile)}={work_estimate.get('periodDays')}")
    if work_estimate.get("llmCalls") is not None:
        parts.append(f"{_ui('llmCalls', profile)}={work_estimate.get('llmCalls')}")
    if work_estimate.get("longRunning"):
        parts.append(_ui("longRunning", profile))
    return _ui("workEstimatePrefix", profile) + ", ".join(parts) if parts else ""


def _usage_cache_summary(usage_cache: dict[str, Any], *, profile: str = "zh") -> str:
    if not usage_cache:
        return ""
    interesting = []
    for key in ("sources", "cached", "reparsed", "removed", "errors"):
        if usage_cache.get(key) is not None:
            interesting.append(f"{key}={usage_cache.get(key)}")
    return _ui("usageCachePrefix", profile) + ", ".join(interesting) if interesting else ""


def _status_progress(status: str) -> int:
    return {
        "scheduled": 5,
        "queued": 5,
        "starting": 15,
        "running": 45,
        "cancel_requested": 75,
        "stopping": 75,
        "completed": 100,
        "ready": 100,
        "partial": 100,
        "stopped": 100,
        "failed": 100,
        "cancelled": 100,
        "configured": 100,
    }.get(status, 0)


def _rag_index_subtitle(job: dict[str, Any], *, profile: str = "zh") -> str:
    source_sets = job.get("sourceSets") if isinstance(job.get("sourceSets"), list) else []
    counts = []
    if job.get("chunkCount") is not None:
        counts.append(f"chunks={job.get('chunkCount')}")
    if job.get("embeddingCount") is not None:
        counts.append(f"embeddings={job.get('embeddingCount')}")
    prefix = ", ".join(counts) if counts else f"provider={job.get('providerId') or job.get('embeddingProvider') or '-'}"
    if source_sets:
        return f"{prefix}; {_ui('sources', profile)}={len(source_sets)}"
    return prefix


def _scheduler_subtitle(job: dict[str, Any], *, profile: str = "zh") -> str:
    return f"{job.get('kind') or _ui('job', profile)} {_ui('at', profile)} {job.get('time') or '-'}"


def _targets_subtitle(count: int, *, profile: str = "zh") -> str:
    return f"{count} {_ui('targets', profile)}"


def _daily_progress(done: object, total: object, failed: object, *, profile: str = "zh") -> str:
    return f"{_ui('daily', profile)} {done}/{total}, {_ui('failed', profile)}={failed}"


def _ui(key: str, profile: str) -> str:
    text = _UI_TEXT["en" if is_english_profile(profile) else "zh"]
    return text[key]


_UI_TEXT = {
    "zh": {
        "cancel": "取消",
        "cancelHistoryConfirm": "确认取消这个历史数据生成任务？当前正在运行的子步骤会先结束，然后停止后续日期/周期。",
        "cancelling": "取消中",
        "retryFailed": "重跑失败项",
        "retryFailedConfirm": "确认只重跑这个历史数据生成任务中的失败日期/周期？",
        "retryFailedSubmitted": "已提交失败项重跑任务",
        "dailyQaRepair": "Daily QA repair: ",
        "foundationRefreshUnavailable": "Foundation refresh jobs unavailable",
        "foundationRepairUnavailable": "Foundation repair runs unavailable",
        "businessDatePrefix": "business date ",
        "schedulerUnavailable": "Scheduler status unavailable",
        "ragCandidateUnavailable": "RAG candidate refresh unavailable",
        "ragProfileMigration": "RAG profile migration",
        "ragCandidateRefresh": "RAG candidate index refresh",
        "ragLifecycleUnavailable": "nova-RAG server lifecycle unavailable",
        "ragSkillRegistrationUnavailable": "RAG skill registration unavailable",
        "ragExternalSkillRegistration": "RAG external agent skill registration",
        "dailyPipelineMaterialization": "Daily pipeline materialization: ",
        "periodSummaryRefresh": "Period summary refresh: ",
        "historyDataBackfill": "History data backfill: ",
        "foundationSnapshotRefresh": "Foundation snapshot refresh: ",
        "daily": "daily",
        "failed": "failed",
        "completed": "completed",
        "skipped": "skipped",
        "periodsPrefix": "periods ",
        "usageCachePrefix": "usage cache ",
        "workEstimatePrefix": "estimated work ",
        "periodDays": "days",
        "llmCalls": "LLM calls",
        "longRunning": "long-running",
        "sources": "sources",
        "job": "job",
        "at": "at",
        "targets": "target(s)",
    },
    "en": {
        "cancel": "Cancel",
        "cancelHistoryConfirm": "Cancel this historical data generation task? The current sub-step will finish first, then later dates/periods will stop.",
        "cancelling": "Cancelling",
        "retryFailed": "Retry Failed Items",
        "retryFailedConfirm": "Retry only the failed dates/periods in this historical data generation task?",
        "retryFailedSubmitted": "Failed-item retry task submitted",
        "dailyQaRepair": "Daily QA repair: ",
        "foundationRefreshUnavailable": "Foundation refresh jobs unavailable",
        "foundationRepairUnavailable": "Foundation repair runs unavailable",
        "businessDatePrefix": "business date ",
        "schedulerUnavailable": "Scheduler status unavailable",
        "ragCandidateUnavailable": "RAG candidate refresh unavailable",
        "ragProfileMigration": "RAG profile migration",
        "ragCandidateRefresh": "RAG candidate index refresh",
        "ragLifecycleUnavailable": "nova-RAG server lifecycle unavailable",
        "ragSkillRegistrationUnavailable": "RAG skill registration unavailable",
        "ragExternalSkillRegistration": "RAG external agent skill registration",
        "dailyPipelineMaterialization": "Daily pipeline materialization: ",
        "periodSummaryRefresh": "Period summary refresh: ",
        "historyDataBackfill": "History data backfill: ",
        "foundationSnapshotRefresh": "Foundation snapshot refresh: ",
        "daily": "daily",
        "failed": "failed",
        "completed": "completed",
        "skipped": "skipped",
        "periodsPrefix": "periods ",
        "usageCachePrefix": "usage cache ",
        "workEstimatePrefix": "estimated work ",
        "periodDays": "days",
        "llmCalls": "LLM calls",
        "longRunning": "long-running",
        "sources": "sources",
        "job": "job",
        "at": "at",
        "targets": "target(s)",
    },
}
