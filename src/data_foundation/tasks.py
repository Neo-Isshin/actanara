"""Read-only shadow import and comparison for the legacy task SQLite store."""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .db import connect, migrate
from .diary_paths import diary_report_prefix
from .jobs import begin_ingestion_run, finish_ingestion_run
from .paths import RuntimePaths

SOURCE_TABLES = ("projects", "tasks", "task_updates")


@dataclass(frozen=True)
class TaskShadowImportResult:
    run_id: int
    source_db_path: Path
    project_count: int
    task_count: int
    update_count: int


@dataclass(frozen=True)
class TaskReportShadowResult:
    run_id: int
    source_file_count: int
    source_with_updates_count: int
    event_count: int


@dataclass(frozen=True)
class TaskBoardProjectionResult:
    snapshot_key: str
    run_id: int
    board_path: Path
    project_count: int
    item_count: int
    completed_count: int
    in_progress_count: int


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _field_value(raw: str) -> str:
    return raw.strip().strip("\"'")


def _parse_report_updates(content: str) -> tuple[str | None, list[dict]]:
    report_date_match = re.search(r'(?m)^date:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$', content)
    report_date = report_date_match.group(1) if report_date_match else None
    lines = content.splitlines()
    updates: list[dict] = []
    for index, line in enumerate(lines):
        if not re.match(r"^task_updates:\s*$", line):
            continue
        current: dict | None = None
        for nested in lines[index + 1 :]:
            if nested and not nested[0].isspace():
                break
            item = re.match(r'^\s+-\s+id:\s*(.+?)\s*$', nested)
            if item:
                if current is not None:
                    updates.append(current)
                current = {"id": _field_value(item.group(1))}
                continue
            field = re.match(r"^\s+(parent_id|title|status|progress_delta):\s*(.*?)\s*$", nested)
            if current is not None and field:
                current[field.group(1)] = _field_value(field.group(2))
        if current is not None:
            updates.append(current)
    normalized = [
        {
            "task_id": str(update["id"]),
            "project_id": str(update.get("parent_id", "")) or None,
            "title": str(update.get("title", "")) or None,
            "status": str(update.get("status", "")) or None,
            "progress_delta": int(update.get("progress_delta", 0) or 0),
        }
        for update in updates
        if update.get("id")
    ]
    return report_date, normalized


def _normalize_board_section(raw: str) -> str:
    value = raw.strip()
    for emoji in ("🟡", "📋", "🔵", "✅", "⚫", "⬜"):
        if value.startswith(emoji):
            value = value[len(emoji) :].strip()
    return re.sub(r"\s*\(.*?\)$", "", value).strip()


def parse_task_board_markdown(content: str) -> dict:
    """Parse TASK_BOARD.md into a read-only projection model."""
    projects: list[dict] = []
    items: list[dict] = []
    current_section = "未知"
    current_project: dict | None = None
    task_re = re.compile(r"^-\s*\[([^\]]*)\]\s*(.+?)(?:\s*←\s*\*\*@(.+?)\*\*)?$")
    table_row_re = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^\|]+?)\s*\|\s*([^\|]*?)\s*\|$")
    skip_re = re.compile(r"^(>|\s*$|---|\|[\s\-:]*\|)")

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.rstrip()
        if skip_re.match(line):
            continue
        section_match = re.match(r"^##\s+(.+)$", line)
        if section_match:
            current_section = _normalize_board_section(section_match.group(1))
            current_project = None
            continue
        project_match = re.match(r"^###\s+(.+)$", line)
        if project_match:
            current_project = {
                "projectOrdinal": len(projects),
                "section": current_section,
                "project": project_match.group(1).strip(),
            }
            projects.append(current_project)
            continue
        if current_project is None:
            continue
        task_match = task_re.match(line)
        if task_match:
            checked = task_match.group(1).strip().lower()
            content_text = task_match.group(2).strip()
            identified = re.search(r"\[(T-[A-Za-z0-9-]+)\]", content_text)
            item_key_source = f"{current_project['projectOrdinal']}\0{len(items)}\0{line_number}\0{line}"
            items.append(
                {
                    "itemKey": hashlib.sha256(item_key_source.encode("utf-8")).hexdigest(),
                    "projectOrdinal": current_project["projectOrdinal"],
                    "itemOrdinal": sum(1 for item in items if item["projectOrdinal"] == current_project["projectOrdinal"]),
                    "section": current_project["section"],
                    "project": current_project["project"],
                    "done": checked == "x",
                    "content": content_text,
                    "agent": task_match.group(3) or "",
                    "identifiedTaskId": identified.group(1) if identified is not None else None,
                    "sourceLine": line_number,
                    "rawLine": line,
                }
            )
            continue
        table_match = table_row_re.match(line)
        if table_match:
            content_text = f"[{table_match.group(1).strip()}] {table_match.group(2).strip()}"
            item_key_source = f"{current_project['projectOrdinal']}\0{len(items)}\0{line_number}\0{line}"
            items.append(
                {
                    "itemKey": hashlib.sha256(item_key_source.encode("utf-8")).hexdigest(),
                    "projectOrdinal": current_project["projectOrdinal"],
                    "itemOrdinal": sum(1 for item in items if item["projectOrdinal"] == current_project["projectOrdinal"]),
                    "section": current_project["section"],
                    "project": current_project["project"],
                    "done": True,
                    "content": content_text,
                    "agent": table_match.group(3).strip(),
                    "identifiedTaskId": None,
                    "sourceLine": line_number,
                    "rawLine": line,
                }
            )
    return {
        "projects": projects,
        "items": items,
        "counts": {
            "projects": len(projects),
            "items": len(items),
            "Completed": sum(1 for item in items if item["done"]),
            "InProgress": sum(1 for item in items if not item["done"]),
        },
    }


