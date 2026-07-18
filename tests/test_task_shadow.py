import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_foundation.db import connect
from data_foundation.paths import initialize_home
from data_foundation.tasks import (
    import_legacy_task_db,
    materialize_task_board_projection,
    materialize_task_report_events,
    parse_task_board_markdown,
    read_task_board_projection,
    task_board_observation_report,
    task_shadow_comparison_report,
)


def _create_legacy_task_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, last_updated DATETIME)")
            connection.execute(
                "CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT, status TEXT, progress INTEGER, last_updated DATETIME)"
            )
            connection.execute(
                "CREATE TABLE task_updates (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, report_date TEXT, progress_delta INTEGER, status TEXT, report_file TEXT)"
            )
            connection.execute("INSERT INTO projects VALUES ('P-Diary', 'Diary', '2026-05-27T01:00:00')")
            connection.execute(
                "INSERT INTO tasks VALUES ('T-260527-001', 'P-Diary', 'Task shadow', 'InProgress', 20, '2026-05-27T01:00:00')"
            )
            connection.execute(
                "INSERT INTO task_updates(task_id, report_date, progress_delta, status, report_file) VALUES (?, ?, ?, ?, ?)",
                ("T-260527-001", "2026-05-27", 20, "InProgress", "report.md"),
            )


