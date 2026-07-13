"""Dashboard message box aggregation."""

from __future__ import annotations

import json
import fcntl
from datetime import datetime
from pathlib import Path

from data_foundation.paths import load_paths
from data_foundation.pipeline import latest_pipeline_failure
from data_foundation.pipeline_runs import list_pipeline_runs

from . import foundation, nova_task_review
from .ui_text import dashboard_language_profile, is_english_profile

MAX_MSGBOX_LIMIT = 100
MAX_READ_STATE_BYTES = 256 * 1024


def message_box(limit: int = 20) -> dict:
    profile = dashboard_language_profile()
    selected_limit = _normalize_limit(limit)
    items: list[dict] = []
    degraded: list[dict] = []
    for source_id, producer in (
        ("pipeline-foundation", lambda: _pipeline_failure_messages(profile=profile)),
        ("nova-task-candidates", lambda: _task_candidate_messages(limit=selected_limit, profile=profile)),
    ):
        try:
            items.extend(producer())
        except Exception as exc:
            degraded.append(_degraded_source(source_id, exc))
            items.append(_degraded_message(source_id, exc, profile=profile))
    read_ids = _read_message_ids()
    items = [item for item in items if str(item.get("id") or "") not in read_ids]
    items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    visible = items[:selected_limit]
    return {
        "items": visible,
        "count": len(visible),
        "attentionCount": sum(1 for item in visible if item.get("severity") in {"error", "warn"}),
        "degraded": degraded,
        "degradedCount": len(degraded),
        "generatedAt": datetime.now().astimezone().isoformat(),
    }


def mark_read(message_id: str) -> dict:
    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("message_id is required")
    path = _read_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            read_ids = _read_message_ids()
            read_ids.add(message_id)
            _write_json_atomic(path, sorted(read_ids))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return {"status": "ok", "messageId": message_id}


def _read_state_path() -> Path:
    return load_paths().state_dir / "dashboard" / "msgbox-read.json"


def _read_message_ids() -> set[str]:
    path = _read_state_path()
    if not path.exists():
        return set()
    try:
        if path.stat().st_size > MAX_READ_STATE_BYTES:
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data if item}


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit or 20)
    except (TypeError, ValueError):
        value = 20
    return max(1, min(value, MAX_MSGBOX_LIMIT))


