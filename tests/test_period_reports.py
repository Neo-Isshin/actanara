import os
import asyncio
import importlib.machinery
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

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    fastapi_stub = types.ModuleType("fastapi")
    responses_stub = types.ModuleType("fastapi.responses")
    fastapi_stub.__spec__ = importlib.machinery.ModuleSpec("fastapi", loader=None)
    responses_stub.__spec__ = importlib.machinery.ModuleSpec("fastapi.responses", loader=None)

    class _Router:
        def _decorator(self, *args, **kwargs):
            return lambda func: func

        def get(self, *args, **kwargs):
            return self._decorator(*args, **kwargs)

        def post(self, *args, **kwargs):
            return self._decorator(*args, **kwargs)

        def patch(self, *args, **kwargs):
            return self._decorator(*args, **kwargs)

        def put(self, *args, **kwargs):
            return self._decorator(*args, **kwargs)

        def delete(self, *args, **kwargs):
            return self._decorator(*args, **kwargs)

    class _BackgroundTasks:
        def __init__(self):
            self.tasks = []

        def add_task(self, *args, **kwargs):
            self.tasks.append((args, kwargs))

        async def __call__(self):
            for args, kwargs in self.tasks:
                func, *func_args = args
                result = func(*func_args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result

    class _JSONResponse(dict):
        def __init__(self, content, status_code=200):
            super().__init__(content)
            self.body = json.dumps(content).encode("utf-8")
            self.status_code = status_code

    class _StreamingResponse:
        def __init__(self, content=None, status_code=200, **kwargs):
            self.content = content
            self.status_code = status_code
            self.kwargs = kwargs

    fastapi_stub.APIRouter = _Router
    fastapi_stub.BackgroundTasks = _BackgroundTasks
    fastapi_stub.Request = object
    responses_stub.JSONResponse = _JSONResponse
    responses_stub.StreamingResponse = _StreamingResponse
    sys.modules["fastapi"] = fastapi_stub
    sys.modules["fastapi.responses"] = responses_stub

from app.services import ai_assets, diary
from app.routers import diary as diary_router
from data_foundation.db import connect, migrate
from data_foundation.diary_markdown import DIARY_PERIOD_PAGE_PROJECTION, materialize_diary_markdown_day
from data_foundation.jobs import begin_ingestion_run
from data_foundation.paths import initialize_home
from data_foundation.period_summary import DIARY_PERIOD_SUMMARY_PROJECTION
from data_foundation.reports import materialize_legacy_asset_projection, read_period_projection, write_period_projection
from data_foundation.settings import write_settings
from data_foundation.snapshots import write_rag_daily_status_snapshot
from data_foundation.nova_task import create_task_node


def _fixture_metrics() -> dict:
    return {
        "parsedDays": 7,
        "kpi": {
            "totalTokens": 42,
            "totalMessages": 3,
            "totalApiCalls": 2,
            "activeSessions": 1,
            "totalSessions": 1,
            "cacheHitRate": 50.0,
            "cronSuccessRate": 100.0,
            "agentCount": 1,
        },
        "dailyTokenSeries": [{"date": "2026-05-13", "displayDate": "05-13", "tokens": 42, "messages": 3, "cacheHitRate": 50.0}],
        "agentActivity": {"fixture": {"messages": 3, "tokens": 42, "days_active": 1, "total_days": 7, "active_rate": 14.3}},
        "cronStats": {"success": 1, "failed": 0, "rate": 100.0},
        "topTopics": [{"topic": "fixture topic", "count": 1}],
        "workspaceUsage": [{"name": "fixture", "tokens": 42, "messages": 1, "days_active": 1, "total_days": 7}],
        "models": [{"name": "fixture-model", "tokens": 42}],
        "assetHourlyHeatmap": {"dates": ["2026-05-13"], "periods": []},
        "memoryStats": {"sessionFiles": 11, "totalSizeMB": 12.5},
        "knowledgePeriodMemoryCurrent": {"sessionFiles": 7, "totalSizeMB": 8.5},
    }


class PeriodReportProjectionTests(unittest.TestCase):
    def _home(self, root: Path):
        paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "legacy")
        migrate(paths)
        run_id = begin_ingestion_run(paths, trigger_type="fixture", business_date=date(2026, 5, 19))
        return paths, run_id

    def test_legacy_period_projection_round_trips_without_live_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, run_id = self._home(Path(tmp))
            start = date(2026, 5, 13)
            end = date(2026, 5, 19)
            materialize_legacy_asset_projection(
                paths,
                start,
                end,
                run_id,
                builder=lambda selected_start, days: _fixture_metrics(),
            )
            projection = read_period_projection(paths, start, end)
            self.assertEqual(projection["metrics"], _fixture_metrics())
            self.assertEqual(projection["projectionType"], "legacy-dashboard-assets-v1")

    def test_current_week_partial_projection_is_labeled_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, run_id = self._home(Path(tmp))
            write_period_projection(
                paths,
                date(2026, 5, 11),
                date(2026, 5, 13),
                _fixture_metrics(),
                source_run_id=run_id,
            )
            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    """
                    SELECT period_type
                    FROM period_reports
                    WHERE report_key = ?
                    """,
                    ("legacy-dashboard-assets-v1:2026-05-11:2026-05-13",),
                ).fetchone()
            self.assertEqual(row["period_type"], "week")

    def test_current_month_partial_projection_is_labeled_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, run_id = self._home(Path(tmp))
            write_period_projection(
                paths,
                date(2026, 5, 1),
                date(2026, 5, 13),
                _fixture_metrics(),
                source_run_id=run_id,
            )
            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    """
                    SELECT period_type
                    FROM period_reports
                    WHERE report_key = ?
                    """,
                    ("legacy-dashboard-assets-v1:2026-05-01:2026-05-13",),
                ).fetchone()
            self.assertEqual(row["period_type"], "month")

    def test_non_rag_period_builder_does_not_read_rag_stats(self):
        with (
            patch.object(diary, "_get_rag_memory_stats", return_value=({}, {"sessionFiles": 4, "totalSizeMB": 5.0})) as stats,
            patch.object(diary, "_period_diary_rollup", return_value={"kpi": {"totalTokens": 0}}) as rollup,
            patch.object(diary, "_period_asset_breakdown", return_value={}),
            patch.object(diary, "_session_memory_stats", return_value={"sessionFiles": 6, "totalSizeMB": 7.0}),
        ):
            projection = diary._period_non_rag_asset_projection(date(2026, 5, 13), 7)
        self.assertEqual(projection["memoryStats"]["sessionFiles"], 4)
        stats.assert_called_once_with(include_rag=False)
        rollup.assert_called_once_with(date(2026, 5, 13), 7, include_prior_baseline=False)

    def test_non_rag_period_builder_does_not_scan_prior_markdown_baseline(self):
        with (
            patch.object(diary, "_get_rag_memory_stats", return_value=({}, {"sessionFiles": 4, "totalSizeMB": 5.0})),
            patch.object(diary, "_period_asset_breakdown", return_value={}),
            patch.object(diary, "_session_memory_stats", return_value={"sessionFiles": 6, "totalSizeMB": 7.0}),
            patch.object(diary, "_prior_knowledge_snapshot", side_effect=AssertionError("prior markdown baseline called")),
            patch.object(diary, "parse_diary", return_value=None),
        ):
            projection = diary._period_non_rag_asset_projection(date(2026, 5, 13), 7)

        self.assertFalse(projection["knowledgePeriod"]["rag"]["deltaAvailable"])
        self.assertFalse(projection["knowledgePeriod"]["memory"]["deltaAvailable"])

    def test_period_asset_breakdown_uses_incremental_usage_cache_groups(self):
        cached_entries = {
            "Codex": [
                {
                    "timestamp": "2026-06-22T12:00:00Z",
                    "input": 20_000_000,
                    "output": 1,
                    "cacheRead": 0,
                    "message_count": 2,
                    "usageGroup": "nova-diary-v2",
                },
                {
                    "timestamp": "2026-06-22T12:00:00Z",
                    "input": 30_000_000,
                    "output": 1,
                    "cacheRead": 0,
                    "message_count": 3,
                    "usageGroup": "home",
                },
            ],
            "Gemini CLI": [
                {
                    "timestamp": "2026-06-23T12:00:00Z",
                    "input": 25_000_000,
                    "output": 1,
                    "cacheRead": 0,
                    "message_count": 1,
                    "usageGroup": "nova-diary-assets",
                }
            ],
        }
        with (
            patch.object(ai_assets, "_scan_usage_incremental", return_value=(cached_entries, {}, {})) as scan,
            patch.object(ai_assets, "_scan_all_hermes", return_value=([], 0)),
            patch.object(ai_assets, "_ALL_SCANNERS", [("Codex", lambda: (_ for _ in ()).throw(AssertionError("legacy scanner called")))]),
        ):
            projection = diary._period_asset_breakdown(date(2026, 6, 22), 7)

        rows = projection["workspaceUsage"]
        self.assertEqual([row["name"] for row in rows], ["nova-diary-assets", "open-nova"])
        self.assertEqual([row["tool"] for row in rows], ["Gemini CLI", "Codex"])
        self.assertEqual([row["days_active"] for row in rows], [1, 1])
        self.assertNotIn("home", [row["name"] for row in rows])
        scan.assert_called_once_with()

    def test_foundation_report_source_uses_materialized_asset_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            create_task_node(paths, node_id="NT-ACTIVE", title="Foundation active", actor="test")
            create_task_node(paths, node_id="NT-DONE", title="Foundation done", status="completed", actor="test")
            start = date(2026, 5, 13)
            materialize_legacy_asset_projection(
                paths,
                start,
                date(2026, 5, 19),
                run_id,
                builder=lambda selected_start, days: _fixture_metrics(),
            )
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_period_asset_breakdown", side_effect=AssertionError("live scanner called")),
                patch.object(diary, "_session_memory_stats", side_effect=AssertionError("live memory scanner called")),
                patch.object(diary, "_get_rag_memory_stats", return_value=({"entries": 11, "sizeMB": 1.2}, {})) as rag_stats,
                patch.object(diary, "parse_diary", side_effect=AssertionError("daily diary parser called")),
            ):
                report = diary.generate_weekly_report(7, start.isoformat(), include_assets=True)
            self.assertEqual(report["workspaceUsage"], _fixture_metrics()["workspaceUsage"])
            self.assertEqual(report["memoryStats"], _fixture_metrics()["memoryStats"])
            self.assertEqual(report["ragStats"]["entries"], 11)
            self.assertEqual(report["knowledgePeriod"]["rag"]["currentCount"], 11)
            self.assertEqual(report["knowledgePeriod"]["memory"]["currentCount"], 7)
            self.assertEqual(report["kpi"], _fixture_metrics()["kpi"])
            self.assertEqual(report["dailyTokenSeries"], _fixture_metrics()["dailyTokenSeries"])
            self.assertEqual(report["cronStats"], _fixture_metrics()["cronStats"])
            self.assertEqual(report["highFrequencyTopics"], [])
            self.assertEqual(report["topTopics"], [])
            self.assertEqual(report["taskStats"]["source"], "foundation")
            self.assertEqual(report["taskStats"]["authority"], "Nova-Task v2 SQLite")
            self.assertEqual(report["taskStats"]["completed"], 1)
            self.assertEqual(report["taskStats"]["inProgress"], 1)
            self.assertEqual(report["dataFreshness"]["periodAssets"]["source"], "foundation")
            self.assertEqual(report["dataFreshness"]["periodAssets"]["memorySource"], "foundation")
            rag_stats.assert_called_once_with(include_memory=False)

    def test_foundation_report_uses_daily_rag_snapshot_and_previous_kpi_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            current_start = date(2026, 5, 13)
            current_end = date(2026, 5, 19)
            previous_start = date(2026, 5, 6)
            previous_end = date(2026, 5, 12)
            current_metrics = _fixture_metrics()
            previous_metrics = _fixture_metrics()
            previous_metrics["kpi"] = {
                **previous_metrics["kpi"],
                "totalTokens": 21,
                "totalMessages": 1,
                "cacheHitRate": 25.0,
            }
            materialize_legacy_asset_projection(
                paths,
                previous_start,
                previous_end,
                run_id,
                builder=lambda selected_start, days: previous_metrics,
            )
            materialize_legacy_asset_projection(
                paths,
                current_start,
                current_end,
                run_id,
                builder=lambda selected_start, days: current_metrics,
            )
            write_rag_daily_status_snapshot(paths, previous_end, {"entries": 8, "sizeMB": 1.0}, source_run_id=run_id)
            write_rag_daily_status_snapshot(paths, current_end, {"entries": 13, "sizeMB": 1.8}, source_run_id=run_id)
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "_get_rag_memory_stats", return_value=({"entries": 99, "sizeMB": 9.9}, {})),
                patch.object(diary, "parse_diary", side_effect=AssertionError("daily diary parser called")),
            ):
                report = diary.generate_weekly_report(7, current_start.isoformat(), include_assets=True)

            self.assertEqual(report["knowledgePeriod"]["rag"]["currentCount"], 13)
            self.assertEqual(report["knowledgePeriod"]["rag"]["deltaCount"], 5)
            self.assertEqual(report["knowledgePeriod"]["rag"]["deltaSizeMB"], 0.8)
            self.assertEqual(report["workloadComparison"]["totalTokens"]["previous"], 21)
            self.assertEqual(report["workloadComparison"]["totalTokens"]["delta"], 21)
            self.assertEqual(report["workloadComparison"]["totalMessages"]["delta"], 2)
            self.assertEqual(report["workloadComparison"]["cacheHitRate"]["delta"], 25.0)

    def test_foundation_report_source_uses_materialized_period_page_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            start = date(2026, 5, 13)
            write_period_projection(
                paths,
                start,
                date(2026, 5, 19),
                {
                    "summaryTopics": [{"date": "2026-05-13", "title": "Foundation topic", "items": ["snapshot item"]}],
                    "lessons": [{"date": "2026-05-13", "agent": "codex", "problem": "snapshot", "suggestion": "use projection"}],
                },
                source_run_id=run_id,
                projection_type=DIARY_PERIOD_PAGE_PROJECTION,
            )
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_get_rag_memory_stats", return_value=({}, {})),
            ):
                report = diary.generate_weekly_report(7, start.isoformat(), include_assets=False)
            self.assertEqual(report["summaryTopics"][0]["title"], "Foundation topic")
            self.assertEqual(report["lessons"][0]["agent"], "codex")
            self.assertEqual(report["dataFreshness"]["periodPage"]["source"], "foundation")
            self.assertEqual(report["dataFreshness"]["periodSummary"]["source"], "snapshot-missing")

    def test_foundation_report_source_uses_materialized_period_summary_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            start = date(2026, 5, 13)
            write_period_projection(
                paths,
                start,
                date(2026, 5, 19),
                {
                    "summary": {
                        "title": "本周总结",
                        "lead": "Foundation generated summary",
                        "highlights": [],
                        "lessons": [],
                        "markdown": "## 本周总结",
                        "highFrequencyTopics": [
                            {"topic": "Runtime hardening", "count": 4, "reason": "Multiple foundation fixes"}
                        ],
                    },
                    "highFrequencyTopics": [
                        {"topic": "Runtime hardening", "count": 4, "reason": "Multiple foundation fixes"}
                    ],
                },
                source_run_id=run_id,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_get_rag_memory_stats", return_value=({}, {})),
            ):
                report = diary.generate_weekly_report(7, start.isoformat(), include_assets=False)
            self.assertEqual(report["periodSummary"]["lead"], "Foundation generated summary")
            self.assertEqual(report["highFrequencyTopics"][0]["topic"], "Runtime hardening")
            self.assertEqual(report["topTopics"], report["highFrequencyTopics"])
            self.assertEqual(report["dataFreshness"]["periodSummary"]["source"], "foundation")

    def test_report_source_reads_generated_period_summary_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            start = date(2026, 5, 13)
            write_period_projection(
                paths,
                start,
                date(2026, 5, 19),
                {"summary": {"title": "本周总结", "lead": "Button generated summary", "highlights": [], "lessons": []}},
                source_run_id=run_id,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_get_rag_memory_stats", return_value=({}, {})),
            ):
                report = diary.generate_weekly_report(7, start.isoformat(), include_assets=False)
            self.assertEqual(report["periodSummary"]["lead"], "Button generated summary")
            self.assertEqual(report["dataFreshness"]["periodSummary"]["source"], "foundation")

    def test_retired_legacy_report_source_uses_foundation_without_markdown_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            start = date(2026, 5, 13)
            write_period_projection(
                paths,
                start,
                date(2026, 5, 19),
                {"summary": {"title": "本周总结", "lead": "Foundation source", "highlights": [], "lessons": []}},
                source_run_id=run_id,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )
            write_settings({"runtimeSources": {"reportReadSource": "legacy"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "parse_diary", side_effect=AssertionError("daily diary parser called")),
                patch.object(diary, "_period_asset_breakdown", side_effect=AssertionError("legacy asset scanner called")),
            ):
                report = diary.generate_weekly_report(7, start.isoformat(), include_assets=True)
            self.assertEqual(report["periodSummary"]["lead"], "Foundation source")
            self.assertEqual(report["dataFreshness"]["reportReadSource"]["retiredSourceRequested"], "legacy")
            self.assertEqual(report["dataFreshness"]["reportReadSource"]["source"], "foundation")

    def test_missing_foundation_projection_reports_refresh_without_live_asset_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, _ = self._home(root)
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_period_asset_breakdown", side_effect=AssertionError("live scanner called")),
                patch.object(diary, "parse_diary", side_effect=AssertionError("daily diary parser called")),
                patch.object(diary, "_get_rag_memory_stats", return_value=({"entries": 11, "sizeMB": 1.2}, {})) as rag_stats,
            ):
                report = diary.generate_weekly_report(7, "2026-05-13", include_assets=True)
            self.assertEqual(report["ragStats"]["entries"], 11)
            self.assertEqual(report["knowledgePeriod"]["rag"]["currentCount"], 11)
            self.assertEqual(report["dataFreshness"]["periodAssets"]["source"], "snapshot-missing")
            self.assertEqual(report["dataFreshness"]["periodPage"]["source"], "snapshot-missing")
            self.assertEqual(report["dataFreshness"]["periodSummary"]["source"], "snapshot-missing")
            self.assertEqual(report["dataFreshness"]["periodAssets"]["status"], "projection_missing")
            self.assertTrue(report["dataFreshness"]["periodAssets"]["refreshRequired"])
            self.assertEqual(report["dataFreshness"]["periodAssets"]["refreshPolicy"], "historical-manual-rebuild")
            rag_stats.assert_called_once_with(include_memory=False)

    def test_missing_current_period_projection_reports_current_refresh_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, _ = self._home(root)
            today = date.today()
            start = today.replace(day=1)
            days = (today - start).days + 1
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_period_asset_breakdown", side_effect=AssertionError("live scanner called")),
            ):
                report = diary.generate_weekly_report(days, start.isoformat(), include_assets=True)
            self.assertEqual(report["dataFreshness"]["periodAssets"]["status"], "projection_missing")
            self.assertEqual(report["dataFreshness"]["periodAssets"]["refreshPolicy"], "current-period-refresh")
            self.assertEqual(report["dataFreshness"]["periodSummary"]["refreshPolicy"], "current-period-refresh")

    def test_older_projection_without_memory_fields_reports_missing_without_legacy_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, run_id = self._home(root)
            metrics = _fixture_metrics()
            metrics.pop("memoryStats")
            metrics.pop("knowledgePeriodMemoryCurrent")
            materialize_legacy_asset_projection(
                paths,
                date(2026, 5, 13),
                date(2026, 5, 19),
                run_id,
                builder=lambda selected_start, days: metrics,
            )
            write_settings({"runtimeSources": {"reportReadSource": "foundation"}}, paths)
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch.object(diary, "SESSION_DIR", root / "empty-sessions"),
                patch.object(diary, "_get_rag_memory_stats", return_value=({"entries": 11, "sizeMB": 1.2}, {})),
                patch.object(diary, "_session_memory_stats", side_effect=AssertionError("legacy memory scanner called")),
                patch.object(diary, "_prior_knowledge_snapshot", side_effect=AssertionError("prior markdown baseline called")),
                patch.object(diary, "parse_diary", side_effect=AssertionError("daily diary parser called")),
            ):
                report = diary.generate_weekly_report(7, "2026-05-13", include_assets=True)
            self.assertEqual(report["memoryStats"]["sessionFiles"], 0)
            self.assertEqual(report["knowledgePeriod"]["rag"]["currentCount"], 11)
            self.assertFalse(report["knowledgePeriod"]["rag"]["deltaAvailable"])
            self.assertEqual(report["knowledgePeriod"]["memory"]["currentCount"], 0)
            self.assertEqual(report["dataFreshness"]["periodAssets"]["memorySource"], "snapshot-missing")

    def test_report_list_and_detail_use_foundation_documents_not_raw_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            report_dir = paths.diary_dir / "diary-2026-05-19"
            report_dir.mkdir(parents=True)
            report = report_dir / "智慧沉淀-260519.md"
            report.write_text("# runtime report\n\n## Lessons\nFoundation document content\n", encoding="utf-8")

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                reports = asyncio.run(diary_router.api_report_list())
                missing = asyncio.run(diary_router.api_report_detail("report-2026-05-19"))

            self.assertEqual(reports, [])
            self.assertEqual(missing.status_code, 404)

            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                reports = asyncio.run(diary_router.api_report_list())
                detail = asyncio.run(diary_router.api_report_detail("report-2026-05-19"))

            self.assertEqual(reports[0]["path"], "diary-2026-05-19/智慧沉淀-260519.md")
            self.assertEqual(reports[0]["source"], "foundation-diary-markdown-documents")
            self.assertEqual(detail["source"], "foundation-diary-markdown-documents")
            self.assertIn("Foundation document content", detail["content"])

    def test_english_learning_report_fallback_title_uses_pipeline_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            migrate(paths)
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            report_dir = paths.diary_dir / "diary-2026-05-19"
            report_dir.mkdir(parents=True)
            report = report_dir / "learning-260519.md"
            report.write_text("## Lessons\nFoundation document content\n", encoding="utf-8")
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                reports = asyncio.run(diary_router.api_report_list())
                detail = asyncio.run(diary_router.api_report_detail("report-2026-05-19"))

            self.assertEqual(reports[0]["title"], "2026-05-19 Learning and Infrastructure Audit")
            self.assertEqual(detail["title"], "2026-05-19 Learning and Infrastructure Audit")
            self.assertEqual(reports[0]["path"], "diary-2026-05-19/learning-260519.md")


if __name__ == "__main__":
    unittest.main()