class TaskShadowTests(unittest.TestCase):
    def test_read_only_task_import_matches_source_and_replaces_changed_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "nova_tasks.db"
            _create_legacy_task_db(source)
            paths = initialize_home(Path(tmp) / "Actanara")

            first = import_legacy_task_db(paths, source, business_date=date(2026, 5, 27))
            self.assertEqual((first.project_count, first.task_count, first.update_count), (1, 1, 1))
            self.assertTrue(task_shadow_comparison_report(paths, source)["matched"])

            with closing(sqlite3.connect(source)) as connection:
                with connection:
                    connection.execute("UPDATE tasks SET progress = 35 WHERE id = 'T-260527-001'")
                    connection.execute("DELETE FROM task_updates")
            second = import_legacy_task_db(paths, source, business_date=date(2026, 5, 27))

            self.assertNotEqual(first.run_id, second.run_id)
            self.assertTrue(task_shadow_comparison_report(paths, source)["matched"])
            with connect(paths, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT progress FROM legacy_tasks").fetchone()[0], 35)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_task_updates").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_shadow_imports").fetchone()[0], 2)
            with closing(sqlite3.connect(source)) as connection:
                self.assertEqual(connection.execute("SELECT progress FROM tasks").fetchone()[0], 35)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_updates").fetchone()[0], 0)

    def test_empty_legacy_database_is_a_valid_observed_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "nova_tasks.db"
            with closing(sqlite3.connect(source)) as connection:
                with connection:
                    connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, last_updated DATETIME)")
                    connection.execute(
                        "CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT, title TEXT, status TEXT, progress INTEGER, last_updated DATETIME)"
                    )
                    connection.execute(
                        "CREATE TABLE task_updates (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, report_date TEXT, progress_delta INTEGER, status TEXT, report_file TEXT)"
                    )
            paths = initialize_home(Path(tmp) / "Actanara")
            result = import_legacy_task_db(paths, source)
            self.assertEqual((result.project_count, result.task_count, result.update_count), (0, 0, 0))
            self.assertTrue(task_shadow_comparison_report(paths, source)["matched"])

    def test_report_event_identity_is_repeatable_and_board_contract_stays_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Diary"
            report_dir = root / "diary-2026-05-27"
            report_dir.mkdir(parents=True)
            report = report_dir / "技术进展-260527.md"
            report.write_text(
                """# Report

```yaml
date: "2026-05-27"
task_updates:
  - id: "T-260527-001"
    parent_id: "P-Diary"
    title: "Task event"
    status: "InProgress"
    progress_delta: 20
```
""",
                encoding="utf-8",
            )
            board = root / "TASK_BOARD.md"
            board.write_text(
                "- [ ] **[T-260527-001]** Task event\n- [x] Finished without id\n> Legacy prose references content[]\n",
                encoding="utf-8",
            )
            paths = initialize_home(Path(tmp) / "Actanara")

            first = materialize_task_report_events(paths, root, business_date=date(2026, 5, 27))
            with connect(paths, read_only=True) as connection:
                first_key = connection.execute("SELECT event_key FROM task_report_update_events").fetchone()[0]
            second = materialize_task_report_events(paths, root, business_date=date(2026, 5, 27))
            with connect(paths, read_only=True) as connection:
                second_key = connection.execute("SELECT event_key FROM task_report_update_events").fetchone()[0]
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM task_report_update_events").fetchone()[0], 1)
            observation = task_board_observation_report(paths, board, second.run_id)

            self.assertEqual((first.event_count, second.event_count), (1, 1))
            self.assertEqual(first_key, second_key)
            self.assertEqual(observation["diaryTaskSnapshot"], {"InProgress": 2, "Completed": 1})
            self.assertEqual(observation["authoritativeBoardDiarySnapshot"], {"InProgress": 1, "Completed": 1})
            self.assertEqual(observation["checkboxRows"], 2)
            self.assertEqual(observation["overlapTaskIds"], ["T-260527-001"])
            self.assertEqual(observation["status"], "board_authority_confirmed_events_non_authoritative")
            self.assertFalse(observation["canEnable"]["reportEventsAsCurrentTaskState"])

            report.write_text(report.read_text(encoding="utf-8").replace("20", "35"), encoding="utf-8")
            materialize_task_report_events(paths, root, business_date=date(2026, 5, 27))
            with connect(paths, read_only=True) as connection:
                changed_key = connection.execute("SELECT event_key FROM task_report_update_events").fetchone()[0]
            self.assertNotEqual(changed_key, first_key)

    def test_report_event_materialization_reads_english_technical_filename_when_profile_is_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Diary"
            report_dir = root / "diary-2026-05-27"
            report_dir.mkdir(parents=True)
            (report_dir / "技术进展-260527.md").write_text(
                """# Report

```yaml
date: "2026-05-27"
task_updates:
  - id: "T-ZH"
    title: "Chinese filename should not be imported"
    status: "InProgress"
    progress_delta: 10
```
""",
                encoding="utf-8",
            )
            (report_dir / "technical-260527.md").write_text(
                """# Technical report

```yaml
date: "2026-05-27"
task_updates:
  - id: "T-EN"
    title: "English filename import"
    status: "InProgress"
    progress_delta: 20
```
""",
                encoding="utf-8",
            )
            paths = initialize_home(Path(tmp) / "Actanara")

            result = materialize_task_report_events(paths, root, business_date=date(2026, 5, 27), language_profile="en")

            self.assertEqual(result.event_count, 1)
            with connect(paths, read_only=True) as connection:
                rows = connection.execute("SELECT source_task_id, source_path FROM task_report_update_events").fetchall()
            self.assertEqual([(row["source_task_id"], row["source_path"]) for row in rows], [("T-EN", "diary-2026-05-27/technical-260527.md")])

    def test_task_board_markdown_projection_preserves_board_authority(self):
        content = """# Board

## 🟡 进行中
### Actanara Data Foundation
- [ ] **[T-260529-001]** Finish phase 11 ← **@codex**
- [x] Document phase 10

## ✅ 已完成
### Releases
| 2026-05-29 | Phase 9 summary button | shipped |
"""
        parsed = parse_task_board_markdown(content)
        self.assertEqual(parsed["counts"], {"projects": 2, "items": 3, "Completed": 2, "InProgress": 1})
        self.assertEqual(parsed["items"][0]["identifiedTaskId"], "T-260529-001")
        self.assertEqual(parsed["items"][0]["agent"], "codex")
        self.assertEqual(parsed["items"][2]["content"], "[2026-05-29] Phase 9 summary button")

        with tempfile.TemporaryDirectory() as tmp:
            board = Path(tmp) / "TASK_BOARD.md"
            board.write_text(content, encoding="utf-8")
            paths = initialize_home(Path(tmp) / "Actanara")
            first = materialize_task_board_projection(paths, board, business_date=date(2026, 5, 29))
            snapshot = read_task_board_projection(paths, first.snapshot_key)
            self.assertEqual(first.item_count, 3)
            self.assertEqual(
                snapshot["details"]["authority"],
                "Nova-Task v2 SQLite authority; TASK_BOARD.md historical projection",
            )
            self.assertEqual(snapshot["preservedSources"]["taskBoardWriter"], "historical-projection")
            self.assertEqual(snapshot["items"][0]["content"], "**[T-260529-001]** Finish phase 11")

            board.write_text(content.replace("[ ] **[T-260529-001]**", "[x] **[T-260529-001]**"), encoding="utf-8")
            second = materialize_task_board_projection(paths, board, business_date=date(2026, 5, 29))
            self.assertNotEqual(first.snapshot_key, second.snapshot_key)
            self.assertEqual(second.completed_count, 3)
            latest = read_task_board_projection(paths)
            self.assertEqual(latest["snapshotKey"], second.snapshot_key)


if __name__ == "__main__":
    unittest.main()