def _write_json_atomic(path: Path, payload: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _degraded_source(source_id: str, error: Exception) -> dict:
    return {"id": source_id, "status": "degraded", "error": str(error)}


def _degraded_message(source_id: str, error: Exception, *, profile: str) -> dict:
    return {
        "id": f"msgbox-source-degraded-{source_id}",
        "type": "source_degraded",
        "severity": "warn",
        "title": _ui("messageSourceDegraded", profile),
        "summary": str(error),
        "createdAt": datetime.now().astimezone().isoformat(),
        "actionLabel": _ui("viewOpsPanel", profile),
        "action": {"kind": "openPage", "page": "foundation-ops"},
        "details": {"source": source_id, "error": str(error)},
    }


def _pipeline_failure_messages(*, profile: str = "zh") -> list[dict]:
    messages = []
    latest_pipeline = _latest_daily_pipeline_failure()
    if latest_pipeline:
        messages.append(
            {
                "id": f"daily-pipeline-failure-{latest_pipeline.get('businessDate')}-{latest_pipeline.get('createdAt')}",
                "type": "pipeline_failure",
                "severity": "error",
                "title": _ui("dailyPipelineFailed", profile),
                "summary": latest_pipeline.get("reason") or latest_pipeline.get("failedStep") or "Daily pipeline failed.",
                "createdAt": latest_pipeline.get("createdAt"),
                "actionLabel": _ui("viewOpsPanel", profile),
                "action": {"kind": "openPage", "page": "foundation-ops"},
                "details": latest_pipeline,
            }
        )
    jobs = foundation.list_refresh_jobs(limit=20)
    latest_failed = jobs.get("latestFailed") if isinstance(jobs, dict) else None
    messages.extend(_history_backfill_failure_messages(jobs.get("jobs") if isinstance(jobs, dict) else [], profile=profile))
    messages.extend(_pipeline_catchup_confirmation_messages(profile=profile))
    if not isinstance(latest_failed, dict):
        return messages
    meta = latest_failed.get("metadata") if isinstance(latest_failed.get("metadata"), dict) else {}
    messages.append(
        {
            "id": f"foundation-refresh-failure-{latest_failed.get('id')}",
            "type": "pipeline_failure",
            "severity": "error",
            "title": _ui("foundationMaterializationFailed", profile),
            "summary": latest_failed.get("error_summary") or "Foundation refresh failed.",
            "createdAt": latest_failed.get("completed_at") or latest_failed.get("started_at"),
            "actionLabel": _ui("viewOpsPanel", profile),
            "action": {"kind": "openPage", "page": "foundation-ops"},
            "details": {
                "runId": latest_failed.get("id"),
                "status": latest_failed.get("status"),
                "businessDate": latest_failed.get("business_date"),
                "scope": meta.get("scope") or latest_failed.get("trigger_type"),
                "error": latest_failed.get("error_summary"),
            },
        }
    )
    return messages


def _pipeline_catchup_confirmation_messages(*, profile: str = "zh") -> list[dict]:
    messages: list[dict] = []
    try:
        runs = list_pipeline_runs(load_paths(), statuses={"blocked"}, limit=20)
    except Exception:
        return []
    for run in runs:
        if run.get("runKind") != "catchup_reconcile" or run.get("failureClass") != "manual_confirmation_required":
            continue
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        missing = metadata.get("missingDates") if isinstance(metadata.get("missingDates"), list) else []
        if not missing:
            continue
        messages.append(
            {
                "id": f"pipeline-catchup-confirmation-{run.get('id')}-{len(missing)}",
                "type": "pipeline_catchup_confirmation",
                "severity": "warn",
                "title": _ui("pipelineCatchupNeedsConfirmation", profile),
                "summary": _pipeline_catchup_summary(missing, profile),
                "createdAt": run.get("updated_at") or run.get("created_at"),
                "actionLabel": _ui("viewOpsPanel", profile),
                "action": {"kind": "openPage", "page": "foundation-ops"},
                "details": {
                    "runId": run.get("id"),
                    "missingDates": missing,
                    "autoLimitExceeded": True,
                    "error": run.get("errorSummary"),
                },
            }
        )
    return messages


def _history_backfill_failure_messages(jobs: list[dict] | None, *, profile: str = "zh") -> list[dict]:
    messages: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict) or job.get("trigger_type") != "dashboard-history-backfill":
            continue
        if job.get("status") not in {"partial", "failed"}:
            continue
        meta = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
        retry_stages = meta.get("retryStages") if isinstance(meta.get("retryStages"), list) else []
        native_outcomes = meta.get("outcomeSchemaVersion") == 2
        failed_periods = meta.get("failedPeriodDetails") if isinstance(meta.get("failedPeriodDetails"), list) else []
        daily = meta.get("dailyPipeline") if isinstance(meta.get("dailyPipeline"), dict) else {}
        failed_days = daily.get("failed") if isinstance(daily.get("failed"), list) else []
        if native_outcomes and not retry_stages:
            continue
        if not native_outcomes and not failed_periods and not failed_days:
            continue
        run_id = job.get("id")
        failed_count = len(retry_stages) if native_outcomes else len(failed_periods) + len(failed_days)
        messages.append(
            {
                "id": f"history-backfill-failure-{run_id}-{failed_count}",
                "type": "history_backfill_failure",
                "severity": "warn",
                "title": _ui("historyBackfillPartialFailed", profile),
                "summary": _history_backfill_failure_summary(run_id, failed_count, profile),
                "createdAt": job.get("completed_at") or job.get("started_at"),
                "actionLabel": _ui("retryFailed", profile),
                "action": {
                    "kind": "apiPost",
                    "url": f"/api/foundation/history-backfill/{run_id}/retry-failed",
                    "successMessage": _ui("retryFailedSubmitted", profile),
                    "refreshBackgroundTasks": True,
                },
                "details": {
                    "runId": run_id,
                    "failedPeriods": failed_periods,
                    "failedDailyPipelineDays": failed_days,
                    "retryStages": retry_stages,
                    "error": job.get("error_summary"),
                },
            }
        )
    return messages


def _latest_daily_pipeline_failure() -> dict | None:
    return latest_pipeline_failure(load_paths())