def _read_legacy_rows(source_db_path: Path) -> dict[str, list[dict]]:
    if not source_db_path.exists():
        raise FileNotFoundError(f"legacy task database does not exist: {source_db_path}")
    connection = sqlite3.connect(f"{source_db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('projects', 'tasks', 'task_updates')"
            )
        }
        missing = sorted(set(SOURCE_TABLES) - tables)
        if missing:
            raise ValueError(f"legacy task database is missing tables: {', '.join(missing)}")
        return {
            "projects": [
                dict(row)
                for row in connection.execute("SELECT id, name, last_updated FROM projects ORDER BY id")
            ],
            "tasks": [
                dict(row)
                for row in connection.execute(
                    "SELECT id, project_id, title, status, progress, last_updated FROM tasks ORDER BY id"
                )
            ],
            "task_updates": [
                dict(row)
                for row in connection.execute(
                    "SELECT id, task_id, report_date, progress_delta, status, report_file FROM task_updates ORDER BY id"
                )
            ],
        }
    finally:
        connection.close()


def import_legacy_task_db(
    paths: RuntimePaths,
    source_db_path: Path,
    *,
    business_date: date | None = None,
) -> TaskShadowImportResult:
    """Replace the Foundation shadow copy from a read-only legacy task DB snapshot."""
    migrate(paths)
    run_id = begin_ingestion_run(
        paths,
        trigger_type="task-shadow-import",
        business_date=business_date,
        adapter_versions={"legacy-task-db": "shadow-v1"},
    )
    try:
        source = _read_legacy_rows(source_db_path)
        with connect(paths) as connection:
            connection.execute("DELETE FROM legacy_task_updates")
            connection.execute("DELETE FROM legacy_tasks")
            connection.execute("DELETE FROM legacy_task_projects")
            connection.executemany(
                """
                INSERT INTO legacy_task_projects(source_project_id, name, source_last_updated, source_run_id)
                VALUES (?, ?, ?, ?)
                """,
                [(row["id"], row["name"], row["last_updated"], run_id) for row in source["projects"]],
            )
            connection.executemany(
                """
                INSERT INTO legacy_tasks(
                    source_task_id, source_project_id, title, status, progress, source_last_updated, source_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["id"],
                        row["project_id"],
                        row["title"],
                        row["status"],
                        int(row["progress"] or 0),
                        row["last_updated"],
                        run_id,
                    )
                    for row in source["tasks"]
                ],
            )
            connection.executemany(
                """
                INSERT INTO legacy_task_updates(
                    source_row_id, source_task_id, report_date, progress_delta, status, report_file, source_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["id"],
                        row["task_id"],
                        row["report_date"],
                        row["progress_delta"],
                        row["status"],
                        row["report_file"],
                        run_id,
                    )
                    for row in source["task_updates"]
                ],
            )
            connection.execute(
                """
                INSERT INTO task_shadow_imports(
                    run_id, source_db_path, source_project_count, source_task_count, source_update_count,
                    imported_at, notes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(source_db_path.resolve()),
                    len(source["projects"]),
                    len(source["tasks"]),
                    len(source["task_updates"]),
                    datetime.now().astimezone().isoformat(),
                    json.dumps(
                        {
                            "mode": "read-only-source-snapshot",
                            "authority": "Nova-Task v2 SQLite authority; legacy task imports are historical projections",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise
    return TaskShadowImportResult(
        run_id=run_id,
        source_db_path=source_db_path.resolve(),
        project_count=len(source["projects"]),
        task_count=len(source["tasks"]),
        update_count=len(source["task_updates"]),
    )


def _shadow_rows(paths: RuntimePaths) -> dict[str, list[dict]]:
    with connect(paths, read_only=True) as connection:
        return {
            "projects": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT source_project_id AS id, name, source_last_updated AS last_updated
                    FROM legacy_task_projects ORDER BY source_project_id
                    """
                )
            ],
            "tasks": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT source_task_id AS id, source_project_id AS project_id, title, status, progress,
                           source_last_updated AS last_updated
                    FROM legacy_tasks ORDER BY source_task_id
                    """
                )
            ],
            "task_updates": [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT source_row_id AS id, source_task_id AS task_id, report_date, progress_delta, status, report_file
                    FROM legacy_task_updates ORDER BY source_row_id
                    """
                )
            ],
        }


