import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect
from data_foundation.nova_task import create_task_node
from data_foundation.nova_task_layers import (
    ORIGIN_PLANNED,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
)
from data_foundation.nova_task_planning_import import apply_planning_import_artifact, import_planning_document
from data_foundation.paths import initialize_home


class NovaTaskPlanningImportTests(unittest.TestCase):
    def test_planning_document_can_create_pathless_planned_l1_tree(self):
        def fake_sender(**kwargs):
            self.assertIn("Document title:\nActanara Agent RFC", kwargs["prompt"])
            return """```yaml
nova_task:
  planning_import:
    document_title: "Actanara Agent RFC"
    project:
      proposed_title: "Actanara Agent"
      suggested_node_type: track
      proposed_level: 1
      matched_existing_node_id: ""
      workspace_root_path: ""
      reason: "RFC defines the product boundary."
      children:
        - proposed_title: "Planner subsystem"
          suggested_node_type: workstream
          proposed_level: 2
          reason: "Core planning workstream."
          children:
            - proposed_title: "Task tree importer"
              suggested_node_type: deliverable
              proposed_level: 3
              reason: "Document import deliverable."
              children: []
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")

            result = import_planning_document(
                paths,
                document_title="Actanara Agent RFC",
                document_text="# Actanara Agent RFC\n\nBuild the planner subsystem.",
                apply=False,
                sender=fake_sender,
            )
            applied = apply_planning_import_artifact(paths, artifact_path=result.artifact_path)
            with self.assertRaises(ValueError):
                apply_planning_import_artifact(paths, artifact_path=result.artifact_path)

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    "SELECT node_id, parent_node_id, node_type, title, status, metadata_json FROM nova_task_nodes ORDER BY parent_node_id, title"
                ).fetchall()
            artifact_head = Path(result.artifact_path).read_text(encoding="utf-8")[:200]

        self.assertFalse(result.applied)
        self.assertEqual(result.preview_tree["title"], "Actanara Agent")
        self.assertEqual(result.preview_tree["action"], "create")
        self.assertEqual(result.validation_report["summary"], {"create": 3, "reuse": 0, "skip": 0})
        self.assertEqual(result.node_created_count, 3)
        self.assertTrue(applied.applied)
        self.assertTrue(applied.root_created)
        self.assertEqual(applied.node_created_count, 3)
        self.assertEqual(applied.node_reused_count, 0)
        by_title = {row["title"]: row for row in rows}
        self.assertEqual((by_title["Actanara Agent"]["node_type"], by_title["Actanara Agent"]["status"]), ("track", "planned"))
        self.assertEqual(by_title["Planner subsystem"]["parent_node_id"], by_title["Actanara Agent"]["node_id"])
        self.assertEqual(by_title["Task tree importer"]["parent_node_id"], by_title["Planner subsystem"]["node_id"])
        metadata = json.loads(by_title["Actanara Agent"]["metadata_json"])
        self.assertEqual(metadata["origin"], ORIGIN_PLANNED)
        self.assertEqual(metadata["stateAuthority"], STATE_AUTHORITY_PLANNED_STATE_MACHINE)
        self.assertEqual(metadata["createdFrom"], "nova_task_planning_import")
        self.assertIn("- applied: true", artifact_head)

    def test_planning_import_reuses_existing_l1_and_skips_invalid_level(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  planning_import:
    document_title: "Actanara Roadmap"
    project:
      proposed_title: "actanara"
      suggested_node_type: track
      proposed_level: 1
      matched_existing_node_id: "NT-ROOT"
      workspace_root_path: ""
      reason: "Roadmap for existing project."
      children:
        - proposed_title: "Valid subsystem"
          suggested_node_type: workstream
          proposed_level: 2
          reason: "Valid L2."
          children: []
        - proposed_title: "Invalid deliverable"
          suggested_node_type: deliverable
          proposed_level: 3
          reason: "Cannot hang L3 directly under L1."
          children: []
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = import_planning_document(
                paths,
                document_title="Actanara Roadmap",
                document_text="# Actanara Roadmap",
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    "SELECT node_id, parent_node_id, node_type, title FROM nova_task_nodes ORDER BY title"
                ).fetchall()

        self.assertEqual(result.root_node_id, root.node_id)
        self.assertFalse(result.root_created)
        self.assertEqual(result.node_reused_count, 1)
        self.assertEqual(result.node_created_count, 1)
        self.assertEqual(result.skipped_count, 1)
        titles = {row["title"]: row for row in rows}
        self.assertIn("Valid subsystem", titles)
        self.assertNotIn("Invalid deliverable", titles)
        self.assertEqual(titles["Valid subsystem"]["parent_node_id"], root.node_id)


if __name__ == "__main__":
    unittest.main()