def _task_candidate_messages(limit: int, *, profile: str = "zh") -> list[dict]:
    candidate_limit = max(50, int(limit or 20))
    data = nova_task_review.candidates(status="pending_review", limit=candidate_limit)
    candidates = data.get("candidates") if isinstance(data, dict) else []
    if not isinstance(candidates, list) or not candidates:
        return []
    pending_review_count = int(
        data.get("pendingReviewCount", data.get("pendingCount", len(candidates))) or len(candidates)
    )
    parent_count = sum(1 for item in candidates if _candidate_type(item) == "parent")
    subtask_count = sum(1 for item in candidates if _candidate_type(item) == "subtask")
    status_update_count = sum(1 for item in candidates if _candidate_type(item) == "status_update")
    latest_time = _latest_candidate_time(candidates)
    batch_id = f"nova-task-candidates-pending-review-{pending_review_count}-{latest_time or 'unknown'}"
    return [
        {
            "id": batch_id,
            "type": "task_candidate_review",
            "severity": "warn",
            "title": _ui("taskCandidatesPending", profile),
            "summary": _task_candidate_summary(
                pending_review_count,
                parent_count,
                subtask_count,
                status_update_count,
                profile,
            ),
            "createdAt": latest_time,
            "actionLabel": _ui("openTaskBoard", profile),
            "action": {"kind": "openUrl", "url": "/tasks"},
            "details": {
                "pendingReviewCount": pending_review_count,
                "pendingCount": pending_review_count,
                "parentCount": parent_count,
                "subtaskCount": subtask_count,
                "statusUpdateCount": status_update_count,
                "sampleTitles": [str(item.get("proposedTitle") or item.get("proposed_title") or "") for item in candidates[:5]],
            },
        }
    ]


def _candidate_type(candidate: dict) -> str:
    raw_type = str(candidate.get("candidateType") or candidate.get("candidate_type") or "").lower()
    if raw_type in {"parent", "parent_task", "candidate_parent"}:
        return "parent"
    if raw_type in {"subtask", "candidate_subtask"}:
        return "subtask"
    if raw_type in {"status_update", "completion_signal"}:
        return "status_update"
    return raw_type


def _latest_candidate_time(candidates: list[dict]) -> str | None:
    values = [str(item.get("createdAt") or item.get("created_at") or "") for item in candidates]
    values = [value for value in values if value]
    return max(values) if values else None


def _history_backfill_failure_summary(run_id: object, failed_count: int, profile: str) -> str:
    if is_english_profile(profile):
        return f"Run #{run_id} has {failed_count} failed item(s). You can retry only the failed dates/periods."
    return f"Run #{run_id} 有 {failed_count} 个失败项，可单独重跑失败日期/周期。"


def _pipeline_catchup_summary(missing_dates: list, profile: str) -> str:
    dates = [str(item) for item in missing_dates]
    sample = ", ".join(dates[:5])
    suffix = "" if len(dates) <= 5 else f" +{len(dates) - 5}"
    if is_english_profile(profile):
        return f"{len(dates)} daily pipeline date(s) are missing ({sample}{suffix}); confirmation is required before catch-up."
    return f"缺失 {len(dates)} 天每日管线（{sample}{suffix}），超过自动补跑上限，需要确认后再补跑。"


def _task_candidate_summary(
    pending_review_count: int,
    parent_count: int,
    subtask_count: int,
    status_update_count: int,
    profile: str,
) -> str:
    if is_english_profile(profile):
        return (
            f"{pending_review_count} L1 proposal(s) awaiting review."
        )
    return f"{pending_review_count} 条 L1 提案待确认。"


def _ui(key: str, profile: str) -> str:
    text = _UI_TEXT["en" if is_english_profile(profile) else "zh"]
    return text[key]


_UI_TEXT = {
    "zh": {
        "dailyPipelineFailed": "每日管线运行失败",
        "foundationMaterializationFailed": "Foundation materialization 失败",
        "historyBackfillPartialFailed": "历史数据生成部分失败",
        "pipelineCatchupNeedsConfirmation": "每日管线缺失较多，需要确认补跑",
        "taskCandidatesPending": "有新的 L1 提案待确认",
        "viewOpsPanel": "查看运维面板",
        "retryFailed": "重跑失败项",
        "retryFailedSubmitted": "已提交失败项重跑任务",
        "openTaskBoard": "打开任务看板",
        "messageSourceDegraded": "消息来源部分不可用",
    },
    "en": {
        "dailyPipelineFailed": "Daily pipeline failed",
        "foundationMaterializationFailed": "Foundation materialization failed",
        "historyBackfillPartialFailed": "Historical data generation partially failed",
        "pipelineCatchupNeedsConfirmation": "Daily pipeline catch-up needs confirmation",
        "taskCandidatesPending": "New L1 proposals need review",
        "viewOpsPanel": "View Ops Panel",
        "retryFailed": "Retry Failed Items",
        "retryFailedSubmitted": "Failed-item retry task submitted",
        "openTaskBoard": "Open Task Board",
        "messageSourceDegraded": "Message source partially unavailable",
    },
}
