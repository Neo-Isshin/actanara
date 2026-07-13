"""Dangerous operator-controlled rebuild for the SQLite read-model cache."""

from __future__ import annotations

import shutil
import re
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from .adapters.usage import UsageAdapter
from .db import migrate
from .diary_paths import iter_diary_markdown_files
from .diary_markdown import materialize_diary_markdown_period_documents, materialize_diary_period_page_snapshot
from .ingest import run_shadow_period_ingestion
from .jobs import begin_ingestion_run, finish_ingestion_run
from .paths import RuntimePaths
from .period_summary import materialize_period_summary_snapshot
from .reports import materialize_legacy_asset_projection
from .snapshots import materialize_ai_assets_non_rag_snapshot
from .time import business_today

SQLITE_CACHE_REBUILD_CONFIRMATION = "REBUILD OPEN NOVA SQLITE CACHE"
SQLITE_CACHE_REBUILD_TRIGGER = "operator-sqlite-cache-rebuild"


def _diary_dates(paths: RuntimePaths) -> list[date]:
    root = paths.diary_dir
    if not root.exists():
        return []
    dates: set[date] = set()
    for markdown_path in iter_diary_markdown_files(root):
        match = None
        parts = markdown_path.relative_to(root).parts
        if len(parts) >= 3 and re.match(r"diary-\d{4}$", parts[0]) and re.match(r"diary-\d{4}-\d{2}$", parts[1]) and re.match(r"\d{2}-\d{2}$", parts[2]):
            year = parts[0].removeprefix("diary-")
            month = parts[1].rsplit("-", 1)[-1]
            day = parts[2].split("-", 1)[-1]
            match = f"{year}-{month}-{day}"
        for part in parts:
            if match is None and re.match(r"diary-\d{4}-\d{2}-\d{2}$", part):
                match = part.removeprefix("diary-")
                break
        if match is None:
            if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
                match = f"{parts[0]}-{parts[1]}-{parts[2]}"
        if not match:
            continue
        try:
            parsed = date.fromisoformat(match)
        except ValueError:
            continue
        dates.add(parsed)
    return sorted(dates)


def _selected_range(paths: RuntimePaths, start_date: date | None, end_date: date | None) -> tuple[date | None, date | None]:
    dates = _diary_dates(paths)
    if not dates:
        return start_date, end_date
    return start_date or dates[0], end_date or max(dates[-1], business_today(paths))


