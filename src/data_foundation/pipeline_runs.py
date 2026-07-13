"""Pipeline run ledger and scheduler reconciliation helpers."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from .db import connect, migrate
from .diary_paths import diary_report_paths
from .paths import RuntimePaths
from .settings import resolve_llm_provider
from .time import resolve_timezone

ACTIVE_STATUSES = {"queued", "running"}
SUCCESS_STATUSES = {"completed", "skipped"}
AUTO_CATCHUP_LIMIT_DAYS = 3
DEFAULT_LOOKBACK_DAYS = 7


def create_pipeline_run(
    paths: RuntimePaths,
    *,
    business_date: date | str,
    run_kind: str,
    requested_by: str,
    status: str = "running",
    source_trigger_id: int | None = None,
    retry_of_run_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    migrate(paths)
    provider = resolve_llm_provider(paths, redact_secrets=True)
    now = datetime.now().astimezone().isoformat()
    day = _date_key(business_date)
    with connect(paths) as connection:
        cursor = connection.execute(
            """
            INSERT INTO pipeline_runs(
                business_date, run_kind, requested_by, status, started_at,
                source_trigger_id, provider_id, model, steps_json,
                artifact_paths_json, retry_of_run_id, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?, ?, ?, ?)
            """,
            (
                day,
                str(run_kind or "daily"),
                str(requested_by or "scheduler"),
                status,
                now if status == "running" else None,
                source_trigger_id,
                provider.get("provider") or "",
                provider.get("model") or "",
                retry_of_run_id,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def append_pipeline_step(
    paths: RuntimePaths,
    run_id: int,
    *,
    name: str,
    status: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    migrate(paths)
    now = datetime.now().astimezone().isoformat()
    with connect(paths) as connection:
        row = connection.execute("SELECT steps_json FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return
        steps = _json_list(row["steps_json"])
        steps.append(
            {
                "name": name,
                "status": status,
                "reason": reason,
                "metadata": metadata or {},
                "updatedAt": now,
            }
        )
        connection.execute(
            "UPDATE pipeline_runs SET steps_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(steps, ensure_ascii=False, sort_keys=True), now, run_id),
        )


def finish_pipeline_run(
    paths: RuntimePaths,
    run_id: int,
    *,
    status: str,
    failure_class: str | None = None,
    error_summary: str | None = None,
    artifact_paths: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _finish_pipeline_run(
        paths,
        run_id,
        status=status,
        failure_class=failure_class,
        error_summary=error_summary,
        artifact_paths=artifact_paths,
        metadata=metadata,
        expected_statuses=None,
    )


def finish_pipeline_run_if_status(
    paths: RuntimePaths,
    run_id: int,
    *,
    expected_statuses: set[str],
    status: str,
    failure_class: str | None = None,
    error_summary: str | None = None,
    artifact_paths: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    return _finish_pipeline_run(
        paths,
        run_id,
        status=status,
        failure_class=failure_class,
        error_summary=error_summary,
        artifact_paths=artifact_paths,
        metadata=metadata,
        expected_statuses=set(expected_statuses),
    )


def _finish_pipeline_run(
    paths: RuntimePaths,
    run_id: int,
    *,
    status: str,
    failure_class: str | None,
    error_summary: str | None,
    artifact_paths: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    expected_statuses: set[str] | None,
) -> bool:
    migrate(paths)
    now = datetime.now().astimezone().isoformat()
    with connect(paths) as connection:
        current = connection.execute("SELECT status, metadata_json FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
        if current is None:
            return False
        if expected_statuses is not None and str(current["status"]) not in expected_statuses:
            return False
        merged_metadata = _json_dict(current["metadata_json"])
        if metadata:
            merged_metadata.update(metadata)
        params: list[Any] = [
            now,
            status,
            failure_class,
            error_summary,
            json.dumps(artifact_paths or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(merged_metadata, ensure_ascii=False, sort_keys=True),
            now,
            run_id,
        ]
        status_guard = ""
        if expected_statuses is not None:
            ordered_statuses = sorted(expected_statuses)
            status_guard = f" AND status IN ({','.join('?' for _ in ordered_statuses)})"
            params.extend(ordered_statuses)
        cursor = connection.execute(
            """
            UPDATE pipeline_runs
            SET completed_at = ?, status = ?, failure_class = ?, error_summary = ?,
                artifact_paths_json = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """
            + status_guard,
            params,
        )
        return cursor.rowcount == 1


def pipeline_run_by_id(paths: RuntimePaths, run_id: int) -> dict | None:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        row = connection.execute("SELECT * FROM pipeline_runs WHERE id = ?", (int(run_id),)).fetchone()
    return _row_dict(row) if row is not None else None


def latest_pipeline_run_for_date(paths: RuntimePaths, business_date: date | str) -> dict | None:
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT * FROM pipeline_runs
            WHERE business_date = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (_date_key(business_date),),
        ).fetchone()
    return _row_dict(row) if row is not None else None


def list_pipeline_runs(
    paths: RuntimePaths,
    *,
    statuses: set[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    migrate(paths)
    params: list[Any] = []
    where = ""
    if statuses:
        where = f"WHERE status IN ({','.join('?' for _ in statuses)})"
        params.extend(sorted(statuses))
    params.append(max(1, min(int(limit or 50), 500)))
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            f"SELECT * FROM pipeline_runs {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_dict(row) for row in rows]


def pipeline_run_success_for_date(paths: RuntimePaths, business_date: date | str) -> bool:
    run = latest_pipeline_run_for_date(paths, business_date)
    return bool(run and run.get("status") in SUCCESS_STATUSES)


def pipeline_reconcile_plan(
    paths: RuntimePaths,
    *,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    auto_limit_days: int = AUTO_CATCHUP_LIMIT_DAYS,
) -> dict:
    tz = resolve_timezone(paths)
    current = now.astimezone(tz) if now else datetime.now(tz)
    target_end = current.date() - timedelta(days=1)
    lookback = max(1, int(lookback_days or DEFAULT_LOOKBACK_DAYS))
    target_start = target_end - timedelta(days=lookback - 1)
    days = [target_start + timedelta(days=offset) for offset in range((target_end - target_start).days + 1)]
    missing = [day for day in days if not pipeline_run_success_for_date(paths, day) and not _diary_document_exists(paths, day)]
    active = list_pipeline_runs(paths, statuses=ACTIVE_STATUSES, limit=20)
    can_auto = 0 < len(missing) <= max(0, int(auto_limit_days or AUTO_CATCHUP_LIMIT_DAYS)) and not active
    requires_confirmation = len(missing) > max(0, int(auto_limit_days or AUTO_CATCHUP_LIMIT_DAYS))
    return {
        "status": "ok",
        "targetStart": target_start.isoformat(),
        "targetEnd": target_end.isoformat(),
        "lookbackDays": lookback,
        "autoLimitDays": int(auto_limit_days or AUTO_CATCHUP_LIMIT_DAYS),
        "missingDates": [day.isoformat() for day in missing],
        "missingCount": len(missing),
        "activeRuns": active,
        "canAutoCatchup": can_auto,
        "requiresConfirmation": requires_confirmation,
    }


def record_reconcile_blocked(
    paths: RuntimePaths,
    *,
    missing_dates: list[str],
    requested_by: str = "scheduler",
    reason: str = "manual_confirmation_required",
) -> int | None:
    if not missing_dates:
        return None
    latest = missing_dates[-1]
    existing = latest_pipeline_run_for_date(paths, latest)
    if existing and existing.get("status") == "blocked" and existing.get("failureClass") == reason:
        return int(existing["id"])
    run_id = create_pipeline_run(
        paths,
        business_date=latest,
        run_kind="catchup_reconcile",
        requested_by=requested_by,
        status="blocked",
        metadata={"missingDates": missing_dates},
    )
    finish_pipeline_run(
        paths,
        run_id,
        status="blocked",
        failure_class=reason,
        error_summary=f"{len(missing_dates)} missing pipeline day(s) require confirmation before catch-up.",
        metadata={"missingDates": missing_dates},
    )
    return run_id


def classify_pipeline_failure(reason: str | None) -> str:
    text = str(reason or "").lower()
    if "cancel" in text:
        return "cancelled"
    if "usage limit" in text or "quota" in text or "http 403" in text:
        return "llm_quota"
    if "auth" in text or "401" in text:
        return "auth"
    if "timeout" in text:
        return "timeout"
    if "network" in text or "ssl" in text or "urlerror" in text:
        return "network"
    if "blank" in text:
        return "blank_day_policy"
    if "missing" in text or "not found" in text:
        return "data_missing"
    return "internal_error"


def _date_key(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value)).isoformat()


def _diary_document_exists(paths: RuntimePaths, business_date: date) -> bool:
    return bool(diary_report_paths(paths.diary_dir, business_date.isoformat(), "narrative"))


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _json_dict(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _row_dict(row) -> dict:
    result = dict(row)
    result["steps"] = _json_list(result.pop("steps_json", "[]"))
    result["artifactPaths"] = _json_dict(result.pop("artifact_paths_json", "{}"))
    result["metadata"] = _json_dict(result.pop("metadata_json", "{}"))
    result["businessDate"] = result.get("business_date")
    result["runKind"] = result.get("run_kind")
    result["requestedBy"] = result.get("requested_by")
    result["sourceTriggerId"] = result.get("source_trigger_id")
    result["providerId"] = result.get("provider_id")
    result["failureClass"] = result.get("failure_class")
    result["errorSummary"] = result.get("error_summary")
    result["retryOfRunId"] = result.get("retry_of_run_id")
    return result
