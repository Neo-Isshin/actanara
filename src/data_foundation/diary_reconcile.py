"""Controlled rebuild helpers for diary Markdown SQLite projections."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .db import connect, migrate
from .diary_markdown import (
    DIARY_MARKDOWN_PROJECTION,
    DIARY_PERIOD_PAGE_PROJECTION,
    _authoritative_diary_markdown_paths,
    _pipeline_language_profile,
    materialize_diary_markdown_period_documents,
    materialize_diary_period_page_snapshot,
)
from .ingest import run_shadow_period_ingestion
from .jobs import begin_ingestion_run, finish_ingestion_run, list_ingestion_runs
from .paths import RuntimePaths
from .period_summary import DIARY_PERIOD_SUMMARY_PROJECTION, materialize_period_summary_snapshot

DIARY_PROJECTION_REBUILD_TRIGGER = "dashboard-diary-projection-rebuild"
DIARY_TOKEN_HOURLY_REPAIR_TRIGGER = "dashboard-diary-token-hourly-repair"


def _days(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _diary_root(paths: RuntimePaths, diary_root: Path | None = None) -> Path | None:
    return diary_root or paths.diary_dir


def _disk_relative_paths(paths: RuntimePaths, start_date: date, end_date: date, diary_root: Path | None = None) -> list[str]:
    root = _diary_root(paths, diary_root)
    if root is None or not root.exists():
        return []
    relatives: list[str] = []
    language_profile = _pipeline_language_profile(paths)
    for day in _days(start_date, end_date):
        for markdown_path in _authoritative_diary_markdown_paths(root, day, language_profile=language_profile):
            relatives.append(markdown_path.relative_to(root).as_posix())
    return relatives


def _db_documents(paths: RuntimePaths, start_date: date, end_date: date) -> list[dict]:
    if not paths.db_path.exists():
        return []
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT document_key, business_date, report_type, relative_path, status
            FROM diary_markdown_documents
            WHERE business_date >= ? AND business_date <= ?
            ORDER BY business_date, report_type, relative_path
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    return [dict(row) for row in rows]


def _usage_coverage(paths: RuntimePaths, start_date: date, end_date: date) -> dict:
    expected_dates = [day.isoformat() for day in _days(start_date, end_date)]
    if not paths.db_path.exists():
        return {
            "events": 0,
            "coveredDays": 0,
            "dateRange": None,
            "missingDays": expected_dates[:50],
            "truncated": len(expected_dates) > 50,
        }
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT business_date, COUNT(*) AS events
                FROM usage_events
                WHERE business_date >= ? AND business_date <= ?
                GROUP BY business_date
                ORDER BY business_date
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
    except Exception:
        rows = []
    by_date = {row["business_date"]: int(row["events"] or 0) for row in rows}
    covered = [day for day in expected_dates if by_date.get(day, 0) > 0]
    missing = [day for day in expected_dates if by_date.get(day, 0) <= 0]
    return {
        "events": sum(by_date.values()),
        "coveredDays": len(covered),
        "dateRange": {"startDate": covered[0], "endDate": covered[-1]} if covered else None,
        "missingDays": missing[:50],
        "truncated": len(missing) > 50,
    }


def plan_diary_projection_rebuild(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    diary_root: Path | None = None,
) -> dict:
    """Return the exact rows/files a rebuild would reconcile without writing."""
    if end_date < start_date:
        raise ValueError("endDate must be on or after startDate")
    disk_relative = set(_disk_relative_paths(paths, start_date, end_date, diary_root=diary_root))
    db_rows = _db_documents(paths, start_date, end_date)
    db_relative = {row["relative_path"] for row in db_rows if row.get("status") == "ready"}
    usage = _usage_coverage(paths, start_date, end_date)
    return {
        "dryRun": True,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "diaryRoot": str(_diary_root(paths, diary_root) or ""),
        "database": str(paths.db_path),
        "projectionTypes": [
            DIARY_MARKDOWN_PROJECTION,
            DIARY_PERIOD_PAGE_PROJECTION,
            DIARY_PERIOD_SUMMARY_PROJECTION,
        ],
        "diskMarkdownFiles": len(disk_relative),
        "databaseRows": len(db_rows),
        "readyRows": len(db_relative),
        "matchedRows": len(disk_relative & db_relative),
        "missingDiskFiles": sorted(db_relative - disk_relative)[:50],
        "missingDatabaseRows": sorted(disk_relative - db_relative)[:50],
        "truncated": len(db_relative - disk_relative) > 50 or len(disk_relative - db_relative) > 50,
        "wouldDeleteRows": 0,
        "wouldUpsertDocuments": len(disk_relative),
        "wouldRebuildPeriodReports": [
            f"{DIARY_PERIOD_PAGE_PROJECTION}:{start_date.isoformat()}:{end_date.isoformat()}",
            f"{DIARY_PERIOD_SUMMARY_PROJECTION}:{start_date.isoformat()}:{end_date.isoformat()}",
        ],
        "usageCoverage": usage,
        "wouldRepairUsageEvents": bool(usage["missingDays"]),
        "usageRepairTrigger": DIARY_TOKEN_HOURLY_REPAIR_TRIGGER,
    }


def rebuild_diary_projections(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    diary_root: Path | None = None,
    include_usage: bool = True,
) -> dict:
    """Rebuild diary Markdown documents and exact-range period projections."""
    plan = plan_diary_projection_rebuild(paths, start_date, end_date, diary_root=diary_root)
    migrate(paths)
    run_id = begin_ingestion_run(
        paths,
        trigger_type=DIARY_PROJECTION_REBUILD_TRIGGER,
        business_date=end_date,
        adapter_versions={
            "projection": "diary-reconcile-v1",
            "scope": "diary-markdown-and-period",
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "diaryRoot": str(_diary_root(paths, diary_root) or ""),
        },
    )
    try:
        markdown = materialize_diary_markdown_period_documents(
            paths,
            start_date,
            end_date,
            source_run_id=run_id,
            diary_root=diary_root,
        )
        page_key = materialize_diary_period_page_snapshot(paths, start_date, end_date, source_run_id=run_id)
        summary_key = materialize_period_summary_snapshot(paths, start_date, end_date, source_run_id=run_id)
        usage_repair = None
        if include_usage:
            usage_result = run_shadow_period_ingestion(
                paths,
                start_date,
                end_date,
                trigger=DIARY_TOKEN_HOURLY_REPAIR_TRIGGER,
                observe_assets=False,
            )
            usage_repair = {
                "runId": usage_result.run_id,
                "artifactsSeen": usage_result.artifacts_seen,
                "eventsSeen": usage_result.events_seen,
                "eventsInWindow": usage_result.events_in_window,
                "errors": usage_result.errors,
                "coverage": _usage_coverage(paths, start_date, end_date),
            }
        finish_ingestion_run(paths, run_id, status="completed")
        return {
            **plan,
            "dryRun": False,
            "runId": run_id,
            "deletedRows": 0,
            "documents": markdown["documents"],
            "documentKeys": markdown["documentKeys"],
            "pageProjection": page_key,
            "summaryProjection": summary_key,
            "usageRepair": usage_repair,
            "status": "completed",
        }
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise


def recent_diary_projection_rebuild_jobs(paths: RuntimePaths, *, limit: int = 20) -> list[dict]:
    migrate(paths)
    return list_ingestion_runs(paths, trigger_types=(DIARY_PROJECTION_REBUILD_TRIGGER,), limit=limit)