def task_shadow_comparison_report(paths: RuntimePaths, source_db_path: Path) -> dict:
    legacy = _read_legacy_rows(source_db_path)
    foundation = _shadow_rows(paths)
    differences = {
        table: {"legacy": legacy[table], "foundation": foundation[table]}
        for table in SOURCE_TABLES
        if legacy[table] != foundation[table]
    }
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "sourceDbPath": str(source_db_path.resolve()),
        "counts": {
            table: {"legacy": len(legacy[table]), "foundation": len(foundation[table])}
            for table in SOURCE_TABLES
        },
        "differences": differences,
        "matched": not differences,
        "preservedSources": {
            "taskBoardWriter": "historical-projection",
            "diaryTasks": "nova-task-v2-sqlite-authority",
            "dashboardTasks": "nova-task-v2-sqlite-authority",
        },
        "notes": [
            "The source task database is read-only during this shadow import.",
            "TASK_BOARD.md is retained as a historical compatibility projection, not current task authority.",
            "Legacy technical-report ingestion remains a historical observation source.",
        ],
    }


def write_task_shadow_comparison_report(paths: RuntimePaths, source_db_path: Path) -> dict:
    report = task_shadow_comparison_report(paths, source_db_path)
    output = paths.state_dir / "migration" / "task-shadow-comparison.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["outputPath"] = str(output)
    return report


