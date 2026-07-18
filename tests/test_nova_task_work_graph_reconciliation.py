import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect
from data_foundation.nova_task import create_task_candidate, create_task_node
from data_foundation.nova_task_layers import (
    LAYER_EVIDENCE_LEDGER,
    LAYER_PLANNING_OVERLAY,
    LAYER_PROJECT_GRAPH,
    NODE_CREATED_BY_AGENT,
    NODE_MANAGED_BY_AGENT,
    ORIGIN_OBSERVED,
    ORIGIN_PLANNED,
    STATE_AUTHORITY_OBSERVED_SIGNAL,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
    TASK_NODE_STATUS_ACTIVE,
    TASK_NODE_STATUS_AUTOMATIC,
    TASK_NODE_STATUS_DONE,
    TASK_NODE_STATUS_PLANNED,
    TASK_NODE_STATUS_SETTLED,
    project_graph_metadata,
)
from data_foundation.nova_task_work_graph_reconciliation import (
    apply_candidate_actions_from_reconciliation,
    auto_confirm_reconciliation_candidates,
    build_reconciliation_prompt,
    noise_filtered_candidate_inbox,
    run_work_graph_reconciliation,
)
from data_foundation.paths import initialize_home
from data_foundation.settings import write_settings


class NovaTaskWorkGraphReconciliationTests(unittest.TestCase):
    def test_default_date_uses_canonical_business_day_before_four_am(self):
        def fake_sender(**kwargs):
            self.assertIn('date: "2026-06-30"', kwargs["prompt"])
            return """```yaml
nova_task:
  date: "2026-06-30"
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            write_settings({"general": {"timezone": "America/Los_Angeles"}}, paths)
            before_boundary = datetime(2026, 7, 1, 2, 0, tzinfo=ZoneInfo("America/Los_Angeles"))

            with (
                patch.dict("os.environ", {"TARGET_TIMEZONE": ""}),
                patch("data_foundation.time.business_now", return_value=before_boundary),
            ):
                result = run_work_graph_reconciliation(paths, sender=fake_sender)

        self.assertEqual(result.business_date, "2026-06-30")

    def test_noise_filtered_inbox_excludes_status_updates_and_volatile_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            node = create_task_node(paths, node_id="NT-ROOT", title="Root", actor="operator")
            create_task_candidate(
                paths,
                candidate_type="status_update",
                proposed_title="Root",
                matched_node_id=node.node_id,
                reason="LLM suggested completed",
                evidence=["status line"],
            )
            create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="actanara",
                reason="Project candidate",
                evidence=["project line"],
                metadata={"candidateKind": "project_anchor"},
            )

            inbox = noise_filtered_candidate_inbox(paths)

        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["candidateType"], "parent_task")
        self.assertEqual(inbox[0]["proposedTitle"], "actanara")
        self.assertNotIn("status", inbox[0])
        self.assertNotIn("createdAt", inbox[0])

    def test_apply_reconciliation_writes_review_only_hints(self):
        def fake_sender(**kwargs):
            self.assertIn("Candidate evidence set, noise-filtered", kwargs["prompt"])
            self.assertIn("Technical report for 2026-06-30", kwargs["prompt"])
            self.assertIn("high value technical chronicle", kwargs["prompt"])
            return """## Nova-Task Work Graph Reconciliation

