import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from data_foundation.db import connect
from data_foundation.paths import initialize_home
from data_foundation.settings import write_settings
from data_foundation.tasks import record_authoritative_board_mutation
from app.services import foundation as dashboard_foundation

class TaskMutationAuditTests(unittest.TestCase):
    def test_authoritative_board_mutation_audit_records_transition_without_writing_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = Path(tmp) / "TASK_BOARD.md"
            before = "## Active\n### Diary\n- [ ] **[T-260527-001]** Publish audit\n"
            after = "## Active\n### Diary\n- [x] **[T-260527-001]** Publish audit\n"
            board.write_text(after, encoding="utf-8")
            paths = initialize_home(Path(tmp) / "Actanara")

            run_id = record_authoritative_board_mutation(
                paths,
                board,
                requested_content="**[T-260527-001]** Publish audit",
                requested_done=True,
                before_content=before,
                after_content=after,
            )
            with connect(paths, read_only=True) as connection:
                event = connection.execute(
                    """
                    SELECT mutation_source, identified_task_id, requested_done,
                           before_snapshot_json, after_snapshot_json
                    FROM task_board_mutation_events WHERE audit_run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                run = connection.execute("SELECT status FROM ingestion_runs WHERE id = ?", (run_id,)).fetchone()
            self.assertEqual(board.read_text(encoding="utf-8"), after)
            self.assertEqual(event["mutation_source"], "dashboard-user-patch")
            self.assertEqual(event["identified_task_id"], "T-260527-001")
            self.assertEqual(event["requested_done"], 1)
            self.assertEqual(json.loads(event["before_snapshot_json"]), {"InProgress": 1, "Completed": 0})
            self.assertEqual(json.loads(event["after_snapshot_json"]), {"InProgress": 0, "Completed": 1})
            self.assertEqual(run["status"], "completed")

    def test_dashboard_audit_sink_uses_settings_authority_not_archived_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = Path(tmp) / "TASK_BOARD.md"
            paths = initialize_home(Path(tmp) / "Actanara")
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home), "TASK_AUDIT_SINK": "legacy"}):
                self.assertIsNotNone(
                    dashboard_foundation.audit_task_board_mutation(
                        board_path=board,
                        content="Task",
                        done=True,
                        before_content="- [ ] Task\n",
                        after_content="- [x] Task\n",
                    )
                )

            write_settings({"runtimeSources": {"taskAuditSink": "legacy"}}, paths)
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}),
                patch.object(dashboard_foundation, "record_authoritative_board_mutation", return_value=41) as record,
            ):
                self.assertIsNone(
                    dashboard_foundation.audit_task_board_mutation(
                        board_path=board,
                        content="Task",
                        done=True,
                        before_content="- [ ] Task\n",
                        after_content="- [x] Task\n",
                    ),
                )
            record.assert_not_called()

    def test_legacy_text_patch_route_is_removed(self):
        router_source = (ROOT / "src" / "dashboard" / "app" / "routers" / "tasks.py").read_text(encoding="utf-8")

        self.assertNotIn('@router.patch("/tasks")', router_source)
        self.assertNotIn("def update_task(", router_source)


if __name__ == "__main__":
    unittest.main()