def materialize_task_report_events(
    paths: RuntimePaths,
    reports_root: Path,
    *,
    business_date: date | None = None,
    language_profile: str = "zh",
) -> TaskReportShadowResult:
    """Materialize versioned task-update events from legacy reports without editing source files."""
    migrate(paths)
    root = reports_root.resolve()
    technical_prefix = diary_report_prefix("technical", language_profile)
    report_paths = sorted(root.rglob(f"{technical_prefix}-*.md")) if root.exists() else []
    sources: list[dict] = []
    events: list[dict] = []
    for report_path in report_paths:
        content = report_path.read_text(encoding="utf-8")
        fingerprint = _content_sha256(content)
        relative_path = report_path.relative_to(root).as_posix()
        report_date, updates = _parse_report_updates(content)
        sources.append(
            {
                "source_path": relative_path,
                "content_sha256": fingerprint,
                "report_date": report_date,
                "update_count": len(updates),
            }
        )
        for ordinal, update in enumerate(updates, start=1):
            identity = f"{relative_path}\0{fingerprint}\0{ordinal}"
            events.append(
                {
                    "event_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    "source_path": relative_path,
                    "source_content_sha256": fingerprint,
                    "event_ordinal": ordinal,
                    "report_date": report_date,
                    **update,
                }
            )
    run_id = begin_ingestion_run(
        paths,
        trigger_type="task-report-shadow-import",
        business_date=business_date,
        adapter_versions={"technical-report-task-updates": "shadow-v1"},
    )
    try:
        with connect(paths) as connection:
            connection.execute("DELETE FROM task_report_update_events")
            connection.execute("DELETE FROM task_report_sources")
            connection.executemany(
                """
                INSERT INTO task_report_sources(
                    source_path, content_sha256, report_date, update_count, source_run_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["source_path"],
                        row["content_sha256"],
                        row["report_date"],
                        row["update_count"],
                        run_id,
                    )
                    for row in sources
                ],
            )
            connection.executemany(
                """
                INSERT INTO task_report_update_events(
                    event_key, source_path, source_content_sha256, event_ordinal, report_date,
                    source_task_id, source_project_id, title, status, progress_delta, source_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["event_key"],
                        row["source_path"],
                        row["source_content_sha256"],
                        row["event_ordinal"],
                        row["report_date"],
                        row["task_id"],
                        row["project_id"],
                        row["title"],
                        row["status"],
                        row["progress_delta"],
                        run_id,
                    )
                    for row in events
                ],
            )
            connection.execute(
                """
                INSERT INTO task_report_shadow_imports(
                    run_id, reports_root, source_file_count, source_with_updates_count, event_count,
                    imported_at, notes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(root),
                    len(sources),
                    sum(1 for row in sources if row["update_count"]),
                    len(events),
                    datetime.now().astimezone().isoformat(),
                    json.dumps(
                        {
                            "eventIdentity": "relative source path + source content sha256 + update ordinal",
                            "authority": "read-only event observation; not a task state writer",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
        finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise
    return TaskReportShadowResult(
        run_id=run_id,
        source_file_count=len(sources),
        source_with_updates_count=sum(1 for row in sources if row["update_count"]),
        event_count=len(events),
    )


def task_board_observation_report(paths: RuntimePaths, board_path: Path, source_run_id: int) -> dict:
    """Compare the existing board checkbox contract with observed report event task identifiers."""
    content = board_path.read_text(encoding="utf-8")
    checkbox_rows = [line for line in content.splitlines() if re.match(r"^-\s*\[[ xX]\]\s+", line)]
    identified_ids = sorted(
        {
            match.group(1)
            for line in checkbox_rows
            for match in [re.search(r"\[(T-[A-Za-z0-9-]+)\]", line)]
            if match is not None
        }
    )
    with connect(paths, read_only=True) as connection:
        event_task_ids = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_task_id FROM task_report_update_events ORDER BY source_task_id"
            )
        ]
        event_count = connection.execute("SELECT COUNT(*) FROM task_report_update_events").fetchone()[0]
    event_id_set = set(event_task_ids)
    identified_id_set = set(identified_ids)
    snapshot = {
        "InProgress": len(re.findall(r"\[\s*\]", content)),
        "Completed": len(re.findall(r"\[x\]", content)),
    }
    corrected_snapshot = authoritative_board_diary_snapshot(board_path)
    report = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "status": "board_authority_confirmed_events_non_authoritative",
        "boardPath": str(board_path.resolve()),
        "diaryTaskSnapshot": snapshot,
        "authoritativeBoardDiarySnapshot": corrected_snapshot,
        "checkboxRows": len(checkbox_rows),
        "identifiedCheckboxTaskIds": identified_ids,
        "reportEvents": {"eventCount": event_count, "distinctTaskIds": event_task_ids},
        "overlapTaskIds": sorted(identified_id_set & event_id_set),
        "boardIdsMissingFromReports": sorted(identified_id_set - event_id_set),
        "reportIdsAbsentFromIdentifiedCheckboxes": sorted(event_id_set - identified_id_set),
        "canEnable": {"reportEventsAsCurrentTaskState": False},
        "preservedSources": {
            "taskBoardWriter": "historical-projection",
            "diaryTasks": "nova-task-v2-sqlite-authority",
            "dashboardTasks": "nova-task-v2-sqlite-authority",
        },
        "notes": [
            "TASK_BOARD.md checkbox counts are retained as historical projection data.",
            "Technical-report events are historical observations and are not a replacement board state.",
            "Nova-Task v2 SQLite is the current task authority for dashboard and report readers.",
        ],
    }
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO task_board_observations(
                run_id, board_path, content_sha256, in_progress_count, completed_count,
                identified_checkbox_count, compared_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_run_id,
                str(board_path.resolve()),
                _content_sha256(content),
                snapshot["InProgress"],
                snapshot["Completed"],
                len(identified_ids),
                report["generatedAt"],
                json.dumps(report, ensure_ascii=False, sort_keys=True),
            ),
        )
    return report


def write_task_board_observation_report(paths: RuntimePaths, board_path: Path, source_run_id: int) -> dict:
    report = task_board_observation_report(paths, board_path, source_run_id)
    output = paths.state_dir / "migration" / "task-board-observation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["outputPath"] = str(output)
    return report


def materialize_task_board_projection(
    paths: RuntimePaths,
    board_path: Path,
    *,
    business_date: date | None = None,
    source_run_id: int | None = None,
) -> TaskBoardProjectionResult:
    """Snapshot TASK_BOARD.md into SQLite as a historical compatibility projection."""
    migrate(paths)
    content = board_path.read_text(encoding="utf-8")
    parsed = parse_task_board_markdown(content)
    fingerprint = _content_sha256(content)
    snapshot_key = f"task-board-markdown-v1:{fingerprint}"
    run_id = source_run_id or begin_ingestion_run(
        paths,
        trigger_type="task-board-markdown-projection",
        business_date=business_date,
        adapter_versions={"task-board-markdown": "projection-v1"},
    )
    try:
        with connect(paths) as connection:
            connection.execute("DELETE FROM task_board_items WHERE snapshot_key = ?", (snapshot_key,))
            connection.execute("DELETE FROM task_board_projects WHERE snapshot_key = ?", (snapshot_key,))
            connection.execute(
                """
                INSERT INTO task_board_snapshots(
                    snapshot_key, board_path, content_sha256, projected_at, source_run_id, status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO UPDATE SET
                    board_path=excluded.board_path,
                    content_sha256=excluded.content_sha256,
                    projected_at=excluded.projected_at,
                    source_run_id=excluded.source_run_id,
                    status=excluded.status,
                    details_json=excluded.details_json
                """,
                (
                    snapshot_key,
                    str(board_path.resolve()),
                    fingerprint,
                    datetime.now().astimezone().isoformat(),
                    run_id,
                    "ready",
                    json.dumps(
                        {
                            "projection": "task-board-markdown-v1",
                            "authority": "Nova-Task v2 SQLite authority; TASK_BOARD.md historical projection",
                            "counts": parsed["counts"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO task_board_projects(snapshot_key, project_ordinal, section, project)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_key,
                        project["projectOrdinal"],
                        project["section"],
                        project["project"],
                    )
                    for project in parsed["projects"]
                ],
            )
            connection.executemany(
                """
                INSERT INTO task_board_items(
                    snapshot_key, item_key, project_ordinal, item_ordinal, section, project,
                    done, content, agent, identified_task_id, source_line, raw_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_key,
                        item["itemKey"],
                        item["projectOrdinal"],
                        item["itemOrdinal"],
                        item["section"],
                        item["project"],
                        int(item["done"]),
                        item["content"],
                        item["agent"],
                        item["identifiedTaskId"],
                        item["sourceLine"],
                        item["rawLine"],
                    )
                    for item in parsed["items"]
                ],
            )
        if source_run_id is None:
            finish_ingestion_run(paths, run_id, status="completed")
    except Exception as error:
        if source_run_id is None:
            finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise
    counts = parsed["counts"]
    return TaskBoardProjectionResult(
        snapshot_key=snapshot_key,
        run_id=run_id,
        board_path=board_path.resolve(),
        project_count=counts["projects"],
        item_count=counts["items"],
        completed_count=counts["Completed"],
        in_progress_count=counts["InProgress"],
    )


