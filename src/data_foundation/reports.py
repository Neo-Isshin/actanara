"""Materialized period projections for Dashboard compatibility reads."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .db import connect
from .paths import RuntimePaths

LEGACY_ASSET_PROJECTION = "legacy-dashboard-assets-v1"


def _report_key(projection_type: str, start_date: date, end_date: date) -> str:
    return f"{projection_type}:{start_date.isoformat()}:{end_date.isoformat()}"


def _period_type(start_date: date, end_date: date) -> str:
    days = (end_date - start_date).days + 1
    if start_date.day == 1:
        return "month"
    if start_date.weekday() == 0 and 1 <= days <= 7:
        return "week"
    if days == 7:
        return "week"
    return "custom"


def write_period_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    metrics: dict,
    *,
    source_run_id: int | None,
    projection_type: str = LEGACY_ASSET_PROJECTION,
    status: str = "ready",
) -> str:
    period_type = _period_type(start_date, end_date)
    report_key = _report_key(projection_type, start_date, end_date)
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT INTO period_reports(
                report_key, period_type, start_date, end_date, projection_type,
                metrics_json, generated_at, source_run_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_key) DO UPDATE SET
                period_type=excluded.period_type,
                metrics_json=excluded.metrics_json,
                generated_at=excluded.generated_at,
                source_run_id=excluded.source_run_id,
                status=excluded.status
            """,
            (
                report_key,
                period_type,
                start_date.isoformat(),
                end_date.isoformat(),
                projection_type,
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                datetime.now().astimezone().isoformat(),
                source_run_id,
                status,
            ),
        )
    return report_key


def read_period_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    projection_type: str = LEGACY_ASSET_PROJECTION,
) -> dict | None:
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            """
            SELECT metrics_json, generated_at, status, source_run_id
            FROM period_reports
            WHERE report_key = ? AND status = 'ready'
            """,
            (_report_key(projection_type, start_date, end_date),),
        ).fetchone()
    if row is None:
        return None
    return {
        "metrics": json.loads(row["metrics_json"]),
        "generatedAt": row["generated_at"],
        "status": row["status"],
        "sourceRunId": row["source_run_id"],
        "projectionType": projection_type,
    }


def materialize_legacy_asset_projection(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    source_run_id: int,
    *,
    builder: Callable[[date, int], dict] | None = None,
) -> str:
    """Snapshot the current Dashboard period scanner output outside request handling."""
    if builder is None:
        dashboard_root = Path(__file__).resolve().parents[1] / "dashboard"
        if str(dashboard_root) not in sys.path:
            sys.path.insert(0, str(dashboard_root))
        from app.services import diary

        builder = diary._period_non_rag_asset_projection
    days = (end_date - start_date).days + 1
    metrics = builder(start_date, days)
    return write_period_projection(paths, start_date, end_date, metrics, source_run_id=source_run_id)