```yaml
nova_task:
  date: "2026-06-30"
  candidate_parent_tasks:
    - proposed_title: "Infra operations"
      reason: "Missing Level 1 project anchor"
      evidence: ["candidate:NTC-open"]
  group_hints:
    - proposed_parent_title: "Dashboard Settings subsystem"
      child_task_ids: ["NT-CHILD-A", "NT-CHILD-B"]
      reason: "Same subsystem"
      evidence: ["candidate:NTC-a", "candidate:NTC-b"]
```
"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")
            workstream = create_task_node(
                paths,
                node_id="NT-WORK",
                title="Dashboard",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            create_task_node(paths, node_id="NT-CHILD-A", title="A", parent_node_id=workstream.node_id, actor="operator")
            create_task_node(paths, node_id="NT-CHILD-B", title="B", parent_node_id=workstream.node_id, actor="operator")
            create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="actanara system",
                reason="Existing pending item",
                evidence=["candidate source"],
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                technical_report="high value technical chronicle",
                direct_graph_apply=False,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                node_count = connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0]
                candidates = connection.execute(
                    "SELECT candidate_type, proposed_title, metadata_json FROM nova_task_candidates ORDER BY candidate_type, proposed_title"
                ).fetchall()
                event_sources = [
                    row["source_type"]
                    for row in connection.execute("SELECT DISTINCT source_type FROM nova_task_events ORDER BY source_type")
                ]
            artifact_exists = Path(result.artifact_path).exists()

        self.assertTrue(result.applied)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(node_count, 4)
        self.assertTrue(artifact_exists)
        types = [(row["candidate_type"], row["proposed_title"]) for row in candidates]
        self.assertIn(("parent_task", "Infra operations"), types)
        self.assertEqual(event_sources, ["nova_task_work_graph"])

    def test_auto_confirm_reconciliation_confirms_non_level_one_only(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_parent_tasks:
    - proposed_title: "Infra operations"
      suggested_node_type: track
      reason: "Missing project root"
      evidence: ["candidate:NTC-infra"]
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Settings subsystem"
      reason: "Durable subsystem"
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "Existing actanara root owns this subsystem."
        why_not_lower: "Durable subsystem, not a single deliverable."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["candidate:NTC-settings"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                auto_confirm_non_l1=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                nodes = connection.execute(
                    "SELECT node_type, title, parent_node_id, metadata_json FROM nova_task_nodes ORDER BY title"
                ).fetchall()
                candidates = connection.execute(
                    "SELECT candidate_type, proposed_title, status, metadata_json FROM nova_task_candidates ORDER BY proposed_title"
                ).fetchall()
                event_layers = [
                    json.loads(row["metadata_json"]).get("novaTaskLayer")
                    for row in connection.execute("SELECT metadata_json FROM nova_task_events ORDER BY event_type, summary")
                ]

        self.assertEqual(result.auto_confirmed_count, 1)
        self.assertEqual(result.project_graph_write_count, 1)
        self.assertEqual(result.planning_overlay_proposal_count, 1)
        self.assertGreaterEqual(result.evidence_ledger_event_count, 1)
        self.assertIn(("workstream", "Settings subsystem", "NT-ROOT"), [(row["node_type"], row["title"], row["parent_node_id"]) for row in nodes])
        self.assertIn(("parent_task", "Infra operations", "pending_review"), [(row["candidate_type"], row["proposed_title"], row["status"]) for row in candidates])
        self.assertNotIn(("subtask", "Settings subsystem", "confirmed"), [(row["candidate_type"], row["proposed_title"], row["status"]) for row in candidates])
        settings_node = [row for row in nodes if row["title"] == "Settings subsystem"][0]
        self.assertEqual(json.loads(settings_node["metadata_json"])["novaTaskLayer"], LAYER_PROJECT_GRAPH)
        parent_candidate = [row for row in candidates if row["proposed_title"] == "Infra operations"][0]
        self.assertEqual(json.loads(parent_candidate["metadata_json"])["novaTaskLayer"], LAYER_PLANNING_OVERLAY)
        self.assertIn(LAYER_EVIDENCE_LEDGER, event_layers)

    def test_llm_routing_hints_explain_rag_route_and_agent_orchestration_l1_review(self):
        def fake_sender(**kwargs):
            prompt = kwargs["prompt"]
            self.assertIn("Routing hint inference instructions", prompt)
            self.assertIn("Infer routing_hints inside this reconciliation pass", prompt)
            self.assertIn("not persistent authority", prompt)
            self.assertIn("If the only real parent is higher than the work's chosen level", prompt)
            self.assertIn("create every missing intermediate parent in order", prompt)
            self.assertIn("actanara embedding_server", prompt)
            self.assertIn("Mattermost DM", prompt)
            self.assertNotIn("nova-rag-subsystem", prompt)
            self.assertNotIn("agent-orchestration-l1", prompt)
            return """```yaml
nova_task:
  date: "2026-06-30"
  routing_hints:
    - hint_id: "RH-rag-embedding"
      boundary_type: l2_subsystem
      aliases: ["embedding_server", "provider", "1024", "vector"]
      target_node_id: "NT-RAG"
      target_level: 2
      confidence: high
      reason: "Embedding/provider/vector evidence routes to the existing RAG subsystem."
      evidence: ["technical:embedding_server provider 1024 vector", "graph:NT-RAG"]
      negative_rules:
        - "Do not route to the diary subsystem unless evidence is diary generation specific."
    - hint_id: "RH-agent-l1"
      boundary_type: l1_candidate
      aliases: ["agent-orchestration", "session_send", "Mattermost DM", "Lune", "共享工作区"]
      target_node_id: ""
      target_level: 1
      confidence: high
      reason: "Cross-agent collaboration evidence forms a project boundary."
      evidence: ["technical:session_send + Mattermost DM + Lune"]
      negative_rules:
        - "Do not bury cross-agent orchestration under a workspace solely because files live there."
  candidate_parent_tasks:
    - proposed_title: "多 Agent 协作 / agent-orchestration"
      suggested_node_type: track
      proposed_level: 1
      reason: "Cross-agent boundary spanning local session_send, Mattermost DM, shared workspace rules, and Lune validation."
      confidence: high
      evidence: ["routing_hint:RH-agent-l1", "technical:session_send + Mattermost DM + Lune"]
  candidate_subtasks:
    - proposed_parent_task_id: "NT-RAG"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "actanara embedding_server 架构与多 provider 全通"
      reason: "Embedding server/provider/vector-dimension evidence belongs under nova-RAG."
      confidence: high
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "Existing nova-RAG L2 already owns embedding and retrieval subsystem work."
        why_not_lower: "This is a durable deliverable, not a single implementation action."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["routing_hint:RH-rag-embedding", "technical:embedding_server provider 1024 vector"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-OPEN", title="actanara", node_type="track", actor="operator")
            create_task_node(
                paths,
                node_id="NT-RAG",
                title="nova-RAG 子系统",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )

            prompt, _ = build_reconciliation_prompt(
                paths,
                business_date=date(2026, 6, 30),
                technical_report=(
                    "actanara embedding_server 架构与多 provider 全通；"
                    "多 Agent 协作 / agent-orchestration 覆盖 session_send、Mattermost DM、共享工作区规范与 Lune 验证。"
                ),
            )
            self.assertIn("Routing hint inference instructions", prompt)
            self.assertIn("routing_hints", prompt)

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                technical_report=(
                    "actanara embedding_server 架构与多 provider 全通；"
                    "多 Agent 协作 / agent-orchestration 覆盖 session_send、Mattermost DM、共享工作区规范与 Lune 验证。"
                ),
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                rag_child = connection.execute(
                    """
                    SELECT node_type, parent_node_id, metadata_json
                    FROM nova_task_nodes
                    WHERE title = 'actanara embedding_server 架构与多 provider 全通'
                    """
                ).fetchone()
                l1_candidate = connection.execute(
                    """
                    SELECT candidate_type, status, metadata_json
                    FROM nova_task_candidates
                    WHERE proposed_title = '多 Agent 协作 / agent-orchestration'
                    """
                ).fetchone()
                routing_events = connection.execute(
                    """
                    SELECT event_type, metadata_json
                    FROM nova_task_events
                    WHERE json_extract(metadata_json, '$.hintEventType') = 'routing_hint'
                    ORDER BY summary
                    """
                ).fetchall()

        self.assertEqual(result.project_graph_write_count, 1)
        self.assertEqual(result.planning_overlay_proposal_count, 1)
        self.assertEqual(result.evidence_ledger_event_count, 4)
        self.assertIsNotNone(rag_child)
        self.assertEqual(rag_child["node_type"], "task")
        self.assertEqual(rag_child["parent_node_id"], "NT-RAG")
        self.assertEqual(json.loads(rag_child["metadata_json"])["origin"], ORIGIN_OBSERVED)
        self.assertIsNotNone(l1_candidate)
        self.assertEqual(l1_candidate["candidate_type"], "parent_task")
        self.assertEqual(l1_candidate["status"], "pending_review")
        self.assertEqual(json.loads(l1_candidate["metadata_json"])["reviewPolicy"], "manual_required_for_level_1")
        self.assertEqual(len(routing_events), 2)
        self.assertTrue(all(json.loads(row["metadata_json"])["nonAuthority"] for row in routing_events))

    def test_direct_reconciliation_applies_safe_reparent_hints_for_existing_non_l1_nodes(self):
        def fake_sender(**kwargs):
            prompt = kwargs["prompt"]
            self.assertIn("reparent_hints", prompt)
            self.assertIn("routing_hints", prompt)
            return """```yaml
nova_task:
  date: "2026-06-30"
  routing_hints:
    - hint_id: "RH-rag-reparent"
      boundary_type: l2_subsystem
      aliases: ["embedding_server", "provider", "1024"]
      target_node_id: "NT-RAG"
      target_level: 2
      confidence: high
      reason: "Embedding/provider node belongs under the existing RAG subsystem."
      evidence: ["technical:embedding_server provider 1024", "graph:NT-RAG"]
      negative_rules: []
  reparent_hints:
    - child_task_id: "NT-EMBED"
      proposed_parent_task_id: "NT-RAG"
      confidence: high
      reason: "embedding_server/provider work is under diary but belongs under nova-RAG."
      evidence: ["routing_hint:RH-rag-reparent", "technical:embedding_server provider 1024"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-OPEN", title="actanara", node_type="track", actor="operator")
            rag = create_task_node(
                paths,
                node_id="NT-RAG",
                title="nova-RAG子系统",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            diary = create_task_node(
                paths,
                node_id="NT-DIARY",
                title="jsonl-diary / actanara 日记系统",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-EMBED",
                title="Actanara embedding_server 多 provider 收敛",
                node_type="task",
                parent_node_id=diary.node_id,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                    created_by=NODE_CREATED_BY_AGENT,
                    managed_by=NODE_MANAGED_BY_AGENT,
                ),
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                technical_report="embedding_server provider 1024 vector work should route to nova-RAG.",
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                child = connection.execute(
                    "SELECT parent_node_id FROM nova_task_nodes WHERE node_id = 'NT-EMBED'"
                ).fetchone()
                audit = connection.execute(
                    """
                    SELECT actor, action, node_id, after_json
                    FROM nova_task_audit_log
                    WHERE action = 'direct_reparent_node'
                    """
                ).fetchone()

        self.assertEqual(result.project_graph_write_count, 1)
        self.assertEqual(result.evidence_ledger_event_count, 2)
        self.assertEqual(child["parent_node_id"], rag.node_id)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["actor"], "nova-task-work-graph")
        self.assertEqual(audit["node_id"], "NT-EMBED")
        self.assertEqual(json.loads(audit["after_json"])["parent_node_id"], "NT-RAG")

    def test_direct_reconciliation_rejects_reparent_hint_for_level_one_child(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  reparent_hints:
    - child_task_id: "NT-ROOT-A"
      proposed_parent_task_id: "NT-ROOT-B"
      confidence: high
      reason: "Invalid attempt to move L1."
      evidence: ["technical:bad"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT-A", title="Project A", node_type="track", actor="operator")
            create_task_node(paths, node_id="NT-ROOT-B", title="Project B", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                root = connection.execute(
                    "SELECT parent_node_id FROM nova_task_nodes WHERE node_id = 'NT-ROOT-A'"
                ).fetchone()
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_audit_log WHERE action = 'direct_reparent_node'"
                ).fetchone()[0]

        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertIsNone(root["parent_node_id"])
        self.assertEqual(audit_count, 0)

    def test_direct_reconciliation_rejects_reparent_hint_for_human_managed_child(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  reparent_hints:
    - child_task_id: "NT-HUMAN"
      proposed_parent_task_id: "NT-RAG"
      confidence: high
      reason: "LLM should not restructure a human-managed node."
      evidence: ["technical:bad"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-OPEN", title="actanara", node_type="track", actor="operator")
            rag = create_task_node(
                paths,
                node_id="NT-RAG",
                title="nova-RAG子系统",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            dashboard = create_task_node(
                paths,
                node_id="NT-DASH",
                title="Dashboard",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            del rag
            create_task_node(
                paths,
                node_id="NT-HUMAN",
                title="Human managed deliverable",
                node_type="task",
                parent_node_id=dashboard.node_id,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                child = connection.execute(
                    "SELECT parent_node_id FROM nova_task_nodes WHERE node_id = 'NT-HUMAN'"
                ).fetchone()
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_audit_log WHERE action = 'direct_reparent_node'"
                ).fetchone()[0]

        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(child["parent_node_id"], "NT-DASH")
        self.assertEqual(audit_count, 0)

    def test_direct_reconciliation_rejects_candidate_subtask_when_matched_existing_node_is_set(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-RAG"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "Duplicate embedding provider deliverable"
      reason: "Contradictory YAML says matched existing but also create new."
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "Existing subsystem parent."
        why_not_lower: "Deliverable scope."
        matched_existing_node_id: "NT-EXISTING"
        create_new_node: true
      evidence: ["technical:embedding provider"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = create_task_node(paths, node_id="NT-OPEN", title="actanara", node_type="track", actor="operator")
            rag = create_task_node(
                paths,
                node_id="NT-RAG",
                title="nova-RAG子系统",
                node_type="workstream",
                parent_node_id=root.node_id,
                actor="operator",
            )
            create_task_node(
                paths,
                node_id="NT-EXISTING",
                title="Existing embedding provider deliverable",
                node_type="task",
                parent_node_id=rag.node_id,
                actor="operator",
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                duplicate_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title = 'Duplicate embedding provider deliverable'"
                ).fetchone()[0]
                event = connection.execute(
                    "SELECT metadata_json FROM nova_task_events WHERE summary = 'Duplicate embedding provider deliverable'"
                ).fetchone()

        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(json.loads(event["metadata_json"])["levelValidation"], "rejected")

    def test_direct_reconciliation_rejects_candidate_subtask_without_level_decision(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Malformed workstream without level decision"
      reason: "LLM emitted a real parent but omitted required level_decision."
      evidence: ["technical:some durable work"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                created = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title = 'Malformed workstream without level decision'"
                ).fetchone()[0]
                event = connection.execute(
                    "SELECT metadata_json FROM nova_task_events WHERE summary = 'Malformed workstream without level decision'"
                ).fetchone()

        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(created, 0)
        self.assertEqual(json.loads(event["metadata_json"])["levelValidation"], "rejected")

    def test_direct_reconciliation_materializes_non_l1_topology_under_existing_l1(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_ref: "installer-l2"
      proposed_parent_task_id: "NT-ROOT"
      proposed_parent_ref: ""
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Installer v2 subsystem"
      reason: "Durable subsystem under an approved L1."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "The L1 project already exists."
        why_not_lower: "This spans multiple deliverables."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:installer v2"]
    - proposed_ref: "wizard-l3"
      proposed_parent_task_id: ""
      proposed_parent_ref: "installer-l2"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "Installer v2 wizard"
      reason: "Deliverable under installer subsystem."
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "It is one deliverable under installer v2."
        why_not_lower: "It spans UI flags, TTY, and tests."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:wizard"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    """
                    SELECT node_id, parent_node_id, node_type, title, metadata_json
                    FROM nova_task_nodes
                    WHERE title IN ('Installer v2 subsystem', 'Installer v2 wizard')
                    ORDER BY title
                    """
                ).fetchall()
                events = connection.execute(
                    """
                    SELECT summary, matched_node_id, json_extract(metadata_json, '$.levelValidation')
                    FROM nova_task_events
                    WHERE business_date = '2026-06-30'
                    ORDER BY summary
                    """
                ).fetchall()

        by_title = {row["title"]: row for row in rows}
        self.assertEqual(result.project_graph_write_count, 2)
        self.assertEqual(result.evidence_ledger_event_count, 2)
        self.assertEqual(by_title["Installer v2 subsystem"]["parent_node_id"], "NT-ROOT")
        self.assertEqual(by_title["Installer v2 subsystem"]["node_type"], "workstream")
        self.assertEqual(by_title["Installer v2 wizard"]["parent_node_id"], by_title["Installer v2 subsystem"]["node_id"])
        self.assertEqual(by_title["Installer v2 wizard"]["node_type"], "task")
        self.assertEqual([row[2] for row in events], ["accepted", "accepted"])

    def test_direct_reconciliation_rejects_ref_chain_without_existing_l1_root(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_ref: "missing-l2"
      proposed_parent_task_id: "PENDING_L1"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Should wait for L1"
      reason: "No approved L1 exists."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "Would be L2 under pending L1."
        why_not_lower: "Subsystem."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:pending l1"]
    - proposed_parent_ref: "missing-l2"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "Child should also wait"
      reason: "Parent ref is blocked."
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "Child deliverable."
        why_not_lower: "Not an action."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:child"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                created = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title IN ('Should wait for L1', 'Child should also wait')"
                ).fetchone()[0]
                reasons = [
                    row[0]
                    for row in connection.execute(
                        "SELECT json_extract(metadata_json, '$.levelValidationReason') FROM nova_task_events ORDER BY summary"
                    )
                ]

        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 2)
        self.assertEqual(created, 0)
        self.assertEqual(sorted(reasons), ["blocked_parent_ref", "invalid_non_nt_parent_ref"])

    def test_completed_observed_child_promotes_planned_l1_to_active(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_ref: "installer-l2"
      proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Installer v2 subsystem"
      reason: "Durable installer workstream."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "Approved L1 exists."
        why_not_lower: "Subsystem spans multiple deliverables."
        matched_existing_node_id: ""
        create_new_node: true
      status_decision:
        target_status: active
        source_type: observed_progress
        reason: "Installer workstream is active."
      evidence: ["technical:installer v2 active work"]
    - proposed_parent_ref: "installer-l2"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "Installer v2 wizard completed validation"
      reason: "Wizard implementation finished and tests passed."
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "Deliverable under installer."
        why_not_lower: "More than a single action."
        matched_existing_node_id: ""
        create_new_node: true
      status_decision:
        target_status: completed
        source_type: observed_completion
        reason: "Implementation finished and validation passed."
      evidence: ["technical:wizard completed", "technical:tests passed"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(
                paths,
                node_id="NT-ROOT",
                title="actanara",
                node_type="track",
                status=TASK_NODE_STATUS_PLANNED,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                root = connection.execute(
                    "SELECT status, metadata_json FROM nova_task_nodes WHERE node_id = 'NT-ROOT'"
                ).fetchone()
                wizard = connection.execute(
                    "SELECT status, progress, metadata_json FROM nova_task_nodes WHERE title = 'Installer v2 wizard completed validation'"
                ).fetchone()
                audit = connection.execute(
                    """
                    SELECT action, node_id, after_json
                    FROM nova_task_audit_log
                    WHERE action = 'auto_promote_planned_l1_to_active'
                    """
                ).fetchone()

        self.assertEqual(result.project_graph_write_count, 3)
        self.assertEqual(root["status"], TASK_NODE_STATUS_ACTIVE)
        self.assertEqual(
            json.loads(root["metadata_json"])["statusSignal"]["reason"],
            "planned_ancestor_has_observed_descendant",
        )
        self.assertEqual(wizard["status"], TASK_NODE_STATUS_SETTLED)
        self.assertEqual(wizard["progress"], 100)
        self.assertEqual(json.loads(wizard["metadata_json"])["statusDecision"]["target_status"], "completed")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["node_id"], "NT-ROOT")
        self.assertEqual(json.loads(audit["after_json"])["status"], TASK_NODE_STATUS_ACTIVE)

    def test_future_intent_child_can_remain_planned_without_promoting_l1(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Future release train"
      reason: "Explicit future roadmap item."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "Approved L1 exists."
        why_not_lower: "Release train spans multiple future deliverables."
        matched_existing_node_id: ""
        create_new_node: true
      status_decision:
        target_status: planned
        source_type: future_plan
        reason: "Planned next-step roadmap item, not observed implementation."
      evidence: ["technical:下一步计划 release train"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(
                paths,
                node_id="NT-ROOT",
                title="actanara",
                node_type="track",
                status=TASK_NODE_STATUS_PLANNED,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                root_status = connection.execute(
                    "SELECT status FROM nova_task_nodes WHERE node_id = 'NT-ROOT'"
                ).fetchone()[0]
                child_status = connection.execute(
                    "SELECT status FROM nova_task_nodes WHERE title = 'Future release train'"
                ).fetchone()[0]
                audit_count = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_audit_log WHERE action = 'auto_promote_planned_l1_to_active'"
                ).fetchone()[0]

        self.assertEqual(result.project_graph_write_count, 2)
        self.assertEqual(root_status, TASK_NODE_STATUS_ACTIVE)
        self.assertEqual(child_status, TASK_NODE_STATUS_AUTOMATIC)
        self.assertEqual(audit_count, 1)

    def test_active_observed_child_promotes_planned_l2_to_active(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-L2"
      suggested_node_type: deliverable
      proposed_level: 3
      proposed_title: "Settings audit deliverable"
      reason: "Observed settings audit work started under planned workstream."
      level_decision:
        chosen_level: 3
        layer: project_graph
        parent_level: 2
        why_not_higher: "Existing L2 owns settings contract work."
        why_not_lower: "Deliverable, not one implementation task."
        matched_existing_node_id: ""
        create_new_node: true
      status_decision:
        target_status: active
        source_type: observed_progress
        reason: "Implementation started."
      evidence: ["technical:settings audit active"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-L1", title="actanara", node_type="track", actor="operator")
            create_task_node(
                paths,
                node_id="NT-L2",
                title="Runtime Settings",
                node_type="workstream",
                parent_node_id="NT-L1",
                status=TASK_NODE_STATUS_PLANNED,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                l2 = connection.execute(
                    "SELECT status, metadata_json FROM nova_task_nodes WHERE node_id = 'NT-L2'"
                ).fetchone()
                child = connection.execute(
                    "SELECT parent_node_id, status FROM nova_task_nodes WHERE title = 'Settings audit deliverable'"
                ).fetchone()
                audit = connection.execute(
                    """
                    SELECT action, node_id
                    FROM nova_task_audit_log
                    WHERE action = 'auto_promote_planned_ancestor_to_active'
                    """
                ).fetchone()

        self.assertEqual(result.project_graph_write_count, 2)
        self.assertEqual(l2["status"], TASK_NODE_STATUS_ACTIVE)
        self.assertEqual(
            json.loads(l2["metadata_json"])["statusSignal"]["reason"],
            "planned_ancestor_has_observed_descendant",
        )
        self.assertEqual(child["parent_node_id"], "NT-L2")
        self.assertEqual(child["status"], TASK_NODE_STATUS_AUTOMATIC)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["node_id"], "NT-L2")

    def test_reconciliation_events_are_idempotent_across_artifact_paths(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  matched_tasks:
    - task_id: "NT-ROOT"
      confidence: high
      event_type: progress
      summary: "Same observed progress"
      evidence: ["technical:same evidence"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            first = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )
            second = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                event_count = connection.execute("SELECT COUNT(*) FROM nova_task_events").fetchone()[0]
            first_summary_exists = Path(first.summary_path).exists()
            second_summary_exists = Path(second.summary_path).exists()

        self.assertEqual(first.evidence_ledger_event_count, 1)
        self.assertEqual(second.evidence_ledger_event_count, 0)
        self.assertEqual(event_count, 1)
        self.assertTrue(first_summary_exists)
        self.assertTrue(second_summary_exists)

    def test_candidate_actions_attach_reject_defer_merge_and_supersede_existing_pending_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")
            attached = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="actanara duplicate",
                reason="Duplicate project root",
                evidence=["candidate attach"],
            )
            rejected = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="One-off command",
                reason="Tiny work",
                evidence=["candidate reject"],
            )
            deferred = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="Unclear future stream",
                reason="Plausible but unstable",
                evidence=["candidate defer"],
            )
            merged = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="Duplicate implementation detail",
                reason="Already represented by graph node",
                evidence=["candidate merge"],
            )
            superseded = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Old project proposal",
                reason="Older proposal",
                evidence=["candidate supersede"],
            )
            replacement = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="Replacement project proposal",
                reason="Newer proposal",
                evidence=["candidate replacement"],
            )

            counts = apply_candidate_actions_from_reconciliation(
                paths,
                f"""```yaml
nova_task:
  date: "2026-06-30"
  candidate_actions:
    - candidate_id: "{attached.candidate_id}"
      action: attach_existing
      target_node_id: "NT-ROOT"
      reason: "Already represented by actanara root."
      confidence: high
      evidence: ["candidate:{attached.candidate_id}"]
    - candidate_id: "{rejected.candidate_id}"
      action: reject
      reason: "Tiny one-off command."
      confidence: high
      evidence: ["candidate:{rejected.candidate_id}"]
    - candidate_id: "{deferred.candidate_id}"
      action: defer
      reason: "Needs more evidence."
      confidence: medium
      evidence: ["candidate:{deferred.candidate_id}"]
    - candidate_id: "{merged.candidate_id}"
      action: merge
      target_node_id: "NT-ROOT"
      reason: "Folded into existing graph node."
      confidence: high
      evidence: ["candidate:{merged.candidate_id}"]
    - candidate_id: "{superseded.candidate_id}"
      action: supersede
      target_candidate_id: "{replacement.candidate_id}"
      reason: "Replaced by newer proposal."
      confidence: high
      evidence: ["candidate:{superseded.candidate_id}"]
```""",
            )

            with connect(paths, read_only=True) as connection:
                rows = {
                    row["candidate_id"]: dict(row)
                    for row in connection.execute(
                        "SELECT candidate_id, status, matched_node_id, metadata_json FROM nova_task_candidates"
                    )
                }
                decisions = [
                    (row["candidate_id"], row["decision_type"])
                    for row in connection.execute(
                        "SELECT candidate_id, decision_type FROM nova_task_reconciliation_decisions ORDER BY candidate_id"
                    )
                ]

        self.assertEqual(counts, {"attached": 1, "rejected": 1, "deferred": 1, "merged": 1, "superseded": 1})
        self.assertEqual(rows[attached.candidate_id]["status"], "confirmed")
        self.assertEqual(rows[attached.candidate_id]["matched_node_id"], "NT-ROOT")
        self.assertEqual(json.loads(rows[attached.candidate_id]["metadata_json"])["attachedToNodeId"], "NT-ROOT")
        self.assertEqual(rows[rejected.candidate_id]["status"], "rejected")
        self.assertEqual(rows[deferred.candidate_id]["status"], "deferred")
        self.assertEqual(rows[merged.candidate_id]["status"], "merged")
        self.assertEqual(rows[merged.candidate_id]["matched_node_id"], "NT-ROOT")
        self.assertEqual(json.loads(rows[merged.candidate_id]["metadata_json"])["targetNodeId"], "NT-ROOT")
        self.assertEqual(rows[superseded.candidate_id]["status"], "superseded")
        self.assertEqual(
            json.loads(rows[superseded.candidate_id]["metadata_json"])["targetCandidateId"],
            replacement.candidate_id,
        )
        self.assertEqual(
            sorted(decisions),
            sorted(
                [
                    (attached.candidate_id, "attached"),
                    (rejected.candidate_id, "reject"),
                    (deferred.candidate_id, "defer"),
                    (merged.candidate_id, "merge"),
                    (superseded.candidate_id, "supersede"),
                ]
            ),
        )

    def test_direct_reconciliation_status_updates_only_planned_state_machine_nodes(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  status_signals:
    - task_id: "NT-OBS"
      confidence: high
      target_status: completed
      status_reason: "Observed work finished today."
      status_tags: []
      evidence: ["technical:observed"]
    - task_id: "NT-PLAN"
      confidence: high
      target_status: completed
      status_reason: "Planned work completed."
      status_tags: []
      evidence: ["technical:planned"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(
                paths,
                node_id="NT-OBS",
                title="Observed node",
                parent_node_id=None,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                ),
            )
            create_task_node(
                paths,
                node_id="NT-PLAN",
                title="Planned node",
                parent_node_id=None,
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )
            child_observed = create_task_node(
                paths,
                node_id="NT-OBS-CHILD",
                title="Observed child",
                parent_node_id="NT-OBS",
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                ),
            )
            child_planned = create_task_node(
                paths,
                node_id="NT-PLAN-CHILD",
                title="Planned child",
                parent_node_id="NT-PLAN",
                actor="operator",
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                ),
            )

            def sender_for_children(**kwargs):
                return f"""```yaml
nova_task:
  date: "2026-06-30"
  status_signals:
    - task_id: "{child_observed.node_id}"
      confidence: high
      target_status: completed
      status_reason: "Observed child completed."
      status_tags: []
      evidence: ["technical:observed"]
    - task_id: "{child_planned.node_id}"
      confidence: high
      target_status: completed
      status_reason: "Planned child completed."
      status_tags: []
      evidence: ["technical:planned"]
```"""

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=sender_for_children,
            )

            with connect(paths, read_only=True) as connection:
                statuses = {
                    row["node_id"]: row["status"]
                    for row in connection.execute(
                        "SELECT node_id, status FROM nova_task_nodes WHERE node_id IN ('NT-OBS-CHILD', 'NT-PLAN-CHILD')"
                    )
                }
                event_count = connection.execute("SELECT COUNT(*) FROM nova_task_events").fetchone()[0]

        self.assertEqual(statuses["NT-OBS-CHILD"], "automatic")
        self.assertEqual(statuses["NT-PLAN-CHILD"], TASK_NODE_STATUS_DONE)
        self.assertEqual(result.project_graph_write_count, 1)
        self.assertEqual(event_count, 2)

    def test_run_reconciliation_reports_candidate_action_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")
            candidate = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="actanara duplicate",
                reason="Duplicate project root",
                evidence=["candidate attach"],
            )

            def fake_sender(**kwargs):
                return f"""```yaml
nova_task:
  date: "2026-06-30"
  candidate_actions:
    - candidate_id: "{candidate.candidate_id}"
      action: attach_existing
      target_node_id: "NT-ROOT"
      reason: "Already represented by actanara."
      confidence: high
      evidence: ["candidate:{candidate.candidate_id}"]
```"""

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

        self.assertEqual(result.action_count, 1)
        self.assertEqual(result.attached_count, 1)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.deferred_count, 0)
        self.assertEqual(result.merged_count, 0)
        self.assertEqual(result.superseded_count, 0)
        self.assertEqual(result.pending_after, 0)

    def test_direct_reconciliation_rejects_candidate_subtask_level_parent_mismatch(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-L2"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Misparented subsystem"
      reason: "L2 cannot be created under L2."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 2
        why_not_higher: "Already has project root."
        why_not_lower: "Claims durable subsystem."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["candidate:NTC-bad"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-L1", title="actanara", node_type="track", actor="operator")
            create_task_node(
                paths,
                node_id="NT-L2",
                title="Nova-Task",
                node_type="workstream",
                parent_node_id="NT-L1",
                actor="operator",
            )

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                created = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title = 'Misparented subsystem'"
                ).fetchone()[0]
                events = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_events WHERE summary = 'Misparented subsystem'"
                ).fetchone()[0]

        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(created, 0)
        self.assertEqual(events, 1)

    def test_direct_reconciliation_rejects_l1_candidate_subtask_from_daily_evidence(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: track
      proposed_level: 1
      proposed_title: "New Product Root"
      reason: "Daily evidence mentioned a new product root."
      level_decision:
        chosen_level: 1
        layer: project_graph
        parent_level: 0
        why_not_higher: "Claims to be project root."
        why_not_lower: "Not a subsystem."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:daily mention only"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                created = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title = 'New Product Root'"
                ).fetchone()[0]
                candidates = connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0]
                event = connection.execute(
                    "SELECT metadata_json FROM nova_task_events WHERE summary = 'New Product Root'"
                ).fetchone()

        metadata = json.loads(event["metadata_json"])
        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.planning_overlay_proposal_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(created, 0)
        self.assertEqual(candidates, 0)
        self.assertEqual(metadata["levelValidation"], "rejected")
        self.assertEqual(metadata["levelValidationReason"], "level_1_candidate_subtasks_are_forbidden")

    def test_direct_reconciliation_rejects_subtask_with_missing_parent_instead_of_reattaching(self):
        def fake_sender(**kwargs):
            return """```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-MISSING"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Orphan subsystem"
      reason: "Parent is not present in the active graph."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "Should be under a real L1."
        why_not_lower: "Subsystem claim."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["technical:orphan evidence"]
```"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                created = connection.execute(
                    "SELECT COUNT(*) FROM nova_task_nodes WHERE title = 'Orphan subsystem'"
                ).fetchone()[0]
                event = connection.execute(
                    "SELECT matched_node_id, metadata_json FROM nova_task_events WHERE summary = 'Orphan subsystem'"
                ).fetchone()

        metadata = json.loads(event["metadata_json"])
        self.assertEqual(result.project_graph_write_count, 0)
        self.assertEqual(result.evidence_ledger_event_count, 1)
        self.assertEqual(created, 0)
        self.assertIsNone(event["matched_node_id"])
        self.assertEqual(metadata["levelValidation"], "rejected")
        self.assertEqual(metadata["levelValidationReason"], "missing_real_parent_node")

    def test_actions_only_apply_skips_new_candidate_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            create_task_node(paths, node_id="NT-ROOT", title="actanara", node_type="track", actor="operator")
            candidate = create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="actanara duplicate",
                reason="Duplicate project root",
                evidence=["candidate attach"],
            )

            def fake_sender(**kwargs):
                return f"""```yaml
nova_task:
  date: "2026-06-30"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "Should not be ingested"
      reason: "actions-only skips this"
      evidence: ["candidate:{candidate.candidate_id}"]
  candidate_actions:
    - candidate_id: "{candidate.candidate_id}"
      action: attach_existing
      target_node_id: "NT-ROOT"
      reason: "Already represented by actanara."
      confidence: high
      evidence: ["candidate:{candidate.candidate_id}"]
```"""

            result = run_work_graph_reconciliation(
                paths,
                business_date=date(2026, 6, 30),
                apply=True,
                actions_only=True,
                auto_confirm_non_l1=True,
                sender=fake_sender,
            )

            with connect(paths, read_only=True) as connection:
                candidates = connection.execute(
                    "SELECT proposed_title, status FROM nova_task_candidates ORDER BY proposed_title"
                ).fetchall()

        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.auto_confirmed_count, 0)
        self.assertEqual(result.action_count, 1)
        self.assertEqual([(row["proposed_title"], row["status"]) for row in candidates], [("actanara duplicate", "confirmed")])


if __name__ == "__main__":
    unittest.main()