def _backup_dir(paths: RuntimePaths, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return paths.state_dir / "backups" / "sqlite-rebuild" / stamp


def plan_sqlite_cache_rebuild(
    paths: RuntimePaths,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    selected_start, selected_end = _selected_range(paths, start_date, end_date)
    diary_root = paths.diary_dir
    dates = _diary_dates(paths)
    return {
        "dryRun": True,
        "dangerous": True,
        "confirmationTextRequired": SQLITE_CACHE_REBUILD_CONFIRMATION,
        "runtime": str(paths.home),
        "database": str(paths.db_path),
        "databaseExists": paths.db_path.exists(),
        "backupRoot": str(paths.state_dir / "backups" / "sqlite-rebuild"),
        "diaryRoot": str(diary_root),
        "diaryDates": len(dates),
        "diaryDateRange": {
            "startDate": dates[0].isoformat(),
            "endDate": dates[-1].isoformat(),
        }
        if dates
        else None,
        "rebuildRange": {
            "startDate": selected_start.isoformat() if selected_start else None,
            "endDate": selected_end.isoformat() if selected_end else None,
        },
        "willBackupExistingDatabase": paths.db_path.exists(),
        "willReplaceDatabase": True,
        "willRecreateSchema": True,
        "failureRecovery": "restore-backup-before-raising",
        "willRebuild": [
            "usage_events",
            "daily_tool_usage",
            "daily_model_usage",
            "daily_project_usage",
            "diary_markdown_documents",
            "diary_markdown_sections",
            "dashboard_snapshots.ai-assets:latest:non-rag",
            "period_reports",
        ],
        "warning": "This replaces the SQLite read-model cache. Historical rows that cannot be regenerated from current sources will not be present in the rebuilt database.",
    }


def _backup_and_remove_database(paths: RuntimePaths) -> dict:
    backup = _backup_dir(paths)
    backup.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    removed: list[str] = []
    for path in (paths.db_path, paths.db_path.with_name(paths.db_path.name + "-wal"), paths.db_path.with_name(paths.db_path.name + "-shm")):
        if not path.exists():
            continue
        target = backup / path.name
        shutil.copy2(path, target)
        copied.append(str(target))
        path.unlink()
        removed.append(str(path))
    return {"backupDir": str(backup), "copied": copied, "removed": removed}


def _restore_database_backup(paths: RuntimePaths, backup: dict) -> dict:
    backup_dir = Path(str(backup.get("backupDir") or ""))
    targets = (
        paths.db_path,
        paths.db_path.with_name(paths.db_path.name + "-wal"),
        paths.db_path.with_name(paths.db_path.name + "-shm"),
    )
    removed_new: list[str] = []
    restored: list[str] = []
    for target in targets:
        if target.exists():
            target.unlink()
            removed_new.append(str(target))
        source = backup_dir / target.name
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(str(target))
    return {"backupDir": str(backup_dir), "removedNew": removed_new, "restored": restored}


def rebuild_sqlite_cache(
    paths: RuntimePaths,
    *,
    confirmation_text: str,
    start_date: date | None = None,
    end_date: date | None = None,
    adapters: Iterable[UsageAdapter] | None = None,
    ai_assets_builder: Callable[[], dict] | None = None,
) -> dict:
    if confirmation_text != SQLITE_CACHE_REBUILD_CONFIRMATION:
        raise ValueError(f"confirmation text must be exactly: {SQLITE_CACHE_REBUILD_CONFIRMATION}")
    selected_start, selected_end = _selected_range(paths, start_date, end_date)
    if selected_start is None or selected_end is None:
        raise ValueError("no diary date range found; provide startDate and endDate")
    if selected_end < selected_start:
        raise ValueError("endDate must be on or after startDate")

    plan = plan_sqlite_cache_rebuild(paths, start_date=selected_start, end_date=selected_end)
    backup = _backup_and_remove_database(paths)
    try:
        migrate(paths)
        usage = run_shadow_period_ingestion(
            paths,
            selected_start,
            selected_end,
            adapters=adapters,
            trigger="operator-sqlite-cache-rebuild-usage",
            observe_assets=True,
        )
        run_id = begin_ingestion_run(
            paths,
            trigger_type=SQLITE_CACHE_REBUILD_TRIGGER,
            business_date=selected_end,
            adapter_versions={
                "scope": "sqlite-cache",
                "startDate": selected_start.isoformat(),
                "endDate": selected_end.isoformat(),
                "diaryRoot": str(paths.diary_dir),
            },
        )
    except Exception:
        _restore_database_backup(paths, backup)
        raise
    try:
        markdown = materialize_diary_markdown_period_documents(paths, selected_start, selected_end, source_run_id=run_id)
        page_key = materialize_diary_period_page_snapshot(paths, selected_start, selected_end, source_run_id=run_id)
        summary_key = materialize_period_summary_snapshot(paths, selected_start, selected_end, source_run_id=run_id)
        asset_key = materialize_ai_assets_non_rag_snapshot(paths, run_id, builder=ai_assets_builder)
        period_key = materialize_legacy_asset_projection(paths, selected_start, selected_end, run_id)
        finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        try:
            finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        except Exception:
            pass
        _restore_database_backup(paths, backup)
        raise
    return {
        **plan,
        "dryRun": False,
        "status": "completed",
        "runId": run_id,
        "backup": backup,
        "usageIngestion": {
            "runId": usage.run_id,
            "artifactsSeen": usage.artifacts_seen,
            "eventsSeen": usage.events_seen,
            "eventsInWindow": usage.events_in_window,
            "errors": usage.errors,
        },
        "diaryMarkdown": markdown,
        "pageProjection": page_key,
        "summaryProjection": summary_key,
        "aiAssetsSnapshot": asset_key,
        "periodAssetProjection": period_key,
    }
