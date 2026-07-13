import asyncio
import importlib.machinery
import inspect
import json
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


class _RouterStub:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def patch(self, *_args, **_kwargs):
        return lambda function: function

    def post(self, *_args, **_kwargs):
        return lambda function: function

    def put(self, *_args, **_kwargs):
        return lambda function: function


class _BackgroundTasksStub:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))

    async def __call__(self):
        for func, args, kwargs in self.tasks:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                await result


class _JSONResponseStub(dict):
    def __init__(self, content=None, status_code=200, **kwargs):
        super().__init__(content or {})
        self.status_code = status_code
        self.kwargs = kwargs
        self.body = json.dumps(content or {}).encode("utf-8")


fastapi_stub = types.ModuleType("fastapi")
fastapi_stub.__spec__ = importlib.machinery.ModuleSpec("fastapi", loader=None)
fastapi_stub.APIRouter = lambda: _RouterStub()
fastapi_stub.BackgroundTasks = _BackgroundTasksStub
fastapi_stub.Request = object
responses_stub = types.ModuleType("fastapi.responses")
responses_stub.__spec__ = importlib.machinery.ModuleSpec("fastapi.responses", loader=None)
responses_stub.JSONResponse = _JSONResponseStub
responses_stub.StreamingResponse = _JSONResponseStub
sys.modules.setdefault("fastapi", fastapi_stub)
sys.modules.setdefault("fastapi.responses", responses_stub)

from app.routers import tasks as tasks_router
from data_foundation.nova_task import create_task_candidate, create_task_node, ingest_nova_task_evidence
from data_foundation.nova_task_layers import (
    ORIGIN_OBSERVED,
    STATE_AUTHORITY_OBSERVED_SIGNAL,
    project_graph_metadata,
)
from data_foundation.nova_task_work_graph_reconciliation import apply_reconciliation_graph_direct
from data_foundation.paths import initialize_home


class DashboardNovaTaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.nova_task_enabled = patch.object(tasks_router.foundation, "nova_task_enabled", return_value=True)
        self.nova_task_enabled.start()
        self.addCleanup(self.nova_task_enabled.stop)

    def test_candidate_status_endpoint_delegates_to_review_service(self):
        with patch.object(
            tasks_router.nova_task_review,
            "candidate_status",
            return_value={
                "pendingReviewCount": 2,
                "hasPendingReview": True,
                "pendingCount": 2,
                "hasPending": True,
            },
        ) as status:
            result = asyncio.run(tasks_router.api_task_candidate_status())

        status.assert_called_once_with()
        self.assertEqual(
            result,
            {
                "pendingReviewCount": 2,
                "hasPendingReview": True,
                "pendingCount": 2,
                "hasPending": True,
            },
        )

    def test_candidate_list_endpoint_delegates_status_and_limit(self):
        expected = {
            "candidates": [{"candidateId": "NTC-1"}],
            "count": 1,
            "pendingReviewCount": 1,
            "hasPendingReview": True,
            "pendingCount": 1,
            "hasPending": True,
        }
        with patch.object(tasks_router.nova_task_review, "candidates", return_value=expected) as candidates:
            result = asyncio.run(tasks_router.api_task_candidates(status="pending_review", limit=20))

        candidates.assert_called_once_with(status="pending_review", limit=20)
        self.assertEqual(result, expected)

    def test_l1_review_alias_endpoints_delegate_to_review_service(self):
        status_payload = {"l1ReviewCount": 1, "hasL1Review": True}
        items_payload = {"items": [{"candidateId": "NTC-1"}], "count": 1}
        with (
            patch.object(tasks_router.nova_task_review, "l1_review_status", return_value=status_payload) as status,
            patch.object(tasks_router.nova_task_review, "l1_review_items", return_value=items_payload) as items,
        ):
            status_result = asyncio.run(tasks_router.api_task_l1_review_status())
            items_result = asyncio.run(tasks_router.api_task_l1_review_items(status="pending_review", limit=20))

        status.assert_called_once_with()
        items.assert_called_once_with(status="pending_review", limit=20)
        self.assertEqual(status_result, status_payload)
        self.assertEqual(items_result, items_payload)

    def test_review_service_exposes_candidate_and_anchor_display_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            create_task_candidate(
                paths,
                candidate_type="parent_task",
                proposed_title="TokenClock",
                reason="Workspace anchor",
                evidence=["workspace:/tmp/TokenClock"],
                confidence="high",
                metadata={
                    "source": "workspace-attribution",
                    "candidateKind": "project_anchor",
                    "workspace": {"rootPath": "/tmp/TokenClock", "displayName": "TokenClock"},
                },
            )
            create_task_node(
                paths,
                node_id="NT-TOKENCLOCK",
                title="TokenClock",
                node_type="track",
                status="active",
                actor="test",
                metadata=project_graph_metadata(
                    origin=ORIGIN_OBSERVED,
                    state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                    workspace={"rootPath": "/tmp/TokenClock", "displayName": "TokenClock"},
                ),
            )

            with patch.object(tasks_router.nova_task_review, "_dashboard_paths", return_value=paths):
                candidates = tasks_router.nova_task_review.candidates(status="pending_review", limit=20)
                tree = tasks_router.nova_task_review.tree()

        item = candidates["candidates"][0]
        node = tree["nodes"][0]
        self.assertEqual(item["reviewStatus"], "pending_review")
        self.assertEqual(item["candidateKind"], "project_anchor")
        self.assertEqual(item["workspaceRootPath"], "/tmp/TokenClock")
        self.assertEqual(node["anchorProfile"], "observed_path_backed_l1")
        self.assertEqual(node["workspaceRootPath"], "/tmp/TokenClock")

    def test_review_service_rejects_non_l1_candidate_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            candidate = create_task_candidate(
                paths,
                candidate_type="subtask",
                proposed_title="Historical non-L1 candidate",
                reason="legacy",
                evidence=["legacy"],
            )

            with patch.object(tasks_router.nova_task_review, "_dashboard_paths", return_value=paths):
                with self.assertRaisesRegex(ValueError, "not Level 1"):
                    tasks_router.nova_task_review.reject_candidate(candidate.candidate_id, reason="No longer reviewed")

    def test_review_service_exposes_recent_direct_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            create_task_node(paths, node_id="NT-ROOT", title="Root", node_type="track", actor="operator")
            create_task_node(
                paths,
                node_id="NT-L2",
                title="Existing L2",
                node_type="workstream",
                parent_node_id="NT-ROOT",
                actor="operator",
            )
            ingest_nova_task_evidence(
                paths,
                markdown="""```yaml
nova_task:
  date: "2026-06-07"
  candidate_subtasks:
    - proposed_parent_task_id: "NT-ROOT"
      proposed_title: "Direct subsystem"
      suggested_node_type: workstream
      reason: "Direct write"
      evidence: ["line"]
```""",
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "technical.md",
            )
            apply_reconciliation_graph_direct(
                paths,
                markdown="""```yaml
nova_task:
  date: "2026-06-07"
  routing_hints:
    - hint_id: "RH-direct-write"
      boundary_type: l2_subsystem
      aliases: ["direct", "write"]
      target_node_id: "NT-L2"
      target_level: 2
      confidence: high
      reason: "Direct write evidence routes under the existing L2."
      evidence: ["line"]
      negative_rules:
        - "Do not create a new L1 for this evidence."
  candidate_subtasks:
    - proposed_parent_task_id: "NT-L2"
      proposed_title: "Skipped level task"
      suggested_node_type: subtask
      proposed_level: 4
      reason: "Missing L3 parent."
      level_decision:
        chosen_level: 4
        layer: project_graph
        parent_level: 2
        why_not_higher: "Implementation task."
        why_not_lower: "Not an action."
        matched_existing_node_id: ""
        create_new_node: true
      evidence: ["line"]
```""",
                business_date=date(2026, 6, 7),
                source_path=Path(tmp) / "recon.md",
            )

            with patch.object(tasks_router.nova_task_review, "_dashboard_paths", return_value=paths):
                recent = tasks_router.nova_task_review.recent_direct_writes(limit=5)

        self.assertGreaterEqual(recent["auditCount"], 1)
        self.assertGreaterEqual(recent["eventCount"], 1)
        self.assertGreaterEqual(recent["deferredByGuardCount"], 1)
        self.assertIn("create_node", {item["action"] for item in recent["audits"]})
        self.assertIn("candidate_subtask", {item["eventType"] for item in recent["events"]})
        self.assertIn("level_contract_rejected", {item["reason"] for item in recent["deferredByGuard"]})
        self.assertGreaterEqual(recent["routingHintCount"], 1)
        routing_hint = recent["routingHints"][0]
        self.assertEqual(routing_hint["hintId"], "RH-direct-write")
        self.assertEqual(routing_hint["targetNodeId"], "NT-L2")
        self.assertTrue(routing_hint["nonAuthority"])
        self.assertEqual(routing_hint["scope"], "current_reconciliation_only")

    def test_planning_import_endpoint_delegates_payload(self):
        expected = {"status": "ok", "applied": True}
        with patch.object(tasks_router.nova_task_review, "planning_import", return_value=expected) as planning_import:
            result = asyncio.run(
                tasks_router.api_task_planning_import(
                    {"title": "Roadmap.md", "content": "# Roadmap", "apply": True}
                )
            )

        planning_import.assert_called_once_with(title="Roadmap.md", content="# Roadmap", apply=True)
        self.assertEqual(result, expected)

    def test_planning_import_apply_endpoint_delegates_artifact(self):
        expected = {"status": "ok", "applied": True}
        with patch.object(tasks_router.nova_task_review, "apply_planning_import", return_value=expected) as apply_import:
            result = asyncio.run(
                tasks_router.api_apply_task_planning_import({"artifactPath": "/tmp/import.md"})
            )

        apply_import.assert_called_once_with(artifact_path="/tmp/import.md")
        self.assertEqual(result, expected)

    def test_task_nodes_endpoint_delegates_to_review_service(self):
        expected = {"nodes": [{"nodeId": "NT-1"}], "count": 1}
        with patch.object(tasks_router.nova_task_review, "nodes", return_value=expected) as nodes:
            result = asyncio.run(tasks_router.api_task_nodes())

        nodes.assert_called_once_with()
        self.assertEqual(result, expected)

    def test_task_tree_endpoint_delegates_to_review_service(self):
        expected = {"roots": [{"nodeId": "NT-1", "children": []}], "nodes": [], "count": 1}
        with patch.object(tasks_router.nova_task_review, "tree", return_value=expected) as tree:
            result = asyncio.run(tasks_router.api_task_tree())

        tree.assert_called_once_with()
        self.assertEqual(result, expected)

    def test_recent_direct_writes_endpoint_delegates_limit(self):
        expected = {"audits": [{"auditId": "NTA-1"}], "events": [], "auditCount": 1, "eventCount": 0}
        with patch.object(tasks_router.nova_task_review, "recent_direct_writes", return_value=expected) as recent:
            result = asyncio.run(tasks_router.api_recent_task_direct_writes(limit=10))

        recent.assert_called_once_with(limit=10)
        self.assertEqual(result, expected)

    def test_task_node_update_endpoint_delegates_payload(self):
        expected = {"status": "ok"}
        with patch.object(tasks_router.nova_task_review, "update_node", return_value=expected) as update:
            result = asyncio.run(
                tasks_router.api_update_task_node(
                    "NT-1",
                    {
                        "title": "Renamed",
                        "status": "done",
                        "parentNodeId": "NT-P",
                        "progress": 100,
                        "completionMethod": "Done by dashboard",
                        "managedBy": "human",
                    },
                )
            )

        update.assert_called_once_with(
            "NT-1",
            title="Renamed",
            status="done",
            parent_node_id="NT-P",
            progress=100,
            completion_method="Done by dashboard",
            managed_by="human",
        )
        self.assertEqual(result, expected)

    def test_task_node_create_endpoint_delegates_payload(self):
        expected = {"status": "ok"}
        with patch.object(tasks_router.nova_task_review, "create_node", return_value=expected) as create:
            result = asyncio.run(
                tasks_router.api_create_task_node(
                    {
                        "title": "New task",
                        "status": "planned",
                        "parentNodeId": "NT-P",
                        "nodeType": "subtask",
                    }
                )
            )

        create.assert_called_once_with(
            title="New task",
            status="planned",
            parent_node_id="NT-P",
            node_type="subtask",
        )
        self.assertEqual(result, expected)

    def test_candidate_decision_endpoints_delegate_payload(self):
        with (
            patch.object(tasks_router.nova_task_review, "confirm_candidate", return_value={"status": "ok"}) as confirm,
            patch.object(tasks_router.nova_task_review, "reject_candidate", return_value={"status": "ok"}) as reject,
            patch.object(tasks_router.nova_task_review, "defer_candidate", return_value={"status": "ok"}) as defer,
            patch.object(tasks_router.nova_task_review, "merge_candidate", return_value={"status": "ok"}) as merge,
            patch.object(tasks_router.nova_task_review, "supersede_candidate", return_value={"status": "ok"}) as supersede,
            patch.object(tasks_router.nova_task_review, "delete_candidate", return_value={"status": "ok"}) as delete,
        ):
            self.assertEqual(
                asyncio.run(
                    tasks_router.api_confirm_task_candidate(
                        "NTC-1",
                        {"title": "Renamed", "reason": "Approved", "parentNodeId": "NT-P", "nodeType": "subtask"},
                    )
                ),
                {"status": "ok"},
            )
            self.assertEqual(
                asyncio.run(tasks_router.api_reject_task_candidate("NTC-2", {"reason": "Rejected"})),
                {"status": "ok"},
            )
            self.assertEqual(
                asyncio.run(tasks_router.api_defer_task_candidate("NTC-3", {"reason": "Later"})),
                {"status": "ok"},
            )
            self.assertEqual(
                asyncio.run(
                    tasks_router.api_merge_task_candidate(
                        "NTC-4",
                        {"reason": "Duplicate", "targetCandidateId": "NTC-5", "targetNodeId": "NT-1"},
                    )
                ),
                {"status": "ok"},
            )
            self.assertEqual(
                asyncio.run(
                    tasks_router.api_supersede_task_candidate(
                        "NTC-6",
                        {"reason": "Replaced", "targetCandidateId": "NTC-7", "targetNodeId": "NT-2"},
                    )
                ),
                {"status": "ok"},
            )
            self.assertEqual(
                asyncio.run(tasks_router.api_delete_task_candidate("NTC-8", {"reason": "Delete"})),
                {"status": "ok"},
            )

        confirm.assert_called_once_with(
            "NTC-1",
            title="Renamed",
            reason="Approved",
            parent_node_id="NT-P",
            node_type="subtask",
        )
        reject.assert_called_once_with("NTC-2", reason="Rejected")
        defer.assert_called_once_with("NTC-3", reason="Later")
        merge.assert_called_once_with(
            "NTC-4",
            reason="Duplicate",
            target_candidate_id="NTC-5",
            target_node_id="NT-1",
        )
        supersede.assert_called_once_with(
            "NTC-6",
            reason="Replaced",
            target_candidate_id="NTC-7",
            target_node_id="NT-2",
        )
        delete.assert_called_once_with("NTC-8", reason="Delete")

    def test_nova_task_review_endpoints_return_disabled_state_when_feature_is_off(self):
        tasks_router.foundation.nova_task_enabled.return_value = False
        with patch.object(tasks_router.nova_task_review, "candidate_status", side_effect=AssertionError("review called")):
            result = asyncio.run(tasks_router.api_task_candidate_status())

        self.assertFalse(result["enabled"])
        self.assertEqual(result["pendingReviewCount"], 0)
        self.assertFalse(result["hasPendingReview"])
        self.assertEqual(result["pendingCount"], 0)
        self.assertFalse(result["hasPending"])

    def test_task_board_endpoint_returns_disabled_state_when_nova_task_is_off(self):
        tasks_router.foundation.nova_task_enabled.return_value = False
        result = asyncio.run(tasks_router.api_tasks())

        self.assertFalse(result["enabled"])
        self.assertFalse(result["novaTaskEnabled"])
        self.assertEqual(result["tasks"], [])

    def test_task_board_endpoint_uses_nova_task_tree_without_legacy_board_read(self):
        expected_tree = {"roots": [{"nodeId": "NT-1"}], "nodes": [{"nodeId": "NT-1"}], "count": 1}
        with patch.object(tasks_router.nova_task_review, "tree", return_value=expected_tree) as tree:
            result = asyncio.run(tasks_router.api_tasks())

        tree.assert_called_once_with()
        self.assertEqual(result["authority"], "nova-task-v2-sqlite")
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["grouped"], {})
        self.assertEqual(result["tree"], expected_tree["roots"])
        self.assertEqual(result["nodes"], expected_tree["nodes"])

    def test_retired_task_text_and_overview_routes_are_removed(self):
        router_source = (ROOT / "src" / "dashboard" / "app" / "routers" / "tasks.py").read_text(encoding="utf-8")

        self.assertNotIn('@router.patch("/tasks")', router_source)
        self.assertNotIn('@router.get("/overview-stats")', router_source)
        self.assertNotIn("api_overview_stats", router_source)


if __name__ == "__main__":
    unittest.main()
