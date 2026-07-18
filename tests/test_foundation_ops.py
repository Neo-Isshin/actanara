import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import foundation_ops
from data_foundation.db import migrate
from data_foundation.diary_markdown import materialize_diary_markdown_day
from data_foundation.jobs import begin_ingestion_run, finish_ingestion_run
from data_foundation.nova_task import create_task_node, ingest_nova_task_evidence
from data_foundation.paths import initialize_home, update_runtime_manifest_paths

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


class FoundationOpsTests(unittest.TestCase):
    def test_projection_completeness_matrix_marks_required_and_optional_rows(self):
        readiness = {
            "configuredSources": {"aiAssets": "legacy", "periodAssets": "legacy"},
            "configuredSourcesValid": True,
            "canEnable": {"dashboardReadSourceFoundation": True, "reportReadSourceFoundation": False},
            "aiAssets": {
                "ready": True,
                "status": "current",
                "projectionType": "dashboard-ai-assets-non-rag-v1",
                "generatedAt": "2026-05-29T04:31:00+08:00",
                "sourceRunId": 101,
            },
            "periodAssets": {
                "checked": True,
                "ready": False,
                "status": "memory_fields_missing",
                "start": "2026-05-25",
                "end": "2026-05-29",
                "days": 5,
                "memoryReady": False,
            },
            "periodPage": {"checked": True, "ready": True, "status": "current", "sourceRunId": 102},
            "periodSummary": {"checked": True, "ready": False, "status": "missing"},
        }

        matrix = foundation_ops.build_projection_completeness_matrix(readiness)

        self.assertEqual(matrix["status"], "incomplete")
        self.assertEqual(matrix["requiredComplete"], 2)
        self.assertEqual(matrix["requiredTotal"], 3)
        rows = {row["key"]: row for row in matrix["rows"]}
        self.assertTrue(rows["aiAssets"]["complete"])
        self.assertFalse(rows["periodAssets"]["complete"])
        self.assertFalse(rows["periodSummary"]["complete"])
        self.assertTrue(rows["periodSummary"]["optional"])
        self.assertEqual(rows["periodAssets"]["requiredFor"], ["reportReadSourceFoundation"])

    def test_projection_completeness_matrix_treats_unchecked_periods_as_incomplete(self):
        matrix = foundation_ops.build_projection_completeness_matrix(
            {
                "aiAssets": {"ready": True, "status": "current"},
                "periodAssets": {"checked": False, "ready": None, "status": "not_requested"},
                "periodPage": {"checked": False, "ready": None, "status": "not_requested"},
            }
        )

        self.assertEqual(matrix["status"], "incomplete")
        rows = {row["key"]: row for row in matrix["rows"]}
        self.assertFalse(rows["periodAssets"]["complete"])
        self.assertEqual(rows["periodAssets"]["status"], "not_requested")

    def test_projection_completeness_matrix_ignores_period_rows_for_single_day_scope(self):
        matrix = foundation_ops.build_projection_completeness_matrix(
            {
                "aiAssets": {"ready": True, "status": "current"},
                "periodAssets": {"ready": False, "status": "missing"},
                "periodPage": {"ready": False, "status": "missing"},
            },
            require_period_projections=False,
        )

        self.assertEqual(matrix["status"], "complete")
        self.assertEqual(matrix["requiredComplete"], 1)
        self.assertEqual(matrix["requiredTotal"], 1)
        rows = {row["key"]: row for row in matrix["rows"]}
        self.assertEqual(rows["periodAssets"]["status"], "not_applicable_single_day")
        self.assertEqual(rows["periodAssets"]["requiredFor"], [])
        self.assertTrue(rows["periodAssets"]["optional"])
        self.assertEqual(rows["periodPage"]["status"], "not_applicable_single_day")

    def test_scheduled_run_cadence_uses_timer_jobs_and_recent_failure(self):
        scheduler_status = {
            "running": False,
            "timezone": "Asia/Hong_Kong",
            "state": {
                "lastDashboardAggregationDate": "2026-05-28",
                "lastDashboardAggregationAt": "2026-05-28T04:31:00+08:00",
                "lastDashboardAggregationRunIds": [91, 92],
            },
            "systemTimer": {
                "provider": "launchd",
                "supported": True,
                "registered": True,
                "jobs": [
                    {"kind": "daily-pipeline", "label": "nova.test.pipeline", "time": "03:10"},
                    {"kind": "dashboard-aggregation", "label": "nova.test.dashboard-aggregation", "time": "03:40"},
                ],
            },
        }
        refresh_jobs = {
            "jobs": [
                {"id": 102, "status": "failed", "error_summary": "planned failure"},
                {"id": 101, "status": "completed"},
            ],
            "latestFailed": {"id": 102, "status": "failed", "error_summary": "planned failure"},
        }
        now = datetime(2026, 5, 29, 3, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))

        cadence = foundation_ops.build_scheduled_run_cadence(scheduler_status, refresh_jobs, now=now)

        self.assertEqual(cadence["status"], "attention")
        self.assertTrue(cadence["enabled"])
        self.assertEqual(cadence["dashboardAggregationTime"], "03:40")
        self.assertEqual(cadence["dailyPipelineTime"], "03:10")
        self.assertEqual(cadence["nextDashboardAggregationAt"], "2026-05-29T03:40:00+08:00")
        self.assertEqual(cadence["systemTimer"]["dashboardAggregationLabel"], "nova.test.dashboard-aggregation")
        self.assertEqual(cadence["latestFailedRefreshJob"]["id"], 102)
        self.assertEqual(cadence["latestHistoricalFailedRefreshJob"]["id"], 102)

    def test_scheduled_run_cadence_does_not_treat_recovered_history_as_current_failure(self):
        refresh_jobs = {
            "jobs": [
                {"id": 103, "status": "completed"},
                {"id": 102, "status": "failed", "error_summary": "recovered failure"},
            ],
            "latest": {"id": 103, "status": "completed"},
            "latestFailed": {"id": 102, "status": "failed", "error_summary": "recovered failure"},
        }

        cadence = foundation_ops.build_scheduled_run_cadence(
            {
                "enabled": True,
                "timezone": "Asia/Hong_Kong",
                "systemTimer": {"supported": True, "registered": True},
            },
            refresh_jobs,
            now=datetime(2026, 5, 29, 3, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(cadence["status"], "scheduled")
        self.assertIsNone(cadence["latestFailedRefreshJob"])
        self.assertEqual(cadence["latestHistoricalFailedRefreshJob"]["id"], 102)

    def test_scheduled_run_cadence_rolls_next_run_to_tomorrow(self):
        cadence = foundation_ops.build_scheduled_run_cadence(
            {
                "running": False,
                "timezone": "Asia/Hong_Kong",
                "systemTimer": {"supported": True, "registered": False},
            },
            [],
            now=datetime(2026, 5, 29, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(cadence["status"], "manual")
        self.assertFalse(cadence["scheduleEnabled"])
        self.assertEqual(cadence["nextDashboardAggregationAt"], "2026-05-30T04:30:00+08:00")

    def test_scheduler_loop_running_without_enabled_schedule_is_manual(self):
        cadence = foundation_ops.build_scheduled_run_cadence(
            {"running": True, "enabled": False, "systemTimer": {"supported": True, "registered": False}},
            [],
            now=datetime(2026, 5, 29, 3, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(cadence["status"], "manual")
        self.assertFalse(cadence["enabled"])
        self.assertFalse(cadence["scheduleEnabled"])
        self.assertTrue(cadence["running"])

    def test_snapshot_operations_composes_matrix_cadence_and_jobs(self):
        payload = foundation_ops.build_snapshot_operations(
            readiness={"aiAssets": {"ready": True}, "periodAssets": {"ready": True}, "periodPage": {"ready": True}},
            refresh_jobs={"jobs": [{"id": 1, "status": "completed"}]},
            scheduler_status={"running": True, "systemTimer": {"supported": True, "registered": False}},
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["projectionCompleteness"]["status"], "complete")
        self.assertEqual(payload["scheduledRunCadence"]["status"], "manual")
        self.assertEqual(payload["refreshJobs"][0]["id"], 1)

    def test_production_readiness_reports_legacy_sources_as_migration_blocker(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": True},
                "periodPage": {"ready": True},
            },
            refresh_jobs={"jobs": [{"id": 1, "status": "completed"}]},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={
                "DASHBOARD_READ_SOURCE": "foundation",
                "REPORT_READ_SOURCE": "legacy",
                "DIARY_METRICS_SOURCE": "foundation",
            },
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "legacy_active")
        self.assertEqual(payload["blockers"][0]["key"], "legacy-sources-active")
        self.assertEqual(payload["legacyNormalPaths"][0]["envName"], "REPORT_READ_SOURCE")
        self.assertFalse(payload["materializationOwner"]["requestTimeLegacyFallbackAllowed"])
        self.assertEqual(payload["boundaries"]["taskAuthority"], "Nova-Task v2 SQLite")
        self.assertEqual(payload["boundaries"]["rag"], "v2-independent")

    def test_production_readiness_treats_task_audit_sink_as_supplemental_cutover(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": True},
                "periodPage": {"ready": True},
            },
            refresh_jobs={"jobs": [{"id": 1, "status": "completed"}], "latest": {"id": 1, "status": "completed"}},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={
                "DASHBOARD_READ_SOURCE": "foundation",
                "REPORT_READ_SOURCE": "foundation",
                "DIARY_METRICS_SOURCE": "foundation",
                "DIARY_MEMORY_SOURCE": "foundation",
                "DIARY_TASKS_SOURCE": "foundation",
                "TASK_AUDIT_SINK": "legacy",
            },
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["legacyNormalPaths"], [])
        self.assertEqual(payload["supplementalLegacyPaths"][0]["envName"], "TASK_AUDIT_SINK")
        self.assertEqual(payload["boundaries"]["taskAuditSink"], "optional-additive-cutover")

    def test_production_readiness_blocks_incomplete_projection_and_failed_job(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": False},
                "periodPage": {"ready": True},
            },
            refresh_jobs={"jobs": [{"id": 2, "status": "failed"}], "latestFailed": {"id": 2, "status": "failed"}},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={"DASHBOARD_READ_SOURCE": "foundation", "REPORT_READ_SOURCE": "foundation"},
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual({item["key"] for item in payload["blockers"]}, {"required-projections-incomplete", "latest-refresh-failed"})

    def test_production_readiness_does_not_block_single_day_on_period_projection(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": False, "status": "missing"},
                "periodPage": {"ready": False, "status": "missing"},
            },
            refresh_jobs={"jobs": [{"id": 1, "status": "completed"}], "latest": {"id": 1, "status": "completed"}},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={"DASHBOARD_READ_SOURCE": "foundation", "REPORT_READ_SOURCE": "foundation"},
            require_period_projections=False,
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["projectionCompleteness"]["requiredTotal"], 1)

    def test_production_readiness_blocks_missing_daily_readiness_report_when_checked(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": True},
                "periodPage": {"ready": True},
                "dailyReadinessReports": {
                    "diaryMetrics": {"ready": True, "status": "ready"},
                    "diaryMemory": {"ready": False, "status": "missing"},
                    "diaryTasks": {"ready": True, "status": "ready"},
                },
            },
            refresh_jobs={"jobs": [{"id": 1, "status": "completed"}], "latest": {"id": 1, "status": "completed"}},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={
                "DASHBOARD_READ_SOURCE": "foundation",
                "REPORT_READ_SOURCE": "foundation",
                "DIARY_METRICS_SOURCE": "foundation",
                "DIARY_MEMORY_SOURCE": "foundation",
                "DIARY_TASKS_SOURCE": "foundation",
            },
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "blocked")
        blocker = next(item for item in payload["blockers"] if item["key"] == "daily-readiness-report-incomplete")
        self.assertEqual(blocker["details"][0]["surface"], "diaryMemory")

    def test_runtime_facade_reuses_existing_services_without_page_read_changes(self):
        with (
            patch("app.services.foundation.get_reader_readiness", return_value={"aiAssets": {"ready": True}}) as readiness,
            patch("app.services.foundation.list_refresh_jobs", return_value={"jobs": []}) as jobs,
            patch("app.services.scheduler.scheduler_status", return_value={"running": False}) as status,
        ):
            result = foundation_ops.get_snapshot_operations(limit=5)

        self.assertIn("projectionCompleteness", result)
        readiness.assert_called_once_with()
        jobs.assert_called_once_with(limit=5)
        status.assert_called_once_with()

    def test_production_readiness_runtime_facade_reuses_existing_services(self):
        with (
            patch("app.services.foundation.get_reader_readiness", return_value={"aiAssets": {"ready": True}}) as readiness,
            patch("app.services.foundation.list_refresh_jobs", return_value={"jobs": []}) as jobs,
            patch("app.services.scheduler.scheduler_status", return_value={"running": False}) as status,
            patch("data_foundation.paths.load_paths") as load_paths,
            patch("data_foundation.settings.resolve_runtime_sources", return_value={"DASHBOARD_READ_SOURCE": "legacy"}) as sources,
        ):
            runtime_paths = object()
            load_paths.return_value = runtime_paths
            result = foundation_ops.get_foundation_production_readiness(limit=5)

        self.assertIn("legacyNormalPaths", result)
        readiness.assert_called_once_with()
        jobs.assert_called_once_with(limit=5)
        status.assert_called_once_with()
        sources.assert_called_once_with(runtime_paths)

    def test_daily_qa_runtime_uses_single_day_production_readiness_scope(self):
        target = date(2026, 6, 23)
        with (
            patch("data_foundation.aggregate.daily_diary_usage_metrics", return_value={}),
            patch("data_foundation.diary_markdown.read_diary_markdown_documents", return_value=[]),
            patch("data_foundation.jobs.list_ingestion_runs", return_value=[]),
            patch("data_foundation.paths.load_paths", return_value=object()),
            patch("data_foundation.pipeline.latest_pipeline_failure", return_value=None),
            patch("data_foundation.settings.ensure_settings", return_value={"pipeline": {"languageProfile": "zh"}}),
            patch("data_foundation.snapshots.read_diary_memory_snapshot", return_value=None),
            patch("data_foundation.snapshots.read_diary_tasks_snapshot", return_value=None),
            patch.object(foundation_ops, "get_foundation_production_readiness", return_value={"status": "ready"}) as production,
        ):
            foundation_ops.get_foundation_daily_qa(business_date=target, limit=7)

        production.assert_called_once_with(period_start=target, period_days=1, limit=7)

    def test_daily_qa_runtime_uses_install_time_pipeline_language_profile(self):
        target = date(2026, 6, 5)
        documents = [
            {
                "document_key": "doc:narrative-en",
                "report_type": "narrative",
                "title": "Diary",
                "source_run_id": 9,
                "embeddedJson": {"metrics": {}, "tasks": {}, "modelUsage": []},
                "sections": [
                    {"heading": "Daily Overview", "bodyMarkdown": "Runtime language gate is ready."},
                    {"heading": "Agent Work", "bodyMarkdown": "Adapter completed."},
                    {"heading": "Important Notices", "bodyMarkdown": "None"},
                    {"heading": "Scheduled Jobs", "bodyMarkdown": "None"},
                    {"heading": "Notes", "bodyMarkdown": "No additional notes."},
                ],
            },
            {
                "document_key": "doc:technical-en",
                "report_type": "technical",
                "title": "Technical",
                "source_run_id": 9,
                "sections": [
                    {"heading": "Engineering Objectives and Outcomes", "bodyMarkdown": "Runtime language gate is ready."},
                    {"heading": "Obstacles, Root Causes, and Detours", "bodyMarkdown": "No blocker."},
                    {"heading": "Implementation Path and Key Decisions", "bodyMarkdown": "Adapter completed."},
                    {"heading": "Verification Evidence", "bodyMarkdown": "QA passed."},
                    {"heading": "Residual Risks and Follow-up Observation", "bodyMarkdown": "None"},
                    {"heading": "Reusable Lessons", "bodyMarkdown": "Keep profile gates explicit."},
                    {"heading": "Nova-Task Reconciliation Hooks", "bodyMarkdown": "None"},
                ],
            },
            {
                "document_key": "doc:learning-en",
                "report_type": "learning",
                "title": "Learning",
                "source_run_id": 9,
                "sections": [
                    {"heading": "Lessons", "bodyMarkdown": "### [codex] Prompt drift"},
                    {"heading": "Infrastructure Updates", "bodyMarkdown": "None"},
                ],
            },
        ]
        with (
            patch("data_foundation.aggregate.daily_diary_usage_metrics", return_value={}),
            patch("data_foundation.diary_markdown.read_diary_markdown_documents", return_value=documents),
            patch("data_foundation.jobs.list_ingestion_runs", return_value=[{"id": 9, "business_date": target.isoformat(), "status": "completed"}]),
            patch("data_foundation.paths.load_paths", return_value=object()),
            patch("data_foundation.pipeline.latest_pipeline_failure", return_value=None),
            patch("data_foundation.settings.ensure_settings", return_value={"pipeline": {"languageProfile": "en"}}),
            patch("data_foundation.snapshots.read_diary_memory_snapshot", return_value={"sourceRunId": 9}),
            patch("data_foundation.snapshots.read_diary_tasks_snapshot", return_value={"sourceRunId": 9}),
            patch.object(foundation_ops, "get_foundation_production_readiness", return_value={"status": "ready"}),
        ):
            payload = foundation_ops.get_foundation_daily_qa(business_date=target, limit=7)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["documents"]["required"]["narrative"]["missingSections"], [])
        self.assertEqual(payload["documents"]["required"]["learning"]["missingSections"], [])

    def test_daily_qa_report_marks_complete_foundation_day_ready(self):
        documents = [
            {
                "document_key": "doc:narrative",
                "report_type": "narrative",
                "title": "Narrative",
                "source_run_id": 9,
                "embeddedJson": {"metrics": {}, "tasks": {}, "modelUsage": []},
                "sections": [
                    {"heading": "天气", "bodyMarkdown": "小雨，最高 30°C"},
                    {"heading": "今日概要", "bodyMarkdown": "完成 Foundation QA。"},
                    {"heading": "本日统计", "bodyMarkdown": "| 指标 | 合计 |\n| --- | --- |\n| api_calls | 1 |"},
                    {"heading": "Agent工作", "bodyMarkdown": "codex 完成实现。"},
                    {"heading": "定时任务情况", "bodyMarkdown": "无"},
                ],
            },
            {
                "document_key": "doc:technical",
                "report_type": "technical",
                "title": "Technical",
                "source_run_id": 9,
                "embeddedJson": None,
                "sections": [
                    {"heading": "一、工程目标与完成结果", "bodyMarkdown": "完成 Foundation QA。"},
                    {"heading": "二、阻碍、根因与弯路", "bodyMarkdown": "无阻碍。"},
                    {"heading": "三、实现路径与关键决策", "bodyMarkdown": "采用 Foundation readiness contract。"},
                    {"heading": "四、验证证据", "bodyMarkdown": "QA passed。"},
                    {"heading": "五、残余风险与后续观察", "bodyMarkdown": "无"},
                    {"heading": "六、可沉淀经验", "bodyMarkdown": "保持 contract 明确。"},
                    {"heading": "七、Nova-Task Reconciliation Hooks", "bodyMarkdown": "无"},
                ],
            },
            {
                "document_key": "doc:learning",
                "report_type": "learning",
                "title": "Learning",
                "source_run_id": 9,
                "embeddedJson": None,
                "sections": [
                    {"heading": "黄金教训 (Lessons)", "bodyMarkdown": "lesson"},
                    {"heading": "基建变动 (Infrastructure)", "bodyMarkdown": "infra"},
                ],
            },
        ]
        ready = {
            "status": "ready",
            "sourceRunId": 9,
            "canEnable": {"diaryMetricsSourceFoundation": True},
        }

        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 5),
            documents=documents,
            diary_readiness={"metrics": ready, "memory": ready, "tasks": ready},
            ingestion_runs=[{"id": 9, "business_date": "2026-06-05", "status": "completed"}],
            latest_pipeline_failure=None,
            production_readiness={"status": "ready"},
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["documents"]["status"], "complete")
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["foundationIngestion"]["latestForDate"]["id"], 9)

    def test_daily_qa_report_accepts_english_section_aliases(self):
        ready = {
            "status": "ready",
            "sourceRunId": 9,
            "canEnable": {"diaryMetricsSourceFoundation": True},
        }
        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 5),
            documents=[
                {
                    "document_key": "doc:narrative-en",
                    "report_type": "narrative",
                    "title": "Diary",
                    "source_run_id": 9,
                    "embeddedJson": {"metrics": {}, "tasks": {}, "modelUsage": []},
                    "sections": [
                        {"heading": "Daily Overview", "bodyMarkdown": "* **Runtime language gate**: ready"},
                        {"heading": "Agent Work", "bodyMarkdown": "codex completed adapter work"},
                        {"heading": "Important Notices", "bodyMarkdown": "None"},
                        {"heading": "Scheduled Jobs", "bodyMarkdown": "None"},
                        {"heading": "Notes", "bodyMarkdown": "No generated artifacts were written."},
                    ],
                },
                {
                    "document_key": "doc:technical-en",
                    "report_type": "technical",
                    "title": "Technical",
                    "source_run_id": 9,
                    "embeddedJson": None,
                    "sections": [
                        {"heading": "Engineering Objectives and Outcomes", "bodyMarkdown": "Runtime language gate ready."},
                        {"heading": "Obstacles, Root Causes, and Detours", "bodyMarkdown": "No blocker."},
                        {"heading": "Implementation Path and Key Decisions", "bodyMarkdown": "Adapter completed."},
                        {"heading": "Verification Evidence", "bodyMarkdown": "QA passed."},
                        {"heading": "Residual Risks and Follow-up Observation", "bodyMarkdown": "None"},
                        {"heading": "Reusable Lessons", "bodyMarkdown": "Keep aliases stable."},
                        {"heading": "Nova-Task Reconciliation Hooks", "bodyMarkdown": "None"},
                    ],
                },
                {
                    "document_key": "doc:learning-en",
                    "report_type": "learning",
                    "title": "Learning",
                    "source_run_id": 9,
                    "embeddedJson": None,
                    "sections": [
                        {"heading": "Lessons", "bodyMarkdown": "### [codex] Prompt drift\n#### Problem\nDrift."},
                        {"heading": "Infrastructure Updates", "bodyMarkdown": "None"},
                    ],
                },
            ],
            diary_readiness={"metrics": ready, "memory": ready, "tasks": ready},
            ingestion_runs=[{"id": 9, "business_date": "2026-06-05", "status": "completed"}],
            latest_pipeline_failure=None,
            production_readiness={"status": "ready"},
            language_profile="en",
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["documents"]["status"], "complete")
        self.assertEqual(payload["documents"]["required"]["narrative"]["missingSections"], [])
        self.assertEqual(payload["documents"]["required"]["learning"]["missingSections"], [])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["blockers"], [])

    def test_daily_qa_report_warns_on_placeholder_section_content(self):
        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 5),
            documents=[
                {
                    "document_key": "doc:narrative",
                    "report_type": "narrative",
                    "title": "Narrative",
                    "embeddedJson": {"metrics": {}, "tasks": {}, "modelUsage": []},
                    "sections": [
                        {"heading": "天气", "bodyMarkdown": "获取失败"},
                        {"heading": "今日概要", "bodyMarkdown": "summary"},
                        {"heading": "本日统计", "bodyMarkdown": "stats"},
                        {"heading": "Agent工作", "bodyMarkdown": "work"},
                        {"heading": "定时任务情况", "bodyMarkdown": "无"},
                    ],
                },
                {
                    "document_key": "doc:technical",
                    "report_type": "technical",
                    "sections": [
                        {"heading": "工程目标与完成结果", "bodyMarkdown": "reviewed"},
                        {"heading": "阻碍、根因与弯路", "bodyMarkdown": "none"},
                        {"heading": "实现路径与关键决策", "bodyMarkdown": "path"},
                        {"heading": "验证证据", "bodyMarkdown": "evidence"},
                        {"heading": "残余风险与后续观察", "bodyMarkdown": "none"},
                        {"heading": "可沉淀经验", "bodyMarkdown": "lesson"},
                        {"heading": "Nova-Task Reconciliation Hooks", "bodyMarkdown": "none"},
                    ],
                },
                {
                    "document_key": "doc:learning",
                    "report_type": "learning",
                    "sections": [
                        {"heading": "黄金教训", "bodyMarkdown": "lesson"},
                        {"heading": "基建变动", "bodyMarkdown": "infra"},
                    ],
                },
            ],
            diary_readiness={
                "metrics": {"status": "ready", "canEnable": {"diaryMetricsSourceFoundation": True}},
                "memory": {"status": "ready", "canEnable": {"diarySnapshotSourceFoundation": True}},
                "tasks": {"status": "ready", "canEnable": {"diarySnapshotSourceFoundation": True}},
            },
            ingestion_runs=[{"id": 9, "business_date": "2026-06-05", "status": "completed"}],
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "attention")
        self.assertEqual(payload["warnings"][0]["key"], "diary-section-content-weak")
        self.assertEqual(payload["warnings"][0]["section"], "天气")
        self.assertEqual(payload["blockers"], [])
        pipeline = next(command for command in payload["repairCommands"] if command["label"] == "Run full daily pipeline")
        self.assertEqual(pipeline["actionId"], "run-full-daily-pipeline")
        self.assertEqual(pipeline["actionClass"], "heavy-llm-pipeline")
        self.assertTrue(pipeline["executionPolicy"]["dashboardExecutable"])
        self.assertTrue(pipeline["executionPolicy"]["requiresTypedConfirmation"])
        self.assertEqual(pipeline["executionPolicy"]["confirmationPhrase"], "RUN 2026-06-05")

    def test_daily_qa_repair_commands_prioritize_retry_for_diary_warning_with_latest_failure(self):
        commands = foundation_ops._daily_qa_repair_commands(
            [{"key": "daily-pipeline-latest-failure"}],
            [{"key": "diary-section-content-weak"}],
            date(2026, 6, 5),
            language_profile="en",
        )

        self.assertEqual(
            [command["actionId"] for command in commands],
            ["retry-daily-pipeline", "rematerialize-diary-markdown"],
        )
        retry = commands[0]
        self.assertEqual(retry["label"], "Retry Daily Pipeline After Inspecting Failure")
        self.assertTrue(retry["executionPolicy"]["dashboardExecutable"])
        self.assertTrue(retry["executionPolicy"]["requiresTypedConfirmation"])
        self.assertNotIn("run-full-daily-pipeline", {command["actionId"] for command in commands})

    def test_daily_qa_report_blocks_missing_sections_and_readiness(self):
        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 5),
            documents=[
                {
                    "document_key": "doc:narrative",
                    "report_type": "narrative",
                    "title": "Narrative",
                    "embeddedJson": {"metrics": {}},
                    "sections": [{"heading": "今日概要"}],
                }
            ],
            diary_readiness={
                "metrics": {
                    "status": "table_metrics_mismatch",
                    "canEnable": {"diaryMetricsSourceFoundation": False},
                }
            },
            ingestion_runs=[],
            latest_pipeline_failure={"businessDate": "2026-06-05", "failedStep": "Foundation Diary Inputs"},
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "blocked")
        keys = {item["key"] for item in payload["blockers"]}
        self.assertIn("diary-document-missing", keys)
        self.assertIn("diary-section-missing", keys)
        self.assertIn("diary-embedded-json-key-missing", keys)
        self.assertIn("foundation-diary-readiness-blocked", keys)
        self.assertIn("daily-pipeline-latest-failure", keys)
        self.assertIn("title", payload["blockers"][0])
        self.assertIn("action", payload["blockers"][0])
        self.assertFalse(any("run_foundation_shadow.py" in command["command"] for command in payload["repairCommands"]))
        self.assertFalse(any(command["actionId"] == "refresh-foundation-diary-inputs" for command in payload["repairCommands"]))
        self.assertEqual(payload["repairCommands"][0]["actionId"], "retry-daily-pipeline")
        self.assertFalse(
            any(command["actionId"] == "run-full-daily-pipeline" for command in payload["repairCommands"])
        )
        self.assertEqual(payload["repairCommands"][0]["executionPolicy"]["executionState"], "dashboard-executable")
        self.assertTrue(payload["nextActions"])

    def test_daily_qa_operator_issues_use_english_display_copy_for_english_profile(self):
        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 5),
            documents=[
                {
                    "document_key": "doc:narrative",
                    "report_type": "narrative",
                    "title": "Narrative",
                    "embeddedJson": {"metrics": {}},
                    "sections": [{"heading": "Daily Overview"}],
                }
            ],
            diary_readiness={
                "metrics": {
                    "status": "table_metrics_mismatch",
                    "canEnable": {"diaryMetricsSourceFoundation": False},
                }
            },
            ingestion_runs=[],
            latest_pipeline_failure={"businessDate": "2026-06-05", "failedStep": "Narrative Pass"},
            language_profile="en",
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "blocked")
        blockers_by_key = {}
        for item in payload["blockers"]:
            blockers_by_key.setdefault(item["key"], []).append(item)
        self.assertIn("diary-document-missing", blockers_by_key)
        self.assertIn("diary-section-missing", blockers_by_key)
        self.assertIn("foundation-diary-readiness-blocked", blockers_by_key)
        document_missing = next(item for item in blockers_by_key["diary-document-missing"] if item["reportType"] == "technical")
        section_missing = next(item for item in blockers_by_key["diary-section-missing"] if item["section"] == "Agent Work")
        self.assertEqual(document_missing["title"], "Diary artifact is missing")
        self.assertIn("missing the technical markdown document", document_missing["summary"])
        self.assertEqual(section_missing["title"], "Diary section is missing")
        self.assertIn("missing the Agent Work section", section_missing["summary"])
        self.assertEqual(blockers_by_key["foundation-diary-readiness-blocked"][0]["title"], "Foundation diary input is unavailable")
        self.assertEqual(blockers_by_key["daily-pipeline-latest-failure"][0]["title"], "Latest daily pipeline run failed")
        self.assertIn("Re-run the daily pipeline or affected pass for 2026-06-05", payload["nextActions"][0])
        pipeline = next(command for command in payload["repairCommands"] if command["actionId"] == "retry-daily-pipeline")
        self.assertEqual(pipeline["label"], "Retry Daily Pipeline After Inspecting Failure")
        self.assertEqual(pipeline["executionPolicy"]["confirmationPhrase"], "RUN 2026-06-05")
        self.assertIn("Dashboard can execute this allowlisted action", pipeline["executionPolicy"]["reason"])

    def test_daily_qa_repair_command_uses_configured_dashboard_base_url(self):
        with (
            patch.object(foundation_ops, "load_paths", return_value=object()),
            patch.object(
                foundation_ops,
                "resolve_dashboard_settings",
                return_value={"publicBaseUrl": "http://127.0.0.1:4545"},
            ),
        ):
            commands = foundation_ops._daily_qa_repair_commands(
                [{"key": "foundation-production-readiness-not-ready"}],
                [],
                date(2026, 6, 5),
            )

        command = next(item for item in commands if item["actionId"] == "inspect-production-readiness")
        self.assertIn("http://127.0.0.1:4545/api/foundation/ops/production-readiness", command["command"])
        self.assertNotIn("127.0.0.1:3036", command["command"])

    def test_daily_qa_operator_issues_explain_daily_completeness_missing_items(self):
        payload = foundation_ops.build_foundation_daily_qa_report(
            business_date=date(2026, 6, 23),
            documents=[],
            diary_readiness={},
            ingestion_runs=[{"id": 9, "business_date": "2026-06-23", "status": "completed"}],
            daily_completeness={
                "isBlankDay": False,
                "missingItems": [
                    {"key": "rag-sync", "label": "RAG sync", "action": "rag-sync"},
                ],
            },
            language_profile="zh",
            now=datetime(2026, 6, 24, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        item = next(blocker for blocker in payload["blockers"] if blocker["key"] == "daily-completeness-missing")
        self.assertEqual(item["title"], "每日完整性缺失：RAG sync")
        self.assertIn("不满足每日完整性契约", item["summary"])
        self.assertIn("历史补全", item["impact"])
        self.assertIn("运行 RAG sync", item["action"])

    def test_projection_labels_follow_english_profile_without_changing_keys(self):
        payload = foundation_ops.build_projection_completeness_matrix(
            {
                "aiAssets": {"checked": True, "ready": True, "status": "ready"},
                "periodAssets": {"checked": True, "ready": False, "status": "missing"},
            },
            language_profile="en",
        )

        rows = payload["rows"]
        self.assertEqual([row["key"] for row in rows[:2]], ["aiAssets", "periodAssets"])
        self.assertEqual([row["label"] for row in rows[:2]], ["AI Assets", "Period Assets"])
        self.assertEqual(rows[1]["status"], "missing")

    def test_daily_qa_file_stats_do_not_consume_raw_markdown_without_foundation_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / "diary-2026-06-05"
            day_dir.mkdir(parents=True)
            (day_dir / "日记-260605.md").write_text("# raw diary\n", encoding="utf-8")

            class Paths:
                diary_dir = root

            rows = foundation_ops._daily_generated_file_stats(Paths(), date(2026, 6, 5), [])

        self.assertEqual(rows, [])

    def test_daily_qa_repair_run_records_pipeline_execution_and_rerun_qa(self):
        before = {
            "status": "blocked",
            "businessDate": "2026-06-05",
            "generatedAt": "2026-06-06T05:00:00+08:00",
            "blockers": [{"key": "daily-pipeline-latest-failure"}],
            "warnings": [],
            "repairCommands": [{"actionId": "retry-daily-pipeline"}],
        }
        after = {
            "status": "ready",
            "businessDate": "2026-06-05",
            "generatedAt": "2026-06-06T05:30:00+08:00",
            "blockers": [],
            "warnings": [],
            "repairCommands": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(root / "Actanara")}),
                patch.object(foundation_ops.config, "DIARY_OUTPUT_DIR", root / "Diary"),
                patch.object(foundation_ops, "get_foundation_daily_qa", side_effect=[before, after]),
                patch(
                    "data_foundation.pipeline.run_daily_pipeline",
                    return_value=SimpleNamespace(success=True, failed_step=None),
                ) as pipeline,
            ):
                queued = foundation_ops.queue_foundation_daily_qa_repair(
                    action_id="retry-daily-pipeline",
                    business_date=date(2026, 6, 5),
                    confirmation_text="RUN 2026-06-05",
                )
                self.assertEqual(queued["status"], "queued")
                self.assertEqual(queued["run"]["actionId"], "retry-daily-pipeline")

                foundation_ops.execute_foundation_daily_qa_repair(queued["run"]["id"])
                completed = foundation_ops.get_foundation_repair_run(queued["run"]["id"])

        pipeline.assert_called_once()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["exitCode"], 0)
        self.assertEqual(completed["qaBefore"]["status"], "blocked")
        self.assertEqual(completed["qaAfter"]["status"], "ready")
        self.assertNotIn("confirmationText", completed)

    def test_daily_qa_retry_freezes_and_passes_latest_failed_pipeline_run(self):
        from data_foundation.pipeline_runs import create_pipeline_run, finish_pipeline_run

        before = {
            "status": "blocked",
            "businessDate": "2026-06-05",
            "generatedAt": "2026-06-06T05:00:00+08:00",
            "blockers": [{"key": "daily-pipeline-latest-failure"}],
            "warnings": [],
            "repairCommands": [{"actionId": "retry-daily-pipeline"}],
        }
        after = {"status": "ready", "businessDate": "2026-06-05", "repairCommands": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            parent_id = create_pipeline_run(
                paths,
                business_date="2026-06-05",
                run_kind="daily",
                requested_by="scheduler",
            )
            finish_pipeline_run(
                paths,
                parent_id,
                status="failed",
                failure_class="timeout",
                error_summary="timeout",
            )
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}),
                patch.object(foundation_ops.config, "DIARY_OUTPUT_DIR", root / "Diary"),
                patch.object(foundation_ops, "get_foundation_daily_qa", side_effect=[before, after]),
                patch(
                    "data_foundation.pipeline.run_daily_pipeline",
                    return_value=SimpleNamespace(success=True, failed_step=None),
                ) as pipeline,
            ):
                queued = foundation_ops.queue_foundation_daily_qa_repair(
                    action_id="retry-daily-pipeline",
                    business_date=date(2026, 6, 5),
                    confirmation_text="RUN 2026-06-05",
                )
                foundation_ops.execute_foundation_daily_qa_repair(queued["run"]["id"])
                completed = foundation_ops.get_foundation_repair_run(queued["run"]["id"])

        self.assertEqual(queued["run"]["qaBefore"]["sourcePipelineRunId"], parent_id)
        self.assertEqual(completed["qaBefore"]["sourcePipelineRunId"], parent_id)
        self.assertEqual(pipeline.call_args.kwargs["retry_of_run_id"], parent_id)

    def test_daily_qa_repair_rejects_unrecommended_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(root / "Actanara")}),
                patch.object(foundation_ops.config, "DIARY_OUTPUT_DIR", root / "Diary"),
                patch.object(
                    foundation_ops,
                    "get_foundation_daily_qa",
                    return_value={"status": "ready", "businessDate": "2026-06-05", "repairCommands": []},
                ),
            ):
                with self.assertRaises(ValueError):
                    foundation_ops.queue_foundation_daily_qa_repair(
                        action_id="retry-daily-pipeline",
                        business_date=date(2026, 6, 5),
                        confirmation_text="RUN 2026-06-05",
                    )

    def test_daily_pipeline_summary_reports_files_lessons_and_task_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-06-05"
            day.mkdir(parents=True)
            (day / "日记-260605.md").write_text(
                "# 2026年06月05日 日记\n\n## 今日概要\n\n* **Foundation 运维**：完成 Daily QA 修复入口。\n",
                encoding="utf-8",
            )
            (day / "技术进展-260605.md").write_text(
                "# 技术进展\n\n## 一、工程目标与完成结果\n\n完成 Daily QA 修复入口。\n\n## 七、Nova-Task Reconciliation Hooks\n\n无\n",
                encoding="utf-8",
            )
            (day / "智慧沉淀-260605.md").write_text(
                """# 智慧沉淀

## 🧠 黄金教训 (Lessons)
- **【codex】**: Dashboard 执行入口必须服务端解析 action。解决建议：保留 allowlist。
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara").home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            run_id = begin_ingestion_run(
                paths,
                trigger_type="pipeline-foundation-materialization",
                business_date=date(2026, 6, 5),
                status="running",
            )
            materialize_diary_markdown_day(paths, date(2026, 6, 5), source_run_id=run_id)
            finish_ingestion_run(paths, run_id, status="completed")
            create_task_node(paths, node_id="NT-OPS", title="Foundation Ops", actor="operator")
            ingest_nova_task_evidence(
                paths,
                markdown="""# Technical report

## 四、Nova-Task Evidence
```yaml
nova_task:
  date: "2026-06-05"
  matched_tasks:
    - task_id: "NT-OPS"
      confidence: high
      event_type: progress
      summary: "Added Daily QA repair run"
      evidence: ["line a"]
  candidate_subtasks:
    - proposed_parent_task_id: "NT-OPS"
      proposed_title: "Add pipeline summary panel"
      reason: "Ops needs daily metrics"
      evidence: ["line b"]
```
""",
                business_date=date(2026, 6, 5),
                source_path=day / "技术进展-260605.md",
            )

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                summary = foundation_ops.get_foundation_daily_pipeline_summary(
                    business_date=date(2026, 6, 5),
                    limit=20,
                )

        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["latestMaterializationRun"]["id"], run_id)
        self.assertEqual(summary["documents"]["count"], 3)
        self.assertGreater(summary["documents"]["totalBytes"], 0)
        self.assertEqual(summary["lessons"]["count"], 1)
        self.assertEqual(summary["lessons"]["items"][0]["agent"], "codex")
        self.assertEqual(summary["tasks"]["eventCount"], 2)
        self.assertEqual(summary["tasks"]["matchedUpdates"], 2)
        self.assertEqual(summary["tasks"]["candidateCount"], 0)

    def test_daily_pipeline_summary_marks_no_activity_day_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day.mkdir(parents=True)
            (day / "日记-260620-no-activity.md").write_text(
                """# 2026年06月20日 日记

## 今日概要
今日无活动

```json
{"date": "2026-06-20", "activityState": "empty", "metrics": {}, "cronTasks": []}
```
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara").home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            blank_inputs = begin_ingestion_run(
                paths,
                trigger_type="pipeline-blank-day-inputs",
                business_date=date(2026, 6, 20),
                status="running",
            )
            finish_ingestion_run(paths, blank_inputs, status="completed")
            blank_materialization = begin_ingestion_run(
                paths,
                trigger_type="pipeline-blank-day-materialization",
                business_date=date(2026, 6, 20),
                status="running",
            )
            materialize_diary_markdown_day(paths, date(2026, 6, 20), source_run_id=blank_materialization)
            finish_ingestion_run(paths, blank_materialization, status="completed")

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}):
                with patch(
                    "data_foundation.pipeline.latest_pipeline_failure",
                    return_value={
                        "businessDate": "2026-06-20",
                        "createdAt": "2026-06-22T10:00:00+08:00",
                        "failedStep": "Foundation Diary Inputs",
                    },
                ):
                    summary = foundation_ops.get_foundation_daily_pipeline_summary(
                        business_date=date(2026, 6, 20),
                        limit=20,
                    )

        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["activityState"], "empty")
        self.assertIsNone(summary["latestPipelineFailure"])
        self.assertEqual(summary["latestBlankInputsRun"]["id"], blank_inputs)
        self.assertEqual(summary["latestMaterializationRun"]["id"], blank_materialization)
        self.assertEqual(summary["documents"]["count"], 1)
        self.assertTrue(summary["tasks"]["skipped"])
        self.assertEqual(summary["tasks"]["eventCount"], 0)
        self.assertEqual(summary["tasks"]["matchedUpdates"], 0)

    def test_daily_pipeline_summary_uses_no_activity_filename_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026" / "diary-2026-06" / "06-21"
            day.mkdir(parents=True)
            (day / "日记-260621-no-activity.md").write_text(
                """# 2026年06月21日 日记

## 今日概要
今日无活动
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara").home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            blank_materialization = begin_ingestion_run(
                paths,
                trigger_type="pipeline-blank-day-materialization",
                business_date=date(2026, 6, 21),
                status="running",
            )
            materialize_diary_markdown_day(paths, date(2026, 6, 21), source_run_id=blank_materialization)
            finish_ingestion_run(paths, blank_materialization, status="completed")

            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}), patch(
                "data_foundation.pipeline.latest_pipeline_failure",
                return_value=None,
            ):
                summary = foundation_ops.get_foundation_daily_pipeline_summary(
                    business_date=date(2026, 6, 21),
                    limit=20,
                )

        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["activityState"], "empty")
        self.assertTrue(summary["tasks"]["skipped"])
        self.assertEqual(summary["documents"]["files"][0]["relativePath"], "diary-2026/diary-2026-06/06-21/日记-260621-no-activity.md")

    def test_daily_qa_overview_summarizes_recent_days(self):
        overview = foundation_ops.build_foundation_daily_qa_overview(
            start_date=date(2026, 6, 3),
            days=3,
            reports=[
                {
                    "businessDate": "2026-06-03",
                    "status": "ready",
                    "blockers": [],
                    "warnings": [],
                    "documents": {"status": "complete", "count": 3},
                    "diaryReadiness": {"metrics": {"ready": True}, "memory": {"ready": True}, "tasks": {"ready": True}},
                    "foundationIngestion": {"latestForDate": {"id": 1}},
                },
                {
                    "businessDate": "2026-06-04",
                    "status": "attention",
                    "blockers": [],
                    "warnings": [{"key": "warning"}],
                    "documents": {"status": "complete", "count": 3},
                    "diaryReadiness": {"metrics": {"ready": True}, "memory": {"ready": True}, "tasks": {"ready": False}},
                    "foundationIngestion": {"latestForDate": {"id": 2}},
                },
                {
                    "businessDate": "2026-06-05",
                    "status": "blocked",
                    "blockers": [{"key": "blocked"}],
                    "warnings": [],
                    "documents": {"status": "incomplete", "count": 1},
                    "diaryReadiness": {"metrics": {"ready": False}, "memory": {"ready": False}, "tasks": {"ready": False}},
                    "foundationIngestion": {"latestForDate": None},
                },
            ],
            now=datetime(2026, 6, 6, 5, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(overview["status"], "blocked")
        self.assertEqual(overview["counts"], {"ready": 1, "attention": 1, "blocked": 1, "unknown": 0})
        self.assertEqual(overview["rows"][1]["foundationInputsReady"], 2)
        self.assertEqual(overview["rows"][2]["blockers"], 1)

    def test_production_readiness_blockers_include_operator_copy(self):
        payload = foundation_ops.build_foundation_production_readiness(
            readiness={
                "configuredSourcesValid": True,
                "aiAssets": {"ready": True},
                "periodAssets": {"ready": False},
                "periodPage": {"ready": True},
            },
            refresh_jobs={"jobs": [{"id": 2, "status": "failed"}], "latestFailed": {"id": 2, "status": "failed"}},
            scheduler_status={"enabled": True, "systemTimer": {"supported": True, "registered": True}},
            runtime_sources={"DASHBOARD_READ_SOURCE": "foundation", "REPORT_READ_SOURCE": "foundation"},
            now=datetime(2026, 5, 29, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong")),
        )

        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["operatorFindings"])
        self.assertIn("title", payload["operatorFindings"][0])
        self.assertIn("summary", payload["operatorFindings"][0])
        self.assertIn("action", payload["operatorFindings"][0])

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_router_validates_and_passes_period(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_snapshot_operations", return_value={"ok": True}) as snapshot:
            response = asyncio.run(ops_router.api_foundation_snapshot_operations("2026-05-25", 5, 10))

        self.assertEqual(response, {"ok": True})
        snapshot.assert_called_once()
        self.assertEqual(snapshot.call_args.kwargs["period_start"].isoformat(), "2026-05-25")
        self.assertEqual(snapshot.call_args.kwargs["period_days"], 5)
        self.assertEqual(snapshot.call_args.kwargs["limit"], 10)

        invalid = asyncio.run(ops_router.api_foundation_snapshot_operations("2026-05-25", 0, 10))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Invalid request", json.loads(invalid.body)["error"])

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_production_readiness_router_validates_and_passes_period(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_foundation_production_readiness", return_value={"ok": True}) as readiness:
            response = asyncio.run(ops_router.api_foundation_production_readiness("2026-05-25", 5, 10))

        self.assertEqual(response, {"ok": True})
        readiness.assert_called_once()
        self.assertEqual(readiness.call_args.kwargs["period_start"].isoformat(), "2026-05-25")
        self.assertEqual(readiness.call_args.kwargs["period_days"], 5)
        self.assertEqual(readiness.call_args.kwargs["limit"], 10)

        invalid = asyncio.run(ops_router.api_foundation_production_readiness("2026-05-25", 0, 10))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Invalid request", json.loads(invalid.body)["error"])

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_daily_qa_router_validates_and_passes_date(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_foundation_daily_qa", return_value={"ok": True}) as qa:
            response = asyncio.run(ops_router.api_foundation_daily_qa("2026-06-05", 10))

        self.assertEqual(response, {"ok": True})
        qa.assert_called_once()
        self.assertEqual(qa.call_args.kwargs["business_date"].isoformat(), "2026-06-05")
        self.assertEqual(qa.call_args.kwargs["limit"], 10)

        invalid = asyncio.run(ops_router.api_foundation_daily_qa("2026-06-05", 0))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Invalid request", json.loads(invalid.body)["error"])

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_daily_qa_overview_router_validates_and_passes_range(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_foundation_daily_qa_overview", return_value={"ok": True}) as overview:
            response = asyncio.run(ops_router.api_foundation_daily_qa_overview("2026-06-05", 7, 10))

        self.assertEqual(response, {"ok": True})
        overview.assert_called_once()
        self.assertEqual(overview.call_args.kwargs["end_date"].isoformat(), "2026-06-05")
        self.assertEqual(overview.call_args.kwargs["days"], 7)
        self.assertEqual(overview.call_args.kwargs["limit"], 10)

        invalid = asyncio.run(ops_router.api_foundation_daily_qa_overview("2026-06-05", 0, 10))
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Invalid request", json.loads(invalid.body)["error"])

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_daily_pipeline_summary_router_validates_and_passes_date(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_foundation_daily_pipeline_summary", return_value={"ok": True}) as summary:
            response = asyncio.run(ops_router.api_foundation_daily_pipeline_summary("2026-06-05", 10))

        self.assertEqual(response, {"ok": True})
        summary.assert_called_once()
        self.assertEqual(summary.call_args.kwargs["business_date"], date(2026, 6, 5))
        self.assertEqual(summary.call_args.kwargs["limit"], 10)

        invalid = asyncio.run(ops_router.api_foundation_daily_pipeline_summary("2026-06-05", 0))
        self.assertEqual(invalid.status_code, 400)

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_daily_qa_repair_router_queues_background_task(self):
        from fastapi import BackgroundTasks
        from app.routers import foundation_ops as ops_router

        tasks = BackgroundTasks()
        with (
            patch.object(
                ops_router.foundation_ops,
                "queue_foundation_daily_qa_repair",
                return_value={"status": "queued", "run": {"id": 77, "actionId": "retry-daily-pipeline"}},
            ) as queue,
            patch.object(ops_router.foundation_ops, "execute_foundation_daily_qa_repair") as execute,
        ):
            response = asyncio.run(
                ops_router.api_foundation_daily_qa_repair_run(
                    tasks,
                    {
                        "actionId": "retry-daily-pipeline",
                        "businessDate": "2026-06-05",
                        "confirmationText": "RUN 2026-06-05",
                    },
                )
            )

        self.assertEqual(response.status_code, 202)
        queue.assert_called_once()
        self.assertEqual(queue.call_args.kwargs["action_id"], "retry-daily-pipeline")
        self.assertEqual(queue.call_args.kwargs["business_date"], date(2026, 6, 5))
        self.assertEqual(json.loads(response.body)["run"]["actionId"], "retry-daily-pipeline")
        asyncio.run(tasks())
        execute.assert_called_once_with(77)


if __name__ == "__main__":
    unittest.main()
