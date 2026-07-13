import importlib
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import config
from agentic_rag.rag_settings import resolve_rag_settings
from app.services import diary as dashboard_diary
from app.services import foundation as dashboard_foundation
from data_foundation.db import migrate
from data_foundation.nova_task import create_task_node
from data_foundation.paths import initialize_home
from data_foundation.pipeline import PRODUCTION_STEPS, PipelineStep, run_daily_pipeline
from data_foundation.settings import read_settings


class ProductReleaseContractTests(unittest.TestCase):
    def test_checked_in_foundation_defaults_are_foundation_only(self):
        watched = (
            "NOVA_DATA_FOUNDATION_ENABLED",
            "DASHBOARD_READ_SOURCE",
            "REPORT_READ_SOURCE",
            "DIARY_METRICS_SOURCE",
            "DIARY_MEMORY_SOURCE",
            "DIARY_TASKS_SOURCE",
            "TASK_AUDIT_SINK",
        )
        original_env = {key: config.os.environ.get(key) for key in watched}
        try:
            with patch.dict(config.os.environ, {}, clear=True):
                reloaded = importlib.reload(config)
                self.assertTrue(reloaded.NOVA_DATA_FOUNDATION_ENABLED)
                self.assertEqual(reloaded.DASHBOARD_READ_SOURCE, "foundation")
                self.assertEqual(reloaded.REPORT_READ_SOURCE, "foundation")
                self.assertEqual(reloaded.DIARY_METRICS_SOURCE, "foundation")
                self.assertEqual(reloaded.DIARY_MEMORY_SOURCE, "foundation")
                self.assertEqual(reloaded.DIARY_TASKS_SOURCE, "foundation")
                self.assertEqual(reloaded.TASK_AUDIT_SINK, "foundation")
        finally:
            restored = {key: value for key, value in original_env.items() if value is not None}
            with patch.dict(config.os.environ, restored, clear=False):
                importlib.reload(config)

    def test_production_pipeline_uses_rag_v2_sync_boundary(self):
        self.assertEqual(
            PRODUCTION_STEPS[-1].script,
            config.WORKSPACE_DIR / "src" / "agentic_rag" / "rag_v2_sync.py",
        )
        self.assertIn("nova-RAG", PRODUCTION_STEPS[-1].name)
        self.assertIn("Active", PRODUCTION_STEPS[-1].name)
        self.assertEqual(PRODUCTION_STEPS[-1].args, ())

    def test_default_pipeline_prepares_foundation_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            narrative = Path(tmp) / "narrative_pass.py"
            narrative.write_text("", encoding="utf-8")
            observed = []
            with patch("data_foundation.pipeline.llm_provider_readiness_error", return_value=None):
                result = run_daily_pipeline(
                    date(2026, 5, 19),
                    paths=initialize_home(
                        Path(tmp) / "NovaDiary",
                        legacy_diary_root=Path(tmp) / "Diary",
                    ),
                    steps=[PipelineStep("narrative", narrative, ("{date}",))],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(
                        command,
                        0,
                        "ok\n",
                        "",
                    ),
                    pre_materializer=lambda selected, paths: observed.append(selected) or True,
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, ["2026-05-19"])

    def test_dashboard_readiness_reports_foundation_sources_and_rag_v2_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(config.os.environ, {"NOVA_HOME": str(Path(tmp) / "NovaDiary")}):
                readiness = dashboard_foundation.get_reader_readiness()

        self.assertEqual(
            readiness["configuredSources"],
            {
                "aiAssets": "foundation",
                "periodAssets": "foundation",
                "diaryMetrics": "foundation",
                "diaryMemory": "foundation",
                "diaryTasks": "foundation",
                "taskAuditSink": "foundation",
            },
        )
        self.assertEqual(
            readiness["configuredSourceEnvNames"]["diaryMetrics"],
            "DIARY_METRICS_SOURCE",
        )
        self.assertEqual(
            readiness["configuredSourceFields"]["diaryMetrics"],
            "diaryMetricsSource",
        )
        self.assertTrue(readiness["configuredSourcesValid"])
        self.assertEqual(readiness["preservedSources"], {"rag": "v2"})

    def test_current_diary_root_does_not_imply_legacy_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary")
            with patch.dict(config.os.environ, {"NOVA_HOME": str(paths.home)}):
                settings = read_settings(paths)

        self.assertEqual(
            paths.diary_dir.resolve(),
            (root / "NovaDiary" / "artifacts" / "diary").resolve(),
        )
        self.assertIsNone(paths.legacy_diary_root)
        self.assertEqual(settings["paths"]["diary"]["generatedDiary"], str(paths.diary_dir))
        self.assertEqual(settings["paths"]["diary"]["legacyDiaryRoot"], "")

    def test_default_nova_rag_is_v2_and_retired_sources_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            with patch.dict(config.os.environ, {"NOVA_HOME": str(paths.home)}):
                settings = resolve_rag_settings()

        self.assertEqual(settings.mode, "v2")
        self.assertNotIn("legacy-diary-daily", settings.indexing_source_sets)

    def test_report_task_stats_use_nova_task_v2_sqlite_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary")
            migrate(paths)
            create_task_node(paths, node_id="NT-READY", title="Ready", actor="test")
            create_task_node(
                paths,
                node_id="NT-DONE",
                title="Done",
                status="completed",
                actor="test",
            )
            with patch.dict(config.os.environ, {"NOVA_HOME": str(paths.home)}):
                stats = dashboard_diary._task_stats_snapshot(source="foundation")

        self.assertEqual(stats["source"], "foundation")
        self.assertEqual(stats["authority"], "Nova-Task v2 SQLite")
        self.assertEqual(stats["inProgress"], 1)
        self.assertEqual(stats["completed"], 1)


if __name__ == "__main__":
    unittest.main()
