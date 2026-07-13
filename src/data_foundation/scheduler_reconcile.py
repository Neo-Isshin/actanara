"""Scheduler catch-up reconciliation for laptop sleep/wake gaps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .paths import RuntimePaths, load_paths
from .pipeline import run_daily_pipeline
from .pipeline_runs import (
    AUTO_CATCHUP_LIMIT_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    pipeline_reconcile_plan,
    record_reconcile_blocked,
)


def reconcile_pipeline_schedule(
    paths: RuntimePaths | None = None,
    *,
    now: datetime | None = None,
    apply: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    auto_limit_days: int = AUTO_CATCHUP_LIMIT_DAYS,
) -> dict[str, Any]:
    selected = paths or load_paths()
    plan = pipeline_reconcile_plan(
        selected,
        now=now,
        lookback_days=lookback_days,
        auto_limit_days=auto_limit_days,
    )
    result: dict[str, Any] = {**plan, "applied": False, "runs": []}
    missing = list(plan.get("missingDates") or [])
    if not apply or not missing:
        return result
    if plan.get("activeRuns"):
        result["status"] = "deferred"
        result["reason"] = "active_pipeline_run"
        return result
    if plan.get("requiresConfirmation"):
        run_id = record_reconcile_blocked(
            selected,
            missing_dates=missing,
            requested_by="scheduler",
            reason="manual_confirmation_required",
        )
        result["status"] = "blocked"
        result["blockedRunId"] = run_id
        result["reason"] = "manual_confirmation_required"
        result["applied"] = True
        return result
    if not plan.get("canAutoCatchup"):
        result["status"] = "noop"
        result["reason"] = "no_auto_catchup_required"
        return result
    runs = []
    for day in missing:
        pipeline_result = run_daily_pipeline(day, paths=selected, trigger="scheduler-catchup")
        runs.append(
            {
                "date": pipeline_result.business_date,
                "success": pipeline_result.success,
                "failedStep": pipeline_result.failed_step,
                "steps": f"{pipeline_result.succeeded_steps}/{pipeline_result.total_steps}",
            }
        )
        if not pipeline_result.success:
            break
    result["status"] = "completed" if all(item.get("success") for item in runs) else "partial"
    result["applied"] = True
    result["runs"] = runs
    return result
