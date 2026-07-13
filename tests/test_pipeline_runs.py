import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["OPEN_NOVA_SECRET_BACKEND"] = "memory"

from data_foundation.db import connect
from data_foundation.paths import initialize_home
from data_foundation.pipeline import PipelineStep, run_daily_pipeline
from data_foundation.pipeline_runs import (
    create_pipeline_run,
    finish_pipeline_run,
    finish_pipeline_run_if_status,
    latest_pipeline_run_for_date,
    pipeline_reconcile_plan,
)
from data_foundation.scheduler_reconcile import reconcile_pipeline_schedule
from data_foundation.settings import write_llm_provider, write_settings


class PipelineRunsTests(unittest.TestCase):
    def setUp(self):
        self._readiness_patch = patch("data_foundation.pipeline.llm_provider_readiness_error", return_value=None)
        self._readiness_patch.start()

    def tearDown(self):
        self._readiness_patch.stop()

    def test_daily_pipeline_records_successful_run_in_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "OpenNova", legacy_diary_root=root / "Diary")
            script = root / "step.py"
            script.write_text("print('ok')\n", encoding="utf-8")

            result = run_daily_pipeline(
                "2026-06-22",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=subprocess.run,
            )
            run = latest_pipeline_run_for_date(paths, "2026-06-22")

        self.assertTrue(result.success)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["runKind"], "manual")
        self.assertEqual(run["requestedBy"], "cli")
        self.assertEqual(run["steps"][0]["name"], "fixture")
        self.assertEqual(run["steps"][0]["status"], "completed")

    def test_daily_pipeline_records_failed_step_and_failure_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "OpenNova", legacy_diary_root=root / "Diary")
            script = root / "step.py"
            script.write_text("", encoding="utf-8")

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, "", "HTTP 403 usage limit")

            result = run_daily_pipeline(
                "2026-06-23",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )
            run = latest_pipeline_run_for_date(paths, "2026-06-23")

        self.assertFalse(result.success)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failureClass"], "llm_quota")
        self.assertEqual(run["steps"][0]["status"], "failed")

    def test_reconcile_blocks_when_missing_dates_exceed_auto_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "OpenNova", legacy_diary_root=Path(tmp) / "Diary")
            now = datetime(2026, 6, 29, 9, 0, tzinfo=ZoneInfo("UTC"))

            result = reconcile_pipeline_schedule(paths, now=now, apply=True, lookback_days=5, auto_limit_days=3)
            blocked = latest_pipeline_run_for_date(paths, "2026-06-28")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["missingCount"], 5)
        self.assertEqual(blocked["runKind"], "catchup_reconcile")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failureClass"], "manual_confirmation_required")
        self.assertEqual(blocked["metadata"]["missingDates"], result["missingDates"])

    def test_reconcile_auto_runs_when_missing_dates_are_within_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "OpenNova", legacy_diary_root=Path(tmp) / "Diary")
            now = datetime(2026, 6, 24, 9, 0, tzinfo=ZoneInfo("UTC"))
            calls = []

            def fake_run(day, **kwargs):
                calls.append((day, kwargs.get("trigger")))
                run_id = create_pipeline_run(
                    paths,
                    business_date=day,
                    run_kind="catchup",
                    requested_by="scheduler",
                )
                finish_pipeline_run(paths, run_id, status="completed")
                return SimpleNamespace(business_date=day, success=True, failed_step=None, succeeded_steps=1, total_steps=1)

            with patch("data_foundation.scheduler_reconcile.run_daily_pipeline", side_effect=fake_run):
                result = reconcile_pipeline_schedule(paths, now=now, apply=True, lookback_days=2, auto_limit_days=3)

        self.assertEqual(result["status"], "completed")
        self.assertEqual([day for day, _ in calls], ["2026-06-22", "2026-06-23"])
        self.assertEqual({trigger for _, trigger in calls}, {"scheduler-catchup"})

    def test_reconcile_plan_treats_completed_ledger_day_as_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "OpenNova", legacy_diary_root=Path(tmp) / "Diary")
            run_id = create_pipeline_run(paths, business_date="2026-06-22", run_kind="daily", requested_by="scheduler")
            finish_result = finish_pipeline_run(paths, run_id, status="completed")
            now = datetime(2026, 6, 24, 9, 0, tzinfo=ZoneInfo("UTC"))

            plan = pipeline_reconcile_plan(paths, now=now, lookback_days=2, auto_limit_days=3)

        self.assertIsNone(finish_result)
        self.assertEqual(plan["missingDates"], ["2026-06-23"])

    def test_terminal_compare_and_set_does_not_overwrite_existing_terminal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "OpenNova", legacy_diary_root=Path(tmp) / "Diary")
            run_id = create_pipeline_run(paths, business_date="2026-06-22", run_kind="daily", requested_by="scheduler")
            finish_pipeline_run(paths, run_id, status="failed", failure_class="cancelled")

            won = finish_pipeline_run_if_status(
                paths,
                run_id,
                expected_statuses={"running"},
                status="completed",
            )
            run = latest_pipeline_run_for_date(paths, "2026-06-22")

        self.assertFalse(won)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["failureClass"], "cancelled")

    def test_migration_creates_pipeline_runs_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "OpenNova", legacy_diary_root=Path(tmp) / "Diary")
            create_pipeline_run(paths, business_date="2026-06-22", run_kind="daily", requested_by="scheduler")
            with connect(paths, read_only=True) as connection:
                row = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_runs'").fetchone()

        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
