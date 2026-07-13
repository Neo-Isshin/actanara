import asyncio
import importlib.util
import importlib.machinery
import inspect
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


class _RouterStub:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def put(self, *_args, **_kwargs):
        return lambda function: function

    def post(self, *_args, **_kwargs):
        return lambda function: function

    def patch(self, *_args, **_kwargs):
        return lambda function: function


class _JSONResponseStub(dict):
    def __init__(self, content=None, status_code=200, **kwargs):
        super().__init__(content or {})
        self.status_code = status_code
        self.kwargs = kwargs
        self.body = json.dumps(content or {}).encode("utf-8")


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


if importlib.util.find_spec("fastapi") is None:
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

from app.routers import settings as settings_router
from app.services import msgbox


class DashboardMsgboxTests(unittest.TestCase):
    def test_msgbox_aggregates_pipeline_failure_and_task_candidates(self):
        with (
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": {
                        "id": 7,
                        "status": "failed",
                        "business_date": "2026-06-06",
                        "trigger_type": "pipeline-foundation-materialization",
                        "error_summary": "LLM request failed after fallback attempts",
                        "completed_at": "2026-06-08T10:00:00",
                    }
                },
            ),
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.nova_task_review,
                "candidates",
                return_value={
                    "pendingReviewCount": 2,
                    "pendingCount": 2,
                    "candidates": [
                        {
                            "candidateType": "parent_task",
                            "proposedTitle": "New parent",
                            "createdAt": "2026-06-08T10:05:00",
                        },
                        {"candidateType": "subtask", "proposedTitle": "New subtask", "createdAt": "2026-06-08T10:04:00"},
                    ],
                },
            ),
        ):
            result = msgbox.message_box()
            msgbox.nova_task_review.candidates.assert_called_once_with(status="pending_review", limit=50)

        self.assertEqual(result["attentionCount"], 2)
        self.assertEqual({item["type"] for item in result["items"]}, {"pipeline_failure", "task_candidate_review"})
        task_item = next(item for item in result["items"] if item["type"] == "task_candidate_review")
        self.assertEqual(task_item["details"]["parentCount"], 1)
        self.assertEqual(task_item["details"]["subtaskCount"], 1)
        self.assertEqual(task_item["details"]["pendingReviewCount"], 2)
        self.assertIn("2 条 L1 提案待确认", task_item["summary"])

    def test_msgbox_includes_daily_pipeline_failure_log(self):
        with (
            patch.object(
                msgbox,
                "latest_pipeline_failure",
                return_value={
                    "businessDate": "2026-06-06",
                    "failedStep": "2. Narrative Pass",
                    "reason": "LLM request failed after fallback attempts",
                    "createdAt": "2026-06-08T11:00:00",
                },
            ),
            patch.object(msgbox.foundation, "list_refresh_jobs", return_value={"latestFailed": None}),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["title"], "每日管线运行失败")
        self.assertEqual(result["items"][0]["details"]["failedStep"], "2. Narrative Pass")

    def test_msgbox_includes_history_backfill_retry_action(self):
        with (
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": None,
                    "jobs": [
                        {
                            "id": 42,
                            "trigger_type": "dashboard-history-backfill",
                            "status": "partial",
                            "started_at": "2026-06-17T10:00:00",
                            "completed_at": "2026-06-17T10:05:00",
                            "metadata": {
                                "failedPeriodDetails": [
                                    {"kind": "month", "start": "2026-04-01", "end": "2026-04-30", "error": "period failed"}
                                ]
                            },
                            "error_summary": "1 period(s) failed",
                        }
                    ],
                },
            ),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["type"], "history_backfill_failure")
        self.assertEqual(item["actionLabel"], "重跑失败项")
        self.assertEqual(item["action"]["kind"], "apiPost")
        self.assertIn("/api/foundation/history-backfill/42/retry-failed", item["action"]["url"])

    def test_msgbox_includes_history_backfill_daily_failure_retry_action(self):
        with (
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": None,
                    "jobs": [
                        {
                            "id": 43,
                            "trigger_type": "dashboard-history-backfill",
                            "status": "partial",
                            "started_at": "2026-06-17T10:00:00",
                            "completed_at": "2026-06-17T10:05:00",
                            "metadata": {
                                "dailyPipeline": {
                                    "total": 2,
                                    "completed": [],
                                    "skipped": [],
                                    "failed": [{"date": "2026-04-02", "error": "Narrative Pass"}],
                                }
                            },
                            "error_summary": "1 daily pipeline day(s) failed",
                        }
                    ],
                },
            ),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        item = result["items"][0]
        self.assertEqual(item["type"], "history_backfill_failure")
        self.assertEqual(item["details"]["failedDailyPipelineDays"][0]["date"], "2026-04-02")
        self.assertIn("/api/foundation/history-backfill/43/retry-failed", item["action"]["url"])

    def test_msgbox_uses_native_snapshot_retry_stage_without_legacy_failure_fields(self):
        with (
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": None,
                    "jobs": [
                        {
                            "id": 45,
                            "trigger_type": "dashboard-history-backfill",
                            "status": "failed",
                            "started_at": "2026-06-17T10:00:00",
                            "completed_at": "2026-06-17T10:05:00",
                            "metadata": {
                                "outcomeSchemaVersion": 2,
                                "retryStages": [
                                    {"id": "snapshot:ai-assets", "kind": "snapshot", "snapshot": "ai-assets"}
                                ],
                            },
                            "error_summary": "AI Assets snapshot failed",
                        }
                    ],
                },
            ),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        item = next(item for item in result["items"] if item["type"] == "history_backfill_failure")
        self.assertEqual(item["details"]["retryStages"][0]["id"], "snapshot:ai-assets")
        self.assertIn("/api/foundation/history-backfill/45/retry-failed", item["action"]["url"])

    def test_msgbox_does_not_expand_native_empty_retry_from_legacy_fields(self):
        with (
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": None,
                    "jobs": [
                        {
                            "id": 46,
                            "trigger_type": "dashboard-history-backfill",
                            "status": "failed",
                            "metadata": {
                                "outcomeSchemaVersion": 2,
                                "retryStages": [],
                                "dailyPipeline": {"failed": [{"date": "2026-04-01", "error": "old"}]},
                                "failedPeriodDetails": [
                                    {"kind": "week", "start": "2026-04-01", "end": "2026-04-01", "error": "old"}
                                ],
                            },
                            "error_summary": "non-retryable",
                        }
                    ],
                },
            ),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        self.assertFalse(any(item["type"] == "history_backfill_failure" for item in result["items"]))

    def test_msgbox_includes_pipeline_catchup_confirmation(self):
        with (
            patch.object(msgbox, "_read_message_ids", return_value=set()),
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(msgbox.foundation, "list_refresh_jobs", return_value={"jobs": [], "latestFailed": None}),
            patch.object(
                msgbox,
                "list_pipeline_runs",
                return_value=[
                    {
                        "id": 7,
                        "runKind": "catchup_reconcile",
                        "status": "blocked",
                        "failureClass": "manual_confirmation_required",
                        "metadata": {"missingDates": ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"]},
                        "updated_at": "2026-06-24T09:00:00+00:00",
                        "errorSummary": "4 missing pipeline day(s) require confirmation before catch-up.",
                    }
                ],
            ),
            patch.object(msgbox.nova_task_review, "candidates", return_value={"pendingCount": 0, "candidates": []}),
        ):
            result = msgbox.message_box()

        item = result["items"][0]
        self.assertEqual(item["type"], "pipeline_catchup_confirmation")
        self.assertEqual(item["details"]["missingDates"], ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"])

    def test_msgbox_localizes_display_copy_for_english_profile_without_changing_contract(self):
        with (
            patch.object(msgbox, "dashboard_language_profile", return_value="en"),
            patch.object(msgbox, "latest_pipeline_failure", return_value=None),
            patch.object(
                msgbox.foundation,
                "list_refresh_jobs",
                return_value={
                    "latestFailed": None,
                    "jobs": [
                        {
                            "id": 44,
                            "trigger_type": "dashboard-history-backfill",
                            "status": "partial",
                            "started_at": "2026-06-17T10:00:00",
                            "completed_at": "2026-06-17T10:05:00",
                            "metadata": {
                                "failedPeriodDetails": [
                                    {"kind": "month", "start": "2026-04-01", "end": "2026-04-30", "error": "period failed"}
                                ]
                            },
                            "error_summary": "1 period(s) failed",
                        }
                    ],
                },
            ),
            patch.object(
                msgbox.nova_task_review,
                "candidates",
                return_value={
                    "pendingReviewCount": 1,
                    "pendingCount": 1,
                    "candidates": [{"candidateType": "parent_task", "proposedTitle": "New parent", "createdAt": "2026-06-17T10:06:00"}],
                },
            ),
        ):
            result = msgbox.message_box()

        history_item = next(item for item in result["items"] if item["type"] == "history_backfill_failure")
        task_item = next(item for item in result["items"] if item["type"] == "task_candidate_review")
        self.assertEqual(history_item["title"], "Historical data generation partially failed")
        self.assertIn("failed item", history_item["summary"])
        self.assertEqual(history_item["actionLabel"], "Retry Failed Items")
        self.assertEqual(history_item["action"]["kind"], "apiPost")
        self.assertIn("/api/foundation/history-backfill/44/retry-failed", history_item["action"]["url"])
        self.assertEqual(history_item["action"]["successMessage"], "Failed-item retry task submitted")
        self.assertEqual(task_item["title"], "New L1 proposals need review")
        self.assertIn("1 L1 proposal", task_item["summary"])
        self.assertEqual(task_item["actionLabel"], "Open Task Board")
        self.assertEqual(task_item["action"], {"kind": "openUrl", "url": "/tasks"})
        self.assertEqual(task_item["details"]["pendingReviewCount"], 1)
        self.assertEqual(task_item["details"]["parentCount"], 1)

    def test_msgbox_router_delegates_to_service(self):
        with patch.object(settings_router.msgbox, "message_box", return_value={"items": []}) as service:
            result = asyncio.run(settings_router.api_msgbox(limit=5))

        service.assert_called_once_with(limit=5)
        self.assertEqual(result, {"items": []})

    def test_msgbox_read_endpoint_delegates_to_service(self):
        with patch.object(settings_router.msgbox, "mark_read", return_value={"status": "ok"}) as service:
            result = asyncio.run(settings_router.api_msgbox_mark_read("message-1"))

        service.assert_called_once_with("message-1")
        self.assertEqual(result, {"status": "ok"})

    def test_msgbox_filters_read_message_ids(self):
        with (
            patch.object(msgbox, "_read_message_ids", return_value={"nova-task-candidates-pending"}),
            patch.object(msgbox, "_pipeline_failure_messages", return_value=[]),
            patch.object(
                msgbox,
                "_task_candidate_messages",
                return_value=[
                    {
                        "id": "nova-task-candidates-pending",
                        "type": "task_candidate_review",
                        "severity": "warn",
                        "createdAt": "2026-06-08T10:05:00",
                    }
                ],
            ),
        ):
            result = msgbox.message_box()

        self.assertEqual(result["items"], [])
        self.assertEqual(result["attentionCount"], 0)

    def test_msgbox_reports_degraded_source_and_keeps_payload_renderable(self):
        with (
            patch.object(msgbox, "_read_message_ids", return_value=set()),
            patch.object(msgbox, "_pipeline_failure_messages", side_effect=RuntimeError("pipeline source failed")),
            patch.object(msgbox, "_task_candidate_messages", return_value=[]),
        ):
            result = msgbox.message_box(limit=20)

        self.assertEqual(result["degradedCount"], 1)
        self.assertEqual(result["degraded"][0]["id"], "pipeline-foundation")
        self.assertEqual(result["degraded"][0]["status"], "degraded")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["attentionCount"], 1)
        item = result["items"][0]
        self.assertEqual(item["type"], "source_degraded")
        self.assertEqual(item["severity"], "warn")
        self.assertEqual(item["action"], {"kind": "openPage", "page": "foundation-ops"})
        self.assertIn("pipeline source failed", item["summary"])

    def test_msgbox_caps_limit_and_mark_read_writes_state_atomically(self):
        messages = [
            {
                "id": f"message-{idx:03d}",
                "type": "task_candidate_review",
                "severity": "warn",
                "createdAt": f"2026-06-08T10:{idx % 60:02d}:00",
            }
            for idx in range(150)
        ]
        with (
            patch.object(msgbox, "_read_message_ids", return_value=set()),
            patch.object(msgbox, "_pipeline_failure_messages", return_value=messages),
            patch.object(msgbox, "_task_candidate_messages", return_value=[]),
        ):
            result = msgbox.message_box(limit=1000)

        self.assertEqual(result["count"], msgbox.MAX_MSGBOX_LIMIT)
        self.assertEqual(len(result["items"]), msgbox.MAX_MSGBOX_LIMIT)

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state" / "dashboard" / "msgbox-read.json"
            with patch.object(msgbox, "_read_state_path", return_value=state_path):
                self.assertEqual(msgbox.mark_read("message-1"), {"status": "ok", "messageId": "message-1"})

                self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), ["message-1"])
                self.assertFalse(state_path.with_suffix(state_path.suffix + ".tmp").exists())
                self.assertTrue(state_path.with_suffix(state_path.suffix + ".lock").exists())

                state_path.write_text("x" * (msgbox.MAX_READ_STATE_BYTES + 1), encoding="utf-8")
                self.assertEqual(msgbox._read_message_ids(), set())


if __name__ == "__main__":
    unittest.main()
