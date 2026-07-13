import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import background_tasks


class DashboardBackgroundTasksTests(unittest.TestCase):
    def test_background_tasks_aggregates_active_refresh_repair_scheduler_and_rag(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 7,
                    "trigger_type": "dashboard-projection-refresh",
                    "business_date": "2026-06-15",
                    "started_at": "2026-06-15T08:00:00+08:00",
                    "completed_at": None,
                    "status": "running",
                    "metadata": {
                        "periodStart": "2026-06-01",
                        "periodEnd": "2026-06-15",
                        "progress": 72,
                        "currentStageLabel": "Refreshing AI Assets usage cache and snapshot",
                        "usageCache": {"sources": 10, "cached": 8, "reparsed": 2, "removed": 0, "errors": 0},
                    },
                    "error_summary": None,
                }
            ]
        }
        repair_payload = {
            "runs": [
                {
                    "id": 3,
                    "actionId": "run-full-daily-pipeline",
                    "businessDate": "2026-06-15",
                    "requestedAt": "2026-06-15T07:59:00+08:00",
                    "status": "queued",
                }
            ]
        }
        scheduler_payload = {
            "state": {"lastDashboardAggregationAt": "2026-06-15T04:30:00+08:00"},
            "systemTimer": {
                "provider": "launchd",
                "registered": True,
                "jobs": [
                    {
                        "kind": "daily-pipeline",
                        "label": "open-nova.daily.pipeline",
                        "time": "04:00",
                        "plistPath": "/tmp/open-nova.daily.pipeline.plist",
                    }
                ],
            },
        }
        rag_index_jobs = [
            {
                "id": "rag-index-1",
                "status": "running",
                "progress": 45,
                "requestedAt": "2026-06-15T08:02:00+08:00",
                "sourceSets": ["filtered-dialogue-daily"],
                "providerId": "local",
            }
        ]
        rag_skill_jobs = [
            {
                "id": "rag-skill-1",
                "status": "running",
                "progress": 30,
                "requestedAt": "2026-06-15T08:03:00+08:00",
                "operations": [{"tool": "codex"}],
            }
        ]
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value=repair_payload),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=rag_index_jobs),
            patch.object(
                background_tasks.external_rag_skill_registration,
                "list_rag_skill_registration_jobs",
                return_value=rag_skill_jobs,
            ),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value=scheduler_payload),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(
                background_tasks,
                "read_server_process_state",
                return_value={"status": "starting", "requestedAt": "2026-06-15T08:01:00+08:00"},
            ),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        self.assertEqual(payload["activeCount"], 4)
        task_ids = {task["id"] for task in payload["tasks"]}
        self.assertIn("foundation-refresh-7", task_ids)
        self.assertIn("foundation-repair-3", task_ids)
        self.assertIn("rag-index-1", task_ids)
        self.assertIn("rag-skill-1", task_ids)
        self.assertIn("scheduler-daily-pipeline", task_ids)
        service_ids = {service["id"] for service in payload["services"]}
        self.assertIn("rag-server-lifecycle", service_ids)
        active_ids = {task["id"] for task in payload["active"]}
        self.assertIn("foundation-refresh-7", active_ids)
        self.assertIn("foundation-repair-3", active_ids)
        self.assertIn("rag-index-1", active_ids)
        self.assertIn("rag-skill-1", active_ids)
        self.assertNotIn("rag-server-lifecycle", active_ids)
        self.assertEqual(payload["sources"]["ragCandidateRefreshJobs"], 1)
        self.assertEqual(payload["sources"]["ragSkillRegistrationJobs"], 1)
        self.assertEqual(payload["summary"]["activeTasks"], 4)
        self.assertEqual(payload["summary"]["services"], 1)
        self.assertEqual(payload["summary"]["bySource"]["rag"], 2)
        self.assertEqual(payload["summary"]["byStatus"]["running"], 3)
        self.assertEqual(payload["summary"]["byStatus"]["queued"], 1)
        foundation_task = next(task for task in payload["tasks"] if task["id"] == "foundation-refresh-7")
        self.assertEqual(foundation_task["progress"], 72)
        self.assertIn("Refreshing AI Assets usage cache", foundation_task["subtitle"])
        self.assertIn("cached=8", foundation_task["subtitle"])
        self.assertIn("reparsed=2", foundation_task["metadata"]["usageCacheSummary"])

    def test_background_tasks_reports_degraded_refresh_source_without_failing_payload(self):
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", side_effect=RuntimeError("refresh db locked")),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(
                background_tasks.external_rag_skill_registration,
                "list_rag_skill_registration_jobs",
                return_value=[],
            ),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        self.assertEqual(payload["degradedCount"], 1)
        self.assertEqual(payload["degraded"][0]["id"], "foundationRefreshJobs")
        self.assertEqual(payload["degraded"][0]["status"], "degraded")
        self.assertEqual(payload["sources"]["foundationRefreshJobs"], 0)
        self.assertEqual(payload["sources"]["historyBackfillJobs"], 0)
        degraded_task = next(task for task in payload["tasks"] if task["id"] == "foundation-refresh-status")
        self.assertEqual(degraded_task["status"], "failed")
        self.assertTrue(degraded_task["degraded"])
        self.assertIn("refresh db locked", degraded_task["errorSummary"])
        self.assertEqual(payload["services"], [])

    def test_background_tasks_labels_history_backfill_jobs(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 12,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": None,
                    "status": "running",
                    "metadata": {
                        "periodStart": "2026-04-01",
                        "periodEnd": "2026-04-30",
                        "progress": 35,
                        "currentStageLabel": "Backfilling month 2026-04-01..2026-04-30",
                        "dailyPipeline": {
                            "total": 30,
                            "completed": ["2026-04-01", "2026-04-02"],
                            "skipped": ["2026-04-03"],
                            "failed": [{"date": "2026-04-04", "error": "failed"}],
                        },
                        "completedPeriods": 1,
                        "skippedPeriods": 2,
                        "failedPeriods": 1,
                    },
                    "error_summary": None,
                }
            ]
        }
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        task = payload["tasks"][0]
        self.assertEqual(task["id"], "history-backfill-12")
        self.assertEqual(task["source"], "history-backfill")
        self.assertEqual(task["title"], "History data backfill: 2026-04-01..2026-04-30")
        self.assertEqual(task["progress"], 35)
        self.assertIn("daily 3/30, failed=1", task["subtitle"])
        self.assertIn("periods completed=1, skipped=2, failed=1", task["subtitle"])
        self.assertEqual(task["actions"][0]["url"], "/api/foundation/history-backfill/12/cancel")
        self.assertEqual(task["actions"][0]["label"], "取消")
        self.assertEqual(payload["sources"]["historyBackfillJobs"], 1)

    def test_background_tasks_treats_scheduled_history_backfill_as_active(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 13,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": None,
                    "status": "scheduled",
                    "metadata": {
                        "periodStart": "2026-04-01",
                        "periodEnd": "2026-04-30",
                        "scheduledAt": "2026-06-17T23:00:00+08:00",
                    },
                    "error_summary": None,
                }
            ]
        }
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        self.assertEqual(payload["activeCount"], 1)
        self.assertEqual(payload["active"][0]["status"], "scheduled")

    def test_history_backfill_native_retry_stages_are_the_only_retry_authority(self):
        native_retry = {
            "outcomeSchemaVersion": 2,
            "retryStages": [{"id": "snapshot:ai-assets", "kind": "snapshot", "snapshot": "ai-assets"}],
            "dailyPipeline": {"failed": []},
            "failedPeriodDetails": [],
        }
        native_complete = {
            "outcomeSchemaVersion": 2,
            "retryStages": [],
            "dailyPipeline": {"failed": [{"date": "2026-04-01", "error": "legacy-looking"}]},
            "failedPeriodDetails": [{"kind": "week", "start": "2026-04-01", "end": "2026-04-01"}],
        }

        self.assertTrue(background_tasks._history_backfill_has_retryable_failures(native_retry))
        self.assertFalse(background_tasks._history_backfill_has_retryable_failures(native_complete))

    def test_background_tasks_tracks_history_cancel_requested_but_not_cancelled(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 14,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:01:00+08:00",
                    "completed_at": None,
                    "status": "cancel_requested",
                    "metadata": {"periodStart": "2026-04-01", "periodEnd": "2026-04-30"},
                    "error_summary": None,
                },
                {
                    "id": 15,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": "2026-06-17T08:02:00+08:00",
                    "status": "cancelled",
                    "metadata": {"periodStart": "2026-04-01", "periodEnd": "2026-04-30"},
                    "error_summary": "Cancelled by user request",
                },
            ]
        }
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        self.assertEqual(payload["activeCount"], 1)
        active = payload["active"][0]
        cancelled = next(task for task in payload["tasks"] if task["id"] == "history-backfill-15")
        self.assertEqual(active["status"], "cancel_requested")
        self.assertEqual(active["actions"][0]["label"], "取消中")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["actions"], [])

    def test_background_tasks_offers_retry_for_failed_history_items(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 16,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": "2026-06-17T08:05:00+08:00",
                    "status": "partial",
                    "metadata": {
                        "periodStart": "2026-04-01",
                        "periodEnd": "2026-04-30",
                        "dailyPipeline": {"total": 2, "completed": [], "skipped": [], "failed": [{"date": "2026-04-02", "error": "failed"}]},
                    },
                    "error_summary": "1 daily pipeline day(s) failed",
                }
            ]
        }
        with (
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        task = payload["tasks"][0]
        self.assertEqual(task["actions"][0]["label"], "重跑失败项")
        self.assertEqual(task["actions"][0]["url"], "/api/foundation/history-backfill/16/retry-failed")

    def test_background_tasks_localizes_history_actions_for_english_profile_without_changing_contract(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 17,
                    "trigger_type": "dashboard-history-backfill",
                    "business_date": "2026-04-30",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": "2026-06-17T08:05:00+08:00",
                    "status": "partial",
                    "metadata": {
                        "periodStart": "2026-04-01",
                        "periodEnd": "2026-04-30",
                        "dailyPipeline": {"total": 1, "completed": [], "skipped": [], "failed": [{"date": "2026-04-02"}]},
                    },
                    "error_summary": "1 daily pipeline day(s) failed",
                }
            ]
        }
        with (
            patch.object(background_tasks, "dashboard_language_profile", return_value="en"),
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value={"runs": []}),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"jobs": []}}),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "missing"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        task = payload["tasks"][0]
        self.assertEqual(task["id"], "history-backfill-17")
        self.assertEqual(task["source"], "history-backfill")
        self.assertEqual(task["status"], "partial")
        self.assertEqual(task["title"], "History data backfill: 2026-04-01..2026-04-30")
        self.assertIn("daily 0/1, failed=1", task["subtitle"])
        self.assertEqual(task["actions"][0]["kind"], "apiPost")
        self.assertEqual(task["actions"][0]["url"], "/api/foundation/history-backfill/17/retry-failed")
        self.assertEqual(task["actions"][0]["label"], "Retry Failed Items")
        self.assertEqual(task["actions"][0]["successMessage"], "Failed-item retry task submitted")

    def test_background_tasks_localizes_service_titles_without_changing_contract(self):
        refresh_payload = {
            "jobs": [
                {
                    "id": 18,
                    "trigger_type": "dashboard-period-summary-refresh",
                    "business_date": "2026-06-05",
                    "started_at": "2026-06-17T08:00:00+08:00",
                    "completed_at": None,
                    "status": "running",
                    "metadata": {
                        "periodStart": "2026-06-01",
                        "periodEnd": "2026-06-07",
                        "usageCache": {"sources": 2, "cached": 1},
                        "workEstimate": {"periodDays": 7, "llmCalls": 1, "longRunning": False},
                    },
                    "error_summary": None,
                }
            ]
        }
        repair_payload = {
            "runs": [
                {
                    "id": 4,
                    "actionId": "retry-daily-pipeline",
                    "businessDate": "2026-06-05",
                    "requestedAt": "2026-06-17T08:01:00+08:00",
                    "status": "queued",
                }
            ]
        }
        with (
            patch.object(background_tasks, "dashboard_language_profile", return_value="en"),
            patch.object(background_tasks.foundation, "list_refresh_jobs", return_value=refresh_payload),
            patch.object(background_tasks.foundation_ops, "list_foundation_repair_runs", return_value=repair_payload),
            patch.object(background_tasks.rag_index_jobs, "list_candidate_refresh_jobs", return_value=[{"id": "rag-1", "type": "rag-profile-migration", "status": "running", "progress": 7, "providerId": "local", "sourceSets": ["diary-markdown-sections"]}]),
            patch.object(background_tasks.scheduler, "scheduler_status", return_value={"systemTimer": {"registered": True, "jobs": [{"kind": "daily-pipeline", "label": "open-nova.daily.pipeline", "time": "04:00"}]}}),
            patch.object(background_tasks.external_rag_skill_registration, "list_rag_skill_registration_jobs", return_value=[{"id": "skill-1", "status": "running", "operations": [{"tool": "codex"}]}]),
            patch.object(background_tasks, "resolve_rag_settings", return_value=object()),
            patch.object(background_tasks, "read_server_process_state", return_value={"status": "running", "statePath": "/tmp/rag.json"}),
        ):
            payload = background_tasks.get_background_tasks(limit=10)

        refresh_task = next(task for task in payload["tasks"] if task["source"] == "foundation-refresh")
        repair_task = next(task for task in payload["tasks"] if task["source"] == "foundation-repair")
        scheduler_task = next(task for task in payload["tasks"] if task["source"] == "scheduler")
        rag_titles = {task["title"] for task in payload["tasks"] if task["source"] == "rag"}
        service_titles = {service["title"] for service in payload["services"]}
        self.assertEqual(refresh_task["title"], "Period summary refresh: 2026-06-01..2026-06-07")
        self.assertIn("estimated work days=7, LLM calls=1", refresh_task["subtitle"])
        self.assertIn("usage cache sources=2, cached=1", refresh_task["subtitle"])
        self.assertEqual(repair_task["title"], "Daily QA repair: retry-daily-pipeline")
        self.assertEqual(repair_task["subtitle"], "business date 2026-06-05")
        self.assertEqual(scheduler_task["title"], "LaunchAgent: open-nova.daily.pipeline")
        self.assertEqual(scheduler_task["subtitle"], "daily-pipeline at 04:00")
        self.assertIn("RAG profile migration", rag_titles)
        self.assertIn("RAG external agent skill registration", rag_titles)
        self.assertIn("nova-RAG server", service_titles)


if __name__ == "__main__":
    unittest.main()