def read_task_board_projection(paths: RuntimePaths, snapshot_key: str | None = None) -> dict | None:
    with connect(paths, read_only=True) as connection:
        if snapshot_key is None:
            snapshot = connection.execute(
                """
                SELECT snapshot_key, board_path, content_sha256, projected_at,
                       source_run_id, status, details_json
                FROM task_board_snapshots
                WHERE status = 'ready'
                ORDER BY projected_at DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            snapshot = connection.execute(
                """
                SELECT snapshot_key, board_path, content_sha256, projected_at,
                       source_run_id, status, details_json
                FROM task_board_snapshots
                WHERE snapshot_key = ? AND status = 'ready'
                """,
                (snapshot_key,),
            ).fetchone()
        if snapshot is None:
            return None
        projects = [
            dict(row)
            for row in connection.execute(
                """
                SELECT project_ordinal, section, project
                FROM task_board_projects
                WHERE snapshot_key = ?
                ORDER BY project_ordinal
                """,
                (snapshot["snapshot_key"],),
            )
        ]
        items = [
            dict(row)
            for row in connection.execute(
                """
                SELECT item_key, project_ordinal, item_ordinal, section, project,
                       done, content, agent, identified_task_id, source_line, raw_line
                FROM task_board_items
                WHERE snapshot_key = ?
                ORDER BY project_ordinal, item_ordinal
                """,
                (snapshot["snapshot_key"],),
            )
        ]
    return {
        "snapshotKey": snapshot["snapshot_key"],
        "boardPath": snapshot["board_path"],
        "contentSha256": snapshot["content_sha256"],
        "projectedAt": snapshot["projected_at"],
        "sourceRunId": snapshot["source_run_id"],
        "status": snapshot["status"],
        "details": json.loads(snapshot["details_json"]),
        "projects": [
            {
                "projectOrdinal": row["project_ordinal"],
                "section": row["section"],
                "project": row["project"],
            }
            for row in projects
        ],
        "items": [
            {
                "itemKey": row["item_key"],
                "projectOrdinal": row["project_ordinal"],
                "itemOrdinal": row["item_ordinal"],
                "section": row["section"],
                "project": row["project"],
                "done": bool(row["done"]),
                "content": row["content"],
                "agent": row["agent"] or "",
                "identifiedTaskId": row["identified_task_id"],
                "sourceLine": row["source_line"],
                "rawLine": row["raw_line"],
            }
            for row in items
        ],
        "preservedSources": {
            "taskBoardWriter": "historical-projection",
            "dashboardTasks": "nova-task-v2-sqlite-authority",
        },
    }


def authoritative_board_diary_snapshot(board_path: Path) -> dict:
    """Return checkbox-only diary task counts from the user-authoritative board."""
    return _authoritative_board_diary_snapshot_content(board_path.read_text(encoding="utf-8"))


def _authoritative_board_diary_snapshot_content(content: str) -> dict:
    in_progress = completed = 0
    for line in content.splitlines():
        match = re.match(r"^-\s*\[([ xX])\]\s+", line)
        if match is None:
            continue
        if match.group(1).strip().lower() == "x":
            completed += 1
        else:
            in_progress += 1
    return {"InProgress": in_progress, "Completed": completed}


def record_authoritative_board_mutation(
    paths: RuntimePaths,
    board_path: Path,
    *,
    requested_content: str,
    requested_done: bool,
    before_content: str,
    after_content: str,
) -> int:
    """Append an audit event after a successful user-authoritative board mutation."""
    migrate(paths)
    run_id = begin_ingestion_run(
        paths,
        trigger_type="task-board-authoritative-mutation",
        business_date=None,
        adapter_versions={"dashboard-task-patch": "audit-v1"},
    )
    try:
        identified = re.search(r"\[(T-[A-Za-z0-9-]+)\]", requested_content)
        with connect(paths) as connection:
            connection.execute(
                """
                INSERT INTO task_board_mutation_events(
                    audit_run_id, occurred_at, mutation_source, board_path, requested_content,
                    requested_done, identified_task_id, before_sha256, after_sha256,
                    before_snapshot_json, after_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    datetime.now().astimezone().isoformat(),
                    "dashboard-user-patch",
                    str(board_path.resolve()),
                    requested_content,
                    int(requested_done),
                    identified.group(1) if identified is not None else None,
                    _content_sha256(before_content),
                    _content_sha256(after_content),
                    json.dumps(_authoritative_board_diary_snapshot_content(before_content), sort_keys=True),
                    json.dumps(_authoritative_board_diary_snapshot_content(after_content), sort_keys=True),
                ),
            )
        finish_ingestion_run(paths, run_id, status="completed")
        return run_id
    except Exception as error:
        finish_ingestion_run(paths, run_id, status="failed", error_summary=str(error))
        raise
