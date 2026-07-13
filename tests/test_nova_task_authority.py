import sys
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect, migrate
from data_foundation.nova_task import (
    confirm_candidate_as_task,
    create_task_candidate,
    create_task_node,
    defer_task_candidate,
    export_task_board_markdown,
    ingest_nova_task_evidence,
    list_task_candidates,
    merge_task_candidate,
    pending_candidate_count,
    reconcile_workspace_project_anchors,
    reject_task_candidate,
    render_task_graph_context,
    render_task_board_markdown,
    supersede_task_candidate,
    update_task_node,
)
from data_foundation.nova_task_layers import (
    NODE_CREATED_BY_AGENT,
    NODE_MANAGED_BY_AGENT,
    NODE_MANAGED_BY_HUMAN,
    ORIGIN_OBSERVED,
    ORIGIN_PLANNED,
    STATE_AUTHORITY_OBSERVED_SIGNAL,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
    project_graph_metadata,
)
from data_foundation.paths import initialize_home


class NovaTaskAuthorityTests(unittest.TestCase):
    def test_authoritative_task_graph_supports_parent_child_nodes_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            parent = create_task_node(paths, title="Nova-Task v2 system rebuild", node_type="track", actor="operator")
            child = create_task_node(
                paths,
                title="Task identity model",
                node_type="workstream",
                parent_node_id=parent.node_id,
                actor="operator",
            )

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    "SELECT node_id, parent_node_id, node_type, title FROM nova_task_nodes ORDER BY created_at"
                ).fetchall()
                audit_count = connection.execute("SELECT COUNT(*) FROM nova_task_audit_log").fetchone()[0]

        self.assertEqual(rows[0]["title"], "Nova-Task v2 system rebuild")
        self.assertEqual(rows[1]["parent_node_id"], parent.node_id)
        self.assertEqual(rows[1]["node_id"], child.node_id)
        self.assertEqual(audit_count, 2)

    def test_update_task_node_changes_authority_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            parent = create_task_node(paths, node_id="NT-PARENT", title="Parent", actor="operator")
            child = create_task_node(paths, node_id="NT-CHILD", title="Child", actor="operator")

            updated = update_task_node(
                paths,
                node_id=child.node_id,
                actor="dashboard",
                title="Renamed child",
                status="completed",
                parent_node_id=parent.node_id,
                metadata={"completionMethod": "Manual dashboard edit"},
            )

            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT title, status, progress, parent_node_id, completed_at, metadata_json FROM nova_task_nodes WHERE node_id = ?",
                    (child.node_id,),
                ).fetchone()
                audit = connection.execute(
                    "SELECT actor, action FROM nova_task_audit_log WHERE action = 'update_node'"
                ).fetchone()

        self.assertEqual(updated.parent_node_id, parent.node_id)
        self.assertEqual((row["title"], row["status"], row["progress"], row["parent_node_id"]), ("Renamed child", "done", 100, parent.node_id))
        self.assertIsNotNone(row["completed_at"])
        self.assertEqual(json.loads(row["metadata_json"])["completionMethod"], "Manual dashboard edit")
        self.assertEqual((audit["actor"], audit["action"]), ("dashboard", "update_node"))

    def test_human_node_under_agent_branch_claims_only_ancestor_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            root = create_task_node(
                paths,
                node_id="NT-AGENT-ROOT",
                title="Agent root",
                node_type="track",
                actor="pipeline",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                ),
            )
            branch = create_task_node(
                paths,
                node_id="NT-AGENT-BRANCH",
                title="Agent branch",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="pipeline",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                ),
            )
            sibling = create_task_node(
                paths,
                node_id="NT-AGENT-SIBLING",
                title="Agent sibling",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="pipeline",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                ),
            )
            create_task_node(
                paths,
                node_id="NT-HUMAN-CHILD",
                title="Human child",
                node_type="task",
                parent_node_id=branch.node_id,
                actor="dashboard",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            with connect(paths, read_only=True) as connection:
                rows = {
                    row["node_id"]: json.loads(row["metadata_json"])
                    for row in connection.execute(
                        """
                        SELECT node_id, metadata_json
                        FROM nova_task_nodes
                        WHERE node_id IN ('NT-AGENT-ROOT', 'NT-AGENT-BRANCH', 'NT-AGENT-SIBLING', 'NT-HUMAN-CHILD')
                        """
                    )
                }

        self.assertEqual(rows["NT-AGENT-ROOT"]["createdBy"], NODE_CREATED_BY_AGENT)
        self.assertEqual(rows["NT-AGENT-ROOT"]["managedBy"], NODE_MANAGED_BY_HUMAN)
        self.assertEqual(rows["NT-AGENT-BRANCH"]["managedBy"], NODE_MANAGED_BY_HUMAN)
        self.assertEqual(rows["NT-HUMAN-CHILD"]["managedBy"], NODE_MANAGED_BY_HUMAN)
        self.assertEqual(rows["NT-AGENT-SIBLING"]["managedBy"], NODE_MANAGED_BY_AGENT)

    def test_candidate_creation_is_idempotent_and_not_authoritative_until_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            first = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="New deployment guide",
                reason="LLM saw repeated setup-guide work",
                evidence=["technical report line 1"],
                confidence="medium",
            )
            second = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="New deployment guide",
                reason="LLM saw repeated setup-guide work",
                evidence=["technical report line 1"],
                confidence="medium",
            )

            with connect(paths, read_only=True) as connection:
                node_count_before = connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0]
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]
            pending_count = pending_candidate_count(paths)

            self.assertEqual(first.candidate_id, second.candidate_id)
            self.assertEqual(first.status, "pending_review")
            self.assertEqual(node_count_before, 0)
            self.assertEqual(candidate_count, 1)
            self.assertEqual(pending_count, 1)

    def test_workspace_project_anchor_reconciliation_creates_level_one_review_candidate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "open-nova"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova"\n', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")

            first = reconcile_workspace_project_anchors(paths, observed_paths=[project / "src" / "app.py"])
            second = reconcile_workspace_project_anchors(paths, observed_paths=[project / "src" / "app.py"])

            with connect(paths, read_only=True) as connection:
                node_count = connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0]
                candidate = connection.execute(
                    "SELECT candidate_type, proposed_title, confidence, metadata_json FROM nova_task_candidates"
                ).fetchone()

        metadata = json.loads(candidate["metadata_json"])
        self.assertEqual(first.candidate_count, 1)
        self.assertEqual(second.candidate_count, 0)
        self.assertEqual(node_count, 0)
        self.assertEqual((candidate["candidate_type"], candidate["proposed_title"], candidate["confidence"]), ("parent_task", "open-nova", "high"))
        self.assertEqual(metadata["candidateKind"], "project_anchor")
        self.assertEqual(metadata["suggestedNodeType"], "track")
        self.assertEqual(metadata["level"], 1)
        self.assertEqual(metadata["reviewPolicy"], "manual_required_for_level_1")

    def test_workspace_project_anchor_binds_pathless_planned_l1_without_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "NovaAgent"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "NovaAgent"\n', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            planned = create_task_node(
                paths,
                node_id="NT-PLANNED-L1",
                title="NovaAgent",
                node_type="track",
                status="planned",
                actor="planning-import",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                    createdFrom="nova_task_planning_import",
                ),
            )

            result = reconcile_workspace_project_anchors(paths, observed_paths=[project / "src" / "agent.py"])

            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT metadata_json FROM nova_task_nodes WHERE node_id = ?",
                    (planned.node_id,),
                ).fetchone()
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]
                audit = connection.execute(
                    "SELECT action FROM nova_task_audit_log WHERE action = 'bind_l1_workspace_anchor'"
                ).fetchone()

        metadata = json.loads(row["metadata_json"])
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(candidate_count, 0)
        self.assertEqual(metadata["workspace"]["rootPath"], str(project))
        self.assertEqual(metadata["workspace"]["displayName"], "NovaAgent")
        self.assertEqual(metadata["workspace"]["boundFrom"], "workspace-attribution")
        self.assertIsNotNone(audit)

    def test_workspace_project_anchor_matching_path_backed_l1_does_not_duplicate_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TokenClock"
            project.mkdir()
            (project / "package.json").write_text('{"name":"TokenClock"}', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            create_task_node(
                paths,
                node_id="NT-TOKENCLOCK",
                title="TokenClock",
                node_type="track",
                status="planned",
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                    workspace={"rootPath": str(project), "displayName": "TokenClock"},
                ),
            )

            first = reconcile_workspace_project_anchors(paths, observed_paths=[project / "Sources" / "Clock.swift"])
            second = reconcile_workspace_project_anchors(paths, observed_paths=[project / "Sources" / "Clock.swift"])

            with connect(paths, read_only=True) as connection:
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]
                node_count = connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0]

        self.assertEqual(first.candidate_count, 0)
        self.assertEqual(second.candidate_count, 0)
        self.assertEqual(candidate_count, 0)
        self.assertEqual(node_count, 1)

    def test_workspace_project_anchor_title_aliases_match_existing_l1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "open-nova"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova系统"\n', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            create_task_node(
                paths,
                node_id="NT-OPEN-NOVA",
                title="Open Nova",
                node_type="track",
                status="planned",
                actor="planning-import",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            result = reconcile_workspace_project_anchors(paths, observed_paths=[project / "src" / "app.py"])

            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT metadata_json FROM nova_task_nodes WHERE node_id = 'NT-OPEN-NOVA'"
                ).fetchone()
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]

        metadata = json.loads(row["metadata_json"])
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(candidate_count, 0)
        self.assertEqual(metadata["workspace"]["rootPath"], str(project))
        self.assertEqual(metadata["workspace"]["displayName"], "open-nova系统")

    def test_low_confidence_workspace_anchor_does_not_create_l1_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loose = root / "notes" / "scratch.py"
            loose.parent.mkdir(parents=True)
            loose.write_text("print('not a project marker')\n", encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")

            result = reconcile_workspace_project_anchors(paths, observed_paths=[loose])

            with connect(paths, read_only=True) as connection:
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]
                node_count = connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0]

        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(candidate_count, 0)
        self.assertEqual(node_count, 0)

    def test_confirming_workspace_project_anchor_uses_suggested_track_type_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TokenClock"
            project.mkdir()
            (project / "package.json").write_text('{"name":"TokenClock"}', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            reconcile_workspace_project_anchors(paths, observed_paths=[project / "Sources" / "Clock.swift"])
            with connect(paths, read_only=True) as connection:
                candidate_id = connection.execute("SELECT candidate_id FROM nova_task_candidates").fetchone()["candidate_id"]

            node = confirm_candidate_as_task(paths, candidate_id=candidate_id, actor="operator", reason="Approve project anchor")

            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT node_type, status, metadata_json FROM nova_task_nodes WHERE node_id = ?",
                    (node.node_id,),
                ).fetchone()

        metadata = json.loads(row["metadata_json"])
        self.assertEqual(node.node_type, "track")
        self.assertEqual(row["node_type"], "track")
        self.assertEqual(row["status"], "active")
        self.assertEqual(metadata["origin"], ORIGIN_OBSERVED)
        self.assertEqual(metadata["stateAuthority"], STATE_AUTHORITY_OBSERVED_SIGNAL)
        self.assertEqual(metadata["createdFrom"], "workspace-attribution")
        self.assertEqual(metadata["candidateMetadata"]["candidateKind"], "project_anchor")
        self.assertEqual(metadata["workspace"]["rootPath"], str(project))
        self.assertEqual(metadata["workspace"]["displayName"], "TokenClock")

    def test_confirming_workspace_project_anchor_attaches_to_existing_l1_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "open-nova"
            project.mkdir()
            (project / "pyproject.toml").write_text('[project]\nname = "open-nova"\n', encoding="utf-8")
            paths = initialize_home(root / "NovaDiary")
            reconcile_workspace_project_anchors(paths, observed_paths=[project / "src" / "app.py"])
            with connect(paths, read_only=True) as connection:
                candidate_id = connection.execute("SELECT candidate_id FROM nova_task_candidates").fetchone()["candidate_id"]
            existing = create_task_node(
                paths,
                node_id="NT-EXISTING-L1",
                title="Open Nova",
                node_type="track",
                status="planned",
                actor="planning-import",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            node = confirm_candidate_as_task(paths, candidate_id=candidate_id, actor="operator", reason="Approve project anchor")

            with connect(paths, read_only=True) as connection:
                nodes = connection.execute("SELECT node_id, metadata_json FROM nova_task_nodes ORDER BY node_id").fetchall()
                candidate = connection.execute(
                    "SELECT status, matched_node_id FROM nova_task_candidates WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()
                decision = connection.execute(
                    "SELECT decision_type, after_json FROM nova_task_reconciliation_decisions WHERE candidate_id = ?",
                    (candidate_id,),
                ).fetchone()

        metadata = json.loads(nodes[0]["metadata_json"])
        self.assertEqual(node.node_id, existing.node_id)
        self.assertEqual([row["node_id"] for row in nodes], [existing.node_id])
        self.assertEqual((candidate["status"], candidate["matched_node_id"]), ("confirmed", existing.node_id))
        self.assertEqual(decision["decision_type"], "attached")
        self.assertEqual(json.loads(decision["after_json"])["decisionType"], "attach_existing_l1")
        self.assertEqual(metadata["workspace"]["rootPath"], str(project))

    def test_confirming_planning_overlay_l1_stays_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            candidate = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Future Roadmap Project",
                reason="Roadmap import proposed a future project.",
                evidence=["roadmap.md"],
                confidence="high",
                metadata={
                    "novaTaskLayer": "planning_overlay",
                    "source": "nova_task_planning_import",
                    "candidateKind": "planning_intent",
                    "suggestedNodeType": "track",
                    "level": 1,
                },
            )

            node = confirm_candidate_as_task(paths, candidate_id=candidate.candidate_id, actor="operator", reason="Approve roadmap intent")

            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT node_type, status, metadata_json FROM nova_task_nodes WHERE node_id = ?",
                    (node.node_id,),
                ).fetchone()

        metadata = json.loads(row["metadata_json"])
        self.assertEqual((row["node_type"], row["status"]), ("track", "planned"))
        self.assertEqual(metadata["origin"], ORIGIN_PLANNED)
        self.assertEqual(metadata["stateAuthority"], STATE_AUTHORITY_PLANNED_STATE_MACHINE)

    def test_confirming_candidate_creates_task_node_decision_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            parent = create_task_node(paths, title="Task system", node_type="track", actor="operator")
            candidate = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="Candidate review UI",
                proposed_parent_node_id=parent.node_id,
                reason="LLM proposed review workflow",
                evidence=["technical report line 2"],
                confidence="high",
            )

            node = confirm_candidate_as_task(
                paths,
                candidate_id=candidate.candidate_id,
                actor="operator",
                title="Dashboard candidate review UI",
                reason="Approved as current phase subtask",
            )

            with connect(paths, read_only=True) as connection:
                candidate_status = connection.execute(
                    "SELECT status FROM nova_task_candidates WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                ).fetchone()["status"]
                decisions = connection.execute("SELECT COUNT(*) FROM nova_task_reconciliation_decisions").fetchone()[0]
                audit_actions = [
                    row["action"]
                    for row in connection.execute("SELECT action FROM nova_task_audit_log ORDER BY occurred_at, audit_id")
                ]
            pending_count = pending_candidate_count(paths)

            self.assertEqual(node.title, "Dashboard candidate review UI")
            self.assertEqual(node.parent_node_id, parent.node_id)
            self.assertEqual(node.node_type, "subtask")
            self.assertEqual(candidate_status, "confirmed")
            self.assertEqual(decisions, 1)
            self.assertIn("confirm_candidate_as_task", audit_actions)
            self.assertEqual(pending_count, 0)

    def test_all_migrations_include_nova_task_authority_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)
            with connect(paths, read_only=True) as connection:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'nova_task_%'"
                    )
                }
        self.assertIn("nova_task_nodes", tables)
        self.assertIn("nova_task_candidates", tables)
        self.assertIn("nova_task_reconciliation_decisions", tables)
        self.assertIn("nova_task_audit_log", tables)

    def test_status_vocabulary_migration_allows_paused_pending_review_and_superseded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            node = create_task_node(paths, node_id="NT-PAUSED", title="Paused work", actor="operator")
            update_task_node(paths, node_id=node.node_id, actor="operator", status="paused")
            candidate = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Future duplicate",
                reason="Candidate status vocabulary",
                evidence=["test"],
            )
            with connect(paths) as connection:
                connection.execute(
                    "UPDATE nova_task_candidates SET status = 'superseded' WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                )
                rows = connection.execute(
                    "SELECT status FROM nova_task_nodes WHERE node_id = ?",
                    (node.node_id,),
                ).fetchone()
                candidate_row = connection.execute(
                    "SELECT status FROM nova_task_candidates WHERE candidate_id = ?",
                    (candidate.candidate_id,),
                ).fetchone()

        self.assertEqual(rows["status"], "paused")
        self.assertEqual(candidate.status, "pending_review")
        self.assertEqual(candidate_row["status"], "superseded")

    def test_task_board_render_is_deterministic_sqlite_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            parent = create_task_node(
                paths,
                node_id="NT-PHASE26",
                title="Nova-Task v2 system rebuild",
                node_type="track",
                progress=25,
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-PHASE26-BATCH2",
                title="SQLite board export",
                node_type="workstream",
                parent_node_id=parent.node_id,
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-CLOSED",
                title="Previous handoff sync",
                node_type="task",
                status="completed",
                progress=100,
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-PAUSED",
                title="Paused hardening track",
                node_type="task",
                status="paused",
                actor="operator",
            )

            first = render_task_board_markdown(paths)
            second = render_task_board_markdown(paths)

        self.assertEqual(first, second)
        self.assertIn("> Generated from Nova-Task v2 SQLite authority.", first)
        self.assertIn("- [ ] **[NT-PHASE26]** Nova-Task v2 system rebuild (track - Active - 25%)", first)
        self.assertIn("  - [ ] **[NT-PHASE26-BATCH2]** SQLite board export", first)
        self.assertIn("## Paused", first)
        self.assertIn("- [ ] **[NT-PAUSED]** Paused hardening track (task - Paused)", first)
        self.assertIn("## Done", first)
        self.assertIn("- [x] **[NT-CLOSED]** Previous handoff sync", first)

    def test_export_writes_projection_and_does_not_read_task_board_as_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            board = paths.task_board_path
            board.parent.mkdir(parents=True, exist_ok=True)
            board.write_text("- [x] **[NT-FAKE]** Manual board edit\n", encoding="utf-8")
            create_task_node(paths, node_id="NT-AUTH", title="Authoritative SQLite task", actor="operator")

            first = export_task_board_markdown(paths)
            first_content = board.read_text(encoding="utf-8")
            board.write_text("- [x] **[NT-FAKE]** Manual board edit\n", encoding="utf-8")
            second = export_task_board_markdown(paths)
            second_content = board.read_text(encoding="utf-8")

            with connect(paths, read_only=True) as connection:
                node_ids = [row["node_id"] for row in connection.execute("SELECT node_id FROM nova_task_nodes")]
                export_rows = connection.execute(
                    "SELECT content_sha256, target_path FROM nova_task_exports ORDER BY generated_at"
                ).fetchall()

        self.assertEqual(first_content, second_content)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(node_ids, ["NT-AUTH"])
        self.assertIn("**[NT-AUTH]** Authoritative SQLite task", second_content)
        self.assertNotIn("NT-FAKE", second_content)
        self.assertEqual(len(export_rows), 2)
        self.assertTrue(export_rows[0]["target_path"].endswith("TASK_BOARD.md"))

    def test_compact_task_graph_context_uses_sqlite_active_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            parent = create_task_node(paths, node_id="NT-ACTIVE", title="Active task", actor="operator")
            create_task_node(
                paths,
                node_id="NT-CHILD",
                title="Child task",
                parent_node_id=parent.node_id,
                node_type="subtask",
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-ARCHIVED",
                title="Archived task",
                status="archived",
                actor="operator",
            )

            context = render_task_graph_context(paths)

        self.assertIn("Nova-Task v2 compact active graph", context)
        self.assertIn("NT-ACTIVE | task | active | 0% | Active task", context)
        self.assertIn("NT-CHILD | subtask | active | 0% | Child task", context)
        self.assertNotIn("NT-ARCHIVED", context)

    def test_evidence_ingest_keeps_high_level_completion_as_review_candidate(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  matched_tasks:
    - task_id: "NT-ACTIVE"
      confidence: high
      event_type: progress
      summary: "Made progress"
      evidence: ["line a"]
  candidate_parent_tasks:
    - proposed_title: "New parent"
      reason: "Evidence showed new work"
      evidence: ["line b"]
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ACTIVE"
      proposed_title: "New child"
      reason: "Evidence showed child work"
      evidence: ["line c"]
  completion_signals:
    - task_id: "NT-ACTIVE"
      confidence: medium
      suggested_status: completed
      evidence: ["line d"]
  unresolved:
    - summary: "Unmatched work"
      reason: no_active_task_match
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            create_task_node(paths, node_id="NT-ACTIVE", title="Active task", actor="operator")

            first = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )
            second = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                node = connection.execute("SELECT status, progress FROM nova_task_nodes WHERE node_id = 'NT-ACTIVE'").fetchone()
                event_count = connection.execute("SELECT COUNT(*) FROM nova_task_events").fetchone()[0]
                candidates = connection.execute(
                    """
                    SELECT candidate_type, proposed_title, status, matched_node_id
                    FROM nova_task_candidates
                    ORDER BY candidate_type, proposed_title
                    """
                ).fetchall()

        self.assertEqual(first.event_count, 5)
        self.assertEqual(second.event_count, 0)
        self.assertEqual((node["status"], node["progress"]), ("active", 0))
        self.assertEqual(event_count, 5)
        self.assertEqual(first.candidate_count, 1)
        self.assertEqual(second.pending_candidate_count, 1)
        self.assertEqual(
            [(row["candidate_type"], row["proposed_title"], row["status"], row["matched_node_id"]) for row in candidates],
            [
                ("parent_task", "New parent", "pending_review", None),
            ],
        )

    def test_evidence_ingest_directly_applies_depth_three_completion_signal(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  completion_signals:
    - task_id: "NT-STEP"
      confidence: high
      suggested_status: completed
      completion_method: "Validated the final dashboard flow"
      evidence: ["line d"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            root = create_task_node(paths, node_id="NT-ROOT", title="Root", actor="operator")
            child = create_task_node(paths, node_id="NT-CHILD", title="Child", parent_node_id=root.node_id, actor="operator")
            create_task_node(paths, node_id="NT-STEP", title="Step", parent_node_id=child.node_id, actor="operator")

            first = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )
            second = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                node = connection.execute(
                    "SELECT status, progress, completed_at, metadata_json FROM nova_task_nodes WHERE node_id = 'NT-STEP'"
                ).fetchone()
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_audit_log WHERE action = 'auto_update_low_level_task_status'"
                ).fetchone()[0]

        metadata = json.loads(node["metadata_json"])
        self.assertEqual(first.event_count, 1)
        self.assertEqual(second.event_count, 0)
        self.assertEqual((node["status"], node["progress"] > 0, node["completed_at"] is not None), ("done", True, True))
        self.assertEqual(metadata["statusReason"], "Validated the final dashboard flow")
        self.assertEqual(audit_count, 1)

    def test_status_signal_for_depth_three_directly_updates_status_and_stores_tags(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  status_signals:
    - task_id: "NT-STEP"
      confidence: high
      target_status: blocked
      status_reason: "Waiting for external credentials"
      status_tags: ["waiting_external", "delayed"]
      evidence: ["line d"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            root = create_task_node(paths, node_id="NT-ROOT", title="Root", actor="operator")
            child = create_task_node(paths, node_id="NT-CHILD", title="Child", parent_node_id=root.node_id, actor="operator")
            create_task_node(paths, node_id="NT-STEP", title="Step", parent_node_id=child.node_id, actor="operator")

            result = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                node = connection.execute("SELECT status FROM nova_task_nodes WHERE node_id = 'NT-STEP'").fetchone()
                node_metadata = connection.execute(
                    "SELECT metadata_json FROM nova_task_nodes WHERE node_id = 'NT-STEP'"
                ).fetchone()

        metadata = json.loads(node_metadata["metadata_json"])
        self.assertEqual(result.event_count, 1)
        self.assertEqual(node["status"], "blocked")
        self.assertEqual(metadata["statusReason"], "Waiting for external credentials")
        self.assertEqual(metadata["statusTags"], ["waiting_external", "delayed"])

    def test_status_signal_for_l1_is_evidence_only_without_candidate(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  status_signals:
    - task_id: "NT-ROOT"
      confidence: medium
      target_status: blocked
      status_reason: "Waiting for user decision"
      status_tags: ["needs_review"]
      evidence: ["line d"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            create_task_node(paths, node_id="NT-ROOT", title="Root", actor="operator")

            result = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                node = connection.execute("SELECT status FROM nova_task_nodes WHERE node_id = 'NT-ROOT'").fetchone()

        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(node["status"], "active")

    def test_status_signal_with_needs_review_tag_is_evidence_only(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  status_signals:
    - task_id: "NT-STEP"
      confidence: medium
      target_status: blocked
      status_reason: "Needs operator judgement"
      status_tags: ["needs_review"]
      evidence: ["line d"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            root = create_task_node(paths, node_id="NT-ROOT", title="Root", actor="operator")
            create_task_node(paths, node_id="NT-STEP", title="Step", parent_node_id=root.node_id, actor="operator")

            result = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                node = connection.execute("SELECT status FROM nova_task_nodes WHERE node_id = 'NT-STEP'").fetchone()
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_audit_log WHERE action = 'auto_update_low_level_task_status'"
                ).fetchone()[0]

        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(node["status"], "active")
        self.assertEqual(audit_count, 0)

    def test_hierarchy_reconciliation_hints_write_evidence_and_direct_reparent_only(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  reparent_hints:
    - child_task_id: "NT-SMALL"
      proposed_parent_task_id: "NT-WORKSTREAM"
      reason: "Several tiny fixes now belong under the same workstream"
      evidence: ["three-day cluster"]
  group_hints:
    - proposed_parent_title: "Dashboard Settings subsystem"
      child_task_ids: ["NT-SMALL", "NT-OTHER"]
      reason: "Shared subsystem evidence"
      evidence: ["settings changes"]
  merge_hints:
    - source_task_ids: ["NT-SMALL", "NT-OTHER"]
      proposed_title: "Settings polish"
      reason: "Duplicate low-level tasks"
      evidence: ["similar titles"]
  demote_hints:
    - task_id: "NT-SMALL"
      reason: "too small as standalone task"
      evidence: ["single check"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            root = create_task_node(paths, node_id="NT-ROOT", title="Project", node_type="track", actor="operator")
            workstream = create_task_node(
                paths,
                node_id="NT-WORKSTREAM",
                title="Dashboard Settings subsystem",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            create_task_node(paths, node_id="NT-SMALL", title="One tiny fix", parent_node_id=workstream.node_id, actor="operator")
            create_task_node(paths, node_id="NT-OTHER", title="Another tiny fix", parent_node_id=workstream.node_id, actor="operator")

            result = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                small = connection.execute("SELECT parent_node_id FROM nova_task_nodes WHERE node_id = 'NT-SMALL'").fetchone()
                event_count = connection.execute("SELECT COUNT(*) FROM nova_task_events").fetchone()[0]
                candidate_count = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]

        self.assertEqual(result.event_count, 4)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(small["parent_node_id"], "NT-WORKSTREAM")
        self.assertEqual(event_count, 4)
        self.assertEqual(candidate_count, 0)

    def test_evidence_ingest_tolerates_missing_or_malformed_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")

            missing = ingest_nova_task_evidence(
                paths,
                markdown="# Technical report\nNo structured block\n",
                business_date=date(2026, 6, 7),
            )
            malformed = ingest_nova_task_evidence(
                paths,
                markdown="```yaml\nnova_task: [\n```",
                business_date=date(2026, 6, 7),
            )

            with connect(paths, read_only=True) as connection:
                event_count = connection.execute("SELECT COUNT(*) FROM nova_task_events").fetchone()[0]

        self.assertEqual(missing.event_count, 0)
        self.assertFalse(missing.malformed)
        self.assertTrue(malformed.malformed)
        self.assertEqual(event_count, 0)

    def test_subtask_under_candidate_parent_is_evidence_only_without_fk_failure(self):
        markdown = """# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-07"
  candidate_parent_tasks:
    - proposed_id: "CAND-PARENT"
      proposed_title: "Candidate parent"
      reason: "No active graph match"
      evidence: ["parent evidence"]
  candidate_subtasks:
    - proposed_parent_task_id: "CAND-PARENT"
      proposed_title: "Candidate child"
      reason: "Child belongs under proposed parent"
      evidence: ["child evidence"]
    - proposed_parent_task_id: "NT-CANDIDATE-1"
      proposed_title: "Candidate child with NT placeholder"
      reason: "LLM proposed a placeholder that is not an authority node"
      evidence: ["placeholder child evidence"]
```
"""
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")

            result = ingest_nova_task_evidence(
                paths,
                markdown=markdown,
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )

            with connect(paths, read_only=True) as connection:
                candidates = connection.execute(
                    """
                    SELECT candidate_type, proposed_title, proposed_parent_node_id, metadata_json
                    FROM nova_task_candidates
                    ORDER BY proposed_title
                    """
                ).fetchall()

        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(
            [(row["candidate_type"], row["proposed_title"], row["proposed_parent_node_id"]) for row in candidates],
            [
                ("parent_task", "Candidate parent", None),
            ],
        )

    def test_candidate_listing_reject_and_defer_write_decisions_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            first = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Review candidate one",
                reason="Evidence one",
                evidence=["line 1"],
                confidence="high",
            )
            second = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Review candidate two",
                reason="Evidence two",
                evidence=["line 2"],
                confidence="medium",
            )

            listed = list_task_candidates(paths)
            rejected = reject_task_candidate(
                paths,
                candidate_id=first.candidate_id,
                actor="operator",
                reason="Not current work",
            )
            deferred = defer_task_candidate(
                paths,
                candidate_id=second.candidate_id,
                actor="operator",
                reason="Later review",
            )

            with connect(paths, read_only=True) as connection:
                decisions = connection.execute(
                    "SELECT decision_type FROM nova_task_reconciliation_decisions ORDER BY created_at, decision_id"
                ).fetchall()
                audit_actions = connection.execute(
                    "SELECT action FROM nova_task_audit_log WHERE action IN ('reject_candidate', 'defer_candidate')"
                ).fetchall()
            pending = pending_candidate_count(paths)

        self.assertEqual([item["proposedTitle"] for item in listed], ["Review candidate two", "Review candidate one"])
        self.assertEqual(listed[0]["evidence"], ["line 2"])
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(deferred.status, "deferred")
        self.assertEqual([row["decision_type"] for row in decisions], ["reject", "defer"])
        self.assertEqual({row["action"] for row in audit_actions}, {"reject_candidate", "defer_candidate"})
        self.assertEqual(pending, 0)

    def test_candidate_merge_and_supersede_write_referenced_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            node = create_task_node(paths, node_id="NT-TARGET", title="Existing target", actor="operator")
            merged_candidate = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="Duplicate candidate",
                reason="Already represented",
                evidence=["line 1"],
                confidence="high",
            )
            superseded_candidate = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Old candidate",
                reason="Older proposal",
                evidence=["line 2"],
                confidence="medium",
            )
            replacement = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Replacement candidate",
                reason="Newer proposal",
                evidence=["line 3"],
                confidence="high",
            )

            merged = merge_task_candidate(
                paths,
                candidate_id=merged_candidate.candidate_id,
                actor="operator",
                target_node_id=node.node_id,
                reason="Folded into existing graph node",
            )
            superseded = supersede_task_candidate(
                paths,
                candidate_id=superseded_candidate.candidate_id,
                actor="operator",
                target_candidate_id=replacement.candidate_id,
                reason="Replaced by newer proposal",
            )

            with connect(paths, read_only=True) as connection:
                rows = {
                    row["candidate_id"]: row
                    for row in connection.execute(
                        "SELECT candidate_id, status, matched_node_id, metadata_json FROM nova_task_candidates"
                    ).fetchall()
                }
                decisions = connection.execute(
                    "SELECT candidate_id, decision_type, after_json FROM nova_task_reconciliation_decisions ORDER BY created_at, decision_id"
                ).fetchall()
                audit_actions = {
                    row["action"]
                    for row in connection.execute(
                        "SELECT action FROM nova_task_audit_log WHERE action IN ('merge_candidate', 'supersede_candidate')"
                    ).fetchall()
                }

        self.assertEqual(merged.status, "merged")
        self.assertEqual(superseded.status, "superseded")
        self.assertEqual(rows[merged_candidate.candidate_id]["status"], "merged")
        self.assertEqual(rows[merged_candidate.candidate_id]["matched_node_id"], "NT-TARGET")
        self.assertEqual(json.loads(rows[merged_candidate.candidate_id]["metadata_json"])["targetNodeId"], "NT-TARGET")
        self.assertEqual(rows[superseded_candidate.candidate_id]["status"], "superseded")
        self.assertEqual(
            json.loads(rows[superseded_candidate.candidate_id]["metadata_json"])["targetCandidateId"],
            replacement.candidate_id,
        )
        self.assertEqual([row["decision_type"] for row in decisions], ["merge", "supersede"])
        self.assertEqual(audit_actions, {"merge_candidate", "supersede_candidate"})


if __name__ == "__main__":
    unittest.main()
