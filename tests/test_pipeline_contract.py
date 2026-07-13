import runpy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import config
from data_foundation.pipeline import (
    PipelineStep,
    PRODUCTION_STEPS,
    _acquire_daily_pipeline_lock,
    _pipeline_total_timeout_seconds,
    _run_step,
    _release_daily_pipeline_lock,
    _technical_report_path,
    default_business_date,
    latest_pipeline_failure,
    materialize_blank_day_narrative,
    materialize_nova_task_outputs,
    prepare_diary_foundation_inputs,
    command_main,
    production_steps_for_language,
    run_daily_pipeline,
)
from data_foundation.db import connect
from data_foundation.diary_paths import diary_learning_report_path, diary_narrative_report_path, diary_technical_report_path
from data_foundation.nova_task import create_task_node
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.settings import write_settings


class PipelineCommandContractTests(unittest.TestCase):
    def setUp(self):
        self._stdout_capture = redirect_stdout(io.StringIO())
        self._stdout_capture.__enter__()
        self._env_patch = patch.dict(os.environ, {"LLM_API_KEY": "test-pipeline-key"})
        self._env_patch.__enter__()
        self._readiness_patch = patch("data_foundation.pipeline.llm_provider_readiness_error", return_value=None)
        self._readiness_patch.__enter__()

    def tearDown(self):
        self._readiness_patch.__exit__(None, None, None)
        self._env_patch.__exit__(None, None, None)
        self._stdout_capture.__exit__(None, None, None)

    @contextmanager
    def _real_pipeline_readiness(self):
        self._readiness_patch.__exit__(None, None, None)
        try:
            yield
        finally:
            self._readiness_patch = patch("data_foundation.pipeline.llm_provider_readiness_error", return_value=None)
            self._readiness_patch.__enter__()

    def test_default_date_keeps_previous_local_calendar_day_semantics(self):
        self.assertEqual(default_business_date(datetime(2026, 5, 27, 3, 59)), "2026-05-26")

    def test_default_date_uses_runtime_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_settings({"general": {"timezone": "UTC"}}, paths)

            value = default_business_date(datetime(2026, 5, 27, 0, 30, tzinfo=ZoneInfo("Asia/Hong_Kong")), paths)

        self.assertEqual(value, "2026-05-25")

    def test_command_main_help_does_not_start_pipeline(self):
        with patch("data_foundation.pipeline.run_daily_pipeline") as run:
            with self.assertRaises(SystemExit) as raised:
                command_main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        run.assert_not_called()

    def test_command_main_rejects_invalid_date_without_starting_pipeline(self):
        with patch("data_foundation.pipeline.run_daily_pipeline") as run:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    command_main(["260231"])

        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()

    def test_command_main_accepts_yymmdd_date(self):
        with patch(
            "data_foundation.pipeline.run_daily_pipeline",
            return_value=SimpleNamespace(success=True),
        ) as run:
            self.assertEqual(command_main(["260623"]), 0)

        run.assert_called_once_with("2026-06-23")

    def test_production_pipeline_excludes_legacy_nova_task_scripts(self):
        scripts = [step.script for step in PRODUCTION_STEPS]
        names = {script.name for script in scripts}
        self.assertNotIn("achievement_scanner.py", names)
        self.assertNotIn("ingest_to_db.py", names)
        self.assertNotIn("update_task_board.py", names)
        self.assertNotIn("collect_daily_archive.py", names)
        self.assertIn("unified_source_collector.py", names)
        self.assertFalse(any("src/nova_task" in str(script) for script in scripts))

    def test_pipeline_language_profile_selects_distinct_step_manifest(self):
        zh_scripts = [str(step.script) for step in production_steps_for_language("zh")]
        en_steps = production_steps_for_language("en")
        en_scripts = [str(step.script) for step in en_steps]
        en_manifest = [(step.name, "src/" + str(step.script).split("/src/", 1)[-1], step.args) for step in en_steps]

        self.assertEqual(tuple(production_steps_for_language("zh")), PRODUCTION_STEPS)
        self.assertTrue(any("diary_generator/narrative_pass.py" in script for script in zh_scripts))
        self.assertFalse(any("diary_generator/en/narrative_pass.py" in script for script in zh_scripts))
        self.assertTrue(any("diary_generator/en/narrative_pass.py" in script for script in en_scripts))
        self.assertTrue(any("diary_generator/en/technical_pass.py" in script for script in en_scripts))
        self.assertTrue(any("diary_generator/en/learning_pass.py" in script for script in en_scripts))
        self.assertEqual(
            en_manifest,
            [
                ("0. Unified AI asset collection", "src/ai_assets_center/unified_source_collector.py", ("{date}",)),
                ("2. Narrative pass (English)", "src/diary_generator/en/narrative_pass.py", ("{date}",)),
                ("4. Technical pass (English)", "src/diary_generator/en/technical_pass.py", ("{date}",)),
                ("7. Learning pass (English)", "src/diary_generator/en/learning_pass.py", ("{date}",)),
                ("8. nova-RAG index sync", "src/agentic_rag/rag_v2_sync.py", ()),
            ],
        )
        for step in en_steps:
            relative = str(step.script).split("/src/", 1)[-1]
            self.assertTrue((ROOT / "src" / relative).exists(), relative)

    def test_english_pipeline_pass_dry_run_keeps_structured_contract_status(self):
        script = ROOT / "src" / "diary_generator" / "en" / "narrative_pass.py"

        result = subprocess.run(
            [sys.executable, str(script), "--dry-run", "2026-05-19"],
            cwd=script.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "dry-run")
        self.assertEqual(payload["pipelineLanguageProfile"], "en")
        self.assertEqual(payload["diarySchemaVersion"], "diary-v1-en")
        self.assertEqual(payload["promptPayloadProfile"], "en-US")
        self.assertEqual(payload["pass"], "narrative")
        self.assertEqual(payload["businessDate"], "2026-05-19")
        self.assertEqual(payload["mode"], "dry-run")
        self.assertIn("expectedInputs", payload)
        self.assertIn("expectedOutputs", payload)
        self.assertTrue(payload["machineContractsUnchanged"])

    def test_english_pipeline_pass_contract_query_is_non_generating(self):
        script = ROOT / "src" / "diary_generator" / "en" / "technical_pass.py"

        result = subprocess.run(
            [sys.executable, str(script), "--contract", "2026-05-19"],
            cwd=script.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "contract")
        self.assertEqual(payload["mode"], "contract")
        self.assertEqual(payload["pipelineLanguageProfile"], "en")
        self.assertEqual(payload["diarySchemaVersion"], "diary-v1-en")
        self.assertEqual(payload["promptPayloadProfile"], "en-US")
        self.assertEqual(payload["displayLocale"], "en-US")
        self.assertEqual(payload["ragLanguageProfile"], "en")
        self.assertEqual(payload["pass"], "technical")
        self.assertEqual(payload["businessDate"], "2026-05-19")
        self.assertTrue(payload["expectedInputs"])
        self.assertTrue(payload["expectedOutputs"])

    def test_explicit_date_is_forwarded_to_current_step_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"),
                steps=[PipelineStep("fixture", script, ("{date}",))],
                runner=runner,
            )
            self.assertTrue(result.success)
            self.assertEqual(observed[0][0][-1], "2026-05-19")

    def test_pipeline_step_timeout_is_passed_to_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"stepTimeoutSeconds": 77}}, paths)
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs.get("timeout"))
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )

            self.assertTrue(result.success)
            self.assertEqual(observed, [77])

    def test_pipeline_step_timeout_override_uses_script_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"stepTimeoutSeconds": 77, "stepTimeouts": {"technical_pass.py": 123}}}, paths)
            script = root / "technical_pass.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs.get("timeout"))
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("technical", script)],
                runner=runner,
                nova_task_materializer=lambda selected, runtime_paths: True,
            )

            self.assertTrue(result.success)
            self.assertEqual(observed, [123])

    def test_pipeline_timeout_is_recorded_as_step_failure_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"stepTimeoutSeconds": 9}}, paths)
            script = root / "step.py"
            script.write_text("", encoding="utf-8")

            def runner(command, **kwargs):
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )
            latest = latest_pipeline_failure(paths)

            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "fixture")
            self.assertEqual(latest["failedStep"], "fixture")
            self.assertEqual(latest["reason"], "timeout after 9s")

    def test_pipeline_total_timeout_halts_before_next_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"totalWatchdogSeconds": 900, "stepTimeoutSeconds": 900}}, paths)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")
            calls = []

            monotonic = [100.0]

            def runner(command, **kwargs):
                calls.append(command)
                monotonic[0] = 1060.0
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=runner,
                monotonic_clock=lambda: monotonic[0],
            )

            latest = latest_pipeline_failure(paths)
            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "Daily Pipeline Timeout")
            self.assertEqual(len(calls), 1)
            self.assertEqual(latest["reason"], "timeout after 900s")

    def test_pipeline_total_watchdog_ignores_legacy_daily_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_settings({"pipeline": {"dailyTimeoutSeconds": 900}}, paths)

            timeout = _pipeline_total_timeout_seconds(paths)

        self.assertEqual(timeout, 7200)

    def test_failure_is_returned_as_non_success_for_wrapper_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "step.py"
            script.write_text("", encoding="utf-8")

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 4, "", "failed")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"),
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.succeeded_steps, 0)
            latest = latest_pipeline_failure(initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"))
            self.assertEqual(latest["businessDate"], "2026-05-19")
            self.assertEqual(latest["failedStep"], "fixture")

    def test_daily_pipeline_lock_blocks_duplicate_same_date_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            lock = _acquire_daily_pipeline_lock(paths, "2026-05-19")
            self.assertIsNotNone(lock)
            try:
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("fixture", script)],
                    runner=lambda command, **kwargs: self.fail("duplicate run executed a step"),
                )
            finally:
                _release_daily_pipeline_lock(lock)

            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "Pipeline Run Lock")
            self.assertEqual(result.succeeded_steps, 0)
            self.assertFalse((paths.state_dir / "locks" / "daily-pipeline-2026-05-19.lock").exists())

    def test_archived_legacy_env_override_does_not_disable_pre_materialization_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "narrative_pass.py"
            script.write_text("", encoding="utf-8")
            prepared = []
            with patch.dict(
                "os.environ",
                {"DIARY_METRICS_SOURCE": "legacy", "DIARY_MEMORY_SOURCE": "legacy", "DIARY_TASKS_SOURCE": "legacy"},
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"),
                    steps=[PipelineStep("narrative", script, ("{date}",))],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok\n", ""),
                    pre_materializer=lambda selected, paths: prepared.append((selected, paths)) or True,
                )
            self.assertTrue(result.success)
            self.assertEqual(len(prepared), 1)

    def test_foundation_diary_flag_prepares_target_before_narrative_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            collect = Path(tmp) / "collect.py"
            narrative = Path(tmp) / "narrative_pass.py"
            collect.write_text("", encoding="utf-8")
            narrative.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            def prepare(selected, paths):
                observed.append(f"prepare:{selected}")
                return True

            with patch.dict(
                "os.environ",
                {"DIARY_METRICS_SOURCE": "foundation", "DIARY_MEMORY_SOURCE": "legacy", "DIARY_TASKS_SOURCE": "legacy"},
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"),
                    steps=[PipelineStep("collect", collect), PipelineStep("narrative", narrative)],
                    runner=runner,
                    pre_materializer=prepare,
                )
            self.assertTrue(result.success)
            self.assertEqual(observed, ["collect.py", "prepare:2026-05-19", "narrative_pass.py"])

    def test_blank_day_fast_path_skips_passes_after_collect_and_materializes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            rag = root / "rag_v2_sync.py"
            for script in (collect, narrative, technical, learning, rag):
                script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                name = Path(command[1]).name
                observed.append(name)
                stdout = "🏁 Final Complete Sync: 0 items captured (Action Abstracted).\n" if name == "unified_source_collector.py" else "ok\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs", return_value=True) as blank_inputs,
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=True) as blank_check,
                patch("data_foundation.pipeline.materialize_blank_day_narrative", return_value=True) as blank_writer,
                patch("data_foundation.pipeline.materialize_blank_day_pipeline_outputs", return_value=True) as blank_post,
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[
                        PipelineStep("collect", collect),
                        PipelineStep("narrative", narrative),
                        PipelineStep("technical", technical),
                        PipelineStep("learning", learning),
                        PipelineStep("rag", rag),
                    ],
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: self.fail("Full Foundation inputs should not run on blank day"),
                    nova_task_materializer=lambda selected, runtime_paths: self.fail("Nova-Task should not run on blank day"),
                    post_materializer=lambda selected, runtime_paths: self.fail("Full post materialization should not run on blank day"),
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, ["unified_source_collector.py"])
        blank_inputs.assert_called_once_with("2026-05-19", paths)
        blank_check.assert_called_once_with("2026-05-19", paths)
        blank_writer.assert_called_once_with("2026-05-19", paths)
        blank_post.assert_called_once_with("2026-05-19", paths)

    def test_blank_day_fast_path_allows_missing_usage_stats_before_blank_marker_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            rag = root / "rag_v2_sync.py"
            for script in (collect, narrative, technical, learning, rag):
                script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                name = Path(command[1]).name
                observed.append(name)
                stdout = "🏁 Final Complete Sync: 0 items captured (Action Abstracted).\n" if name == "unified_source_collector.py" else "ok\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("diary_generator.narrative_pass.load_filtered_entries", return_value={}),
                patch("data_foundation.pipeline.daily_diary_usage_metrics", return_value=None),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs", return_value=True) as blank_inputs,
                patch("data_foundation.pipeline.materialize_blank_day_narrative", return_value=True) as blank_writer,
                patch("data_foundation.pipeline.materialize_blank_day_pipeline_outputs", return_value=True) as blank_post,
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[
                        PipelineStep("collect", collect),
                        PipelineStep("narrative", narrative),
                        PipelineStep("technical", technical),
                        PipelineStep("learning", learning),
                        PipelineStep("rag", rag),
                    ],
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: self.fail("Full Foundation inputs should not run on blank day"),
                    nova_task_materializer=lambda selected, runtime_paths: self.fail("Nova-Task should not run on blank day"),
                    post_materializer=lambda selected, runtime_paths: self.fail("Full post materialization should not run on blank day"),
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, ["unified_source_collector.py"])
        blank_inputs.assert_called_once_with("2026-05-19", paths)
        blank_writer.assert_called_once_with("2026-05-19", paths)
        blank_post.assert_called_once_with("2026-05-19", paths)

    def test_blank_day_narrative_uses_english_materializer_for_english_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            from diary_generator import narrative_pass as zh_narrative_pass
            from diary_generator.en import narrative_pass as en_narrative_pass

            expected = paths.diary_dir / "diary-2026-05-19" / "diary-260519-no-activity.md"
            with (
                patch.object(en_narrative_pass, "write_blank_day_report", return_value=expected) as en_writer,
                patch.object(zh_narrative_pass, "write_blank_day_report", side_effect=AssertionError("zh materializer called")),
            ):
                result = materialize_blank_day_narrative("2026-05-19", paths)

        self.assertTrue(result)
        en_writer.assert_called_once_with("2026-05-19", paths.diary_dir)

    def test_blank_day_narrative_real_english_writer_creates_no_activity_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            from diary_generator.en import narrative_pass as en_narrative_pass

            with patch.object(en_narrative_pass, "fetch_weather_for_date", return_value="Cloudy, 28 C"):
                result = materialize_blank_day_narrative("2026-05-19", paths)

            out_file = paths.diary_dir / "diary-2026" / "diary-2026-05" / "05-19" / "diary-260519-no-activity.md"
            out_file_exists = out_file.exists()
            content = out_file.read_text(encoding="utf-8")

        self.assertTrue(result)
        self.assertTrue(out_file_exists)
        self.assertIn("## Weather\nCloudy, 28 C", content)
        self.assertIn('"activityState": "empty"', content)

    def test_blank_day_narrative_writer_ignores_cron_only_filtered_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            cron_dir = paths.diary_dir / "__diary_daily" / "2026-05-19" / "_filtered" / "cron"
            cron_dir.mkdir(parents=True, exist_ok=True)
            (cron_dir / "unified_daily.jsonl").write_text(
                '{"role":"system","content":"cron job completed","time":"04:00"}\n',
                encoding="utf-8",
            )
            from diary_generator import narrative_pass

            with (
                patch.object(narrative_pass, "fetch_weather_for_date", return_value="天气位置未配置"),
                patch.object(narrative_pass, "generate_diary_with_fallback", side_effect=AssertionError("LLM should not run")),
            ):
                result = materialize_blank_day_narrative("2026-05-19", paths)

            out_file = paths.diary_dir / "diary-2026" / "diary-2026-05" / "05-19" / "日记-260519-no-activity.md"
            content = out_file.read_text(encoding="utf-8")

        self.assertTrue(result)
        self.assertIn("## 天气\n天气位置未配置", content)
        self.assertIn("今日无活动", content)
        self.assertIn('"activityState": "empty"', content)
        self.assertNotIn("cron job completed", content)

    def test_blank_day_fast_path_ignores_cron_only_filtered_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            rag = root / "rag_v2_sync.py"
            for script in (collect, narrative, technical, learning, rag):
                script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                name = Path(command[1]).name
                observed.append(name)
                if name == "unified_source_collector.py":
                    cron_dir = paths.diary_dir / "__diary_daily" / "2026-05-19" / "_filtered" / "cron"
                    cron_dir.mkdir(parents=True, exist_ok=True)
                    (cron_dir / "unified_daily.jsonl").write_text(
                        '{"role":"system","content":"cron job completed","time":"04:00"}\n',
                        encoding="utf-8",
                    )
                    stdout = "🏁 Final Complete Sync: 1 items captured (Action Abstracted).\n"
                else:
                    stdout = "ok\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs", return_value=True) as blank_inputs,
                patch("data_foundation.pipeline.materialize_blank_day_narrative", return_value=True) as blank_writer,
                patch("data_foundation.pipeline.materialize_blank_day_pipeline_outputs", return_value=True) as blank_post,
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[
                        PipelineStep("collect", collect),
                        PipelineStep("narrative", narrative),
                        PipelineStep("technical", technical),
                        PipelineStep("learning", learning),
                        PipelineStep("rag", rag),
                    ],
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: self.fail("Full Foundation inputs should not run on cron-only blank day"),
                    nova_task_materializer=lambda selected, runtime_paths: self.fail("Nova-Task should not run on cron-only blank day"),
                    post_materializer=lambda selected, runtime_paths: self.fail("Full post materialization should not run on cron-only blank day"),
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, ["unified_source_collector.py"])
        blank_inputs.assert_called_once_with("2026-05-19", paths)
        blank_writer.assert_called_once_with("2026-05-19", paths)
        blank_post.assert_called_once_with("2026-05-19", paths)

    def test_reuse_foundation_inputs_can_take_blank_day_fast_path_without_collect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            rag = root / "rag_v2_sync.py"
            for script in (collect, narrative, technical, learning, rag):
                script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                del kwargs
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs", return_value=True) as blank_inputs,
                patch("data_foundation.pipeline.prepare_existing_diary_foundation_inputs") as existing_inputs,
                patch("data_foundation.pipeline.prepare_diary_foundation_inputs") as fresh_inputs,
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=True) as blank_check,
                patch("data_foundation.pipeline.materialize_blank_day_narrative", return_value=True) as blank_writer,
                patch("data_foundation.pipeline.materialize_blank_day_pipeline_outputs", return_value=True) as blank_post,
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[
                        PipelineStep("collect", collect),
                        PipelineStep("narrative", narrative),
                        PipelineStep("technical", technical),
                        PipelineStep("learning", learning),
                        PipelineStep("rag", rag),
                    ],
                    runner=runner,
                    reuse_foundation_inputs=True,
                    nova_task_materializer=lambda selected, runtime_paths: self.fail("Nova-Task should not run on blank day"),
                    post_materializer=lambda selected, runtime_paths: self.fail("Full post materialization should not run on blank day"),
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, [])
        blank_inputs.assert_called_once_with("2026-05-19", paths)
        blank_check.assert_called_once_with("2026-05-19", paths)
        blank_writer.assert_called_once_with("2026-05-19", paths)
        blank_post.assert_called_once_with("2026-05-19", paths)
        existing_inputs.assert_not_called()
        fresh_inputs.assert_not_called()

    def test_collect_blank_day_detection_uses_final_summary_not_agent_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            for script in (collect, narrative):
                script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                name = Path(command[1]).name
                observed.append(name)
                if name == "unified_source_collector.py":
                    stdout = (
                        "  gemini-cli: 0 files, 0 entries, 0 dialogue lines\n"
                        "🏁 Final Complete Sync: 42 items captured (Action Abstracted).\n"
                    )
                else:
                    stdout = "ok\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs") as blank_inputs,
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=False),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("collect", collect), PipelineStep("narrative", narrative)],
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: True,
                )

            self.assertTrue(result.success)
            self.assertEqual(observed, ["unified_source_collector.py", "narrative_pass.py"])
            blank_inputs.assert_not_called()

    def test_collect_empty_uses_full_foundation_inputs_when_unified_entries_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            collect = root / "unified_source_collector.py"
            narrative = root / "narrative_pass.py"
            for script in (collect, narrative):
                script.write_text("", encoding="utf-8")
            observed = []
            prepared = []

            def runner(command, **kwargs):
                del kwargs
                name = Path(command[1]).name
                observed.append(name)
                stdout = "🏁 Final Complete Sync: 0 items captured (Action Abstracted).\n" if name == "unified_source_collector.py" else "ok\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")

            def prepare(selected, runtime_paths):
                prepared.append((selected, runtime_paths.home))
                return True

            with (
                patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs") as blank_inputs,
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=False) as blank_check,
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("collect", collect), PipelineStep("narrative", narrative)],
                    runner=runner,
                    pre_materializer=prepare,
                )

        self.assertTrue(result.success)
        self.assertEqual(observed, ["unified_source_collector.py", "narrative_pass.py"])
        self.assertEqual(prepared, [("2026-05-19", paths.home)])
        blank_check.assert_called_once_with("2026-05-19", paths)
        blank_inputs.assert_not_called()

    def test_failed_foundation_preparation_stops_before_narrative_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            narrative = Path(tmp) / "narrative_pass.py"
            narrative.write_text("", encoding="utf-8")
            with patch.dict("os.environ", {"DIARY_MEMORY_SOURCE": "foundation"}):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary"),
                    steps=[PipelineStep("narrative", narrative)],
                    runner=lambda command, **kwargs: self.fail("narrative ran after failed preparation"),
                    pre_materializer=lambda selected, paths: False,
                )
            self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Foundation Diary Inputs")

    def test_runtime_settings_can_drive_pipeline_foundation_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"runtimeSources": {"diaryMetricsSource": "foundation"}}, paths)
            narrative = root / "narrative_pass.py"
            narrative.write_text("", encoding="utf-8")
            observed = []

            def prepare(selected, runtime_paths):
                observed.append((selected, runtime_paths.home))
                return True

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("narrative", narrative)],
                runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok\n", ""),
                pre_materializer=prepare,
            )
            self.assertTrue(result.success)
            self.assertEqual(observed, [("2026-05-19", paths.home)])

    def test_pipeline_materializes_foundation_outputs_before_final_rag_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            learning = root / "learning_pass.py"
            rag = root / "rag_v2_sync.py"
            learning.write_text("", encoding="utf-8")
            rag.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            def post(selected, runtime_paths):
                observed.append(f"materialize:{selected}:{runtime_paths.home.name}")
                return True

            with patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("learning", learning), PipelineStep("rag", rag)],
                    runner=runner,
                    post_materializer=post,
                )
            self.assertTrue(result.success)
            self.assertEqual(observed, ["learning_pass.py", "materialize:2026-05-19:NovaDiary", "rag_v2_sync.py"])

    def test_pipeline_invokes_final_rag_sync_with_default_auto_promote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(command)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with (
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("rag", rag, ())],
                    runner=runner,
                    post_materializer=lambda selected, runtime_paths: True,
                )

            self.assertTrue(result.success)
            self.assertEqual(Path(observed[0][1]).name, "rag_v2_sync.py")
            self.assertNotIn("--no-promote", observed[0])
            self.assertNotIn("--promote", observed[0])

    def test_pipeline_blocks_final_rag_before_subprocess_when_readiness_preflight_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")

            with (
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch(
                    "data_foundation.pipeline._final_rag_readiness_blocking_reason",
                    return_value="nova-RAG server is not ready for candidate indexing.",
                ),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("rag", rag, ())],
                    runner=lambda command, **kwargs: self.fail("RAG subprocess should not run when readiness blocks"),
                    post_materializer=lambda selected, runtime_paths: True,
                )

            latest = latest_pipeline_failure(paths)
            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "Final RAG Sync Readiness")
            self.assertEqual(latest["failedStep"], "Final RAG Sync Readiness")
            self.assertEqual(latest["reason"], "nova-RAG server is not ready for candidate indexing.")

    def test_pipeline_technical_report_path_uses_current_diary_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "LegacyDiary"
            current = root / "GeneratedDiary"
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=legacy)
            paths = update_runtime_manifest_paths(paths.home, generated_diary_root=current, legacy_diary_root=legacy)
            legacy_report = legacy / "diary-2026-05-19" / "技术进展-260519.md"
            current_report = current / "diary-2026" / "diary-2026-05" / "05-19" / "技术进展-260519.md"
            legacy_report.parent.mkdir(parents=True)
            current_report.parent.mkdir(parents=True)
            legacy_report.write_text("legacy", encoding="utf-8")
            current_report.write_text("current", encoding="utf-8")

            self.assertEqual(_technical_report_path("2026-05-19", paths), current_report)

    def test_pipeline_technical_report_path_uses_english_profile_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            paths = update_runtime_manifest_paths(paths.home, generated_diary_root=root / "GeneratedDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            current = paths.diary_dir / "diary-2026" / "diary-2026-05" / "05-19"
            current.mkdir(parents=True)
            chinese_report = current / "技术进展-260519.md"
            english_report = current / "technical-260519.md"
            chinese_report.write_text("zh", encoding="utf-8")
            english_report.write_text("en", encoding="utf-8")

            self.assertEqual(_technical_report_path("2026-05-19", paths), english_report)

    def test_pipeline_technical_report_path_fallback_is_english_when_profile_is_en(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            paths = update_runtime_manifest_paths(paths.home, generated_diary_root=root / "GeneratedDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)

            self.assertEqual(
                _technical_report_path("2026-05-19", paths),
                paths.diary_dir / "diary-2026" / "diary-2026-05" / "05-19" / "technical-260519.md",
            )

    def test_pipeline_treats_executed_rag_sync_blocked_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")
            payload = {
                "status": "blocked",
                "reason": "nova-RAG server is not ready for candidate indexing.",
            }

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

            with (
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", return_value=None),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("rag", rag, ())],
                    runner=runner,
                    post_materializer=lambda selected, runtime_paths: True,
                )

            latest = latest_pipeline_failure(paths)
            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "rag")
            self.assertEqual(
                latest["reason"],
                "nova-RAG index sync blocked: nova-RAG server is not ready for candidate indexing.",
            )

    def test_pipeline_skips_final_rag_when_rag_is_disabled_but_still_materializes_foundation_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"rag": {"mode": "disabled"}}, paths)
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")
            observed = []

            def post(selected, runtime_paths):
                observed.append(f"materialize:{selected}:{runtime_paths.home.name}")
                return True

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("rag", rag)],
                runner=lambda command, **kwargs: self.fail("RAG ran while disabled"),
                post_materializer=post,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.succeeded_steps, 1)
            self.assertEqual(observed, ["materialize:2026-05-19:NovaDiary"])

    def test_pipeline_skip_final_rag_env_override_skips_only_rag_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")
            observed = []

            def post(selected, runtime_paths):
                observed.append(f"materialize:{selected}:{runtime_paths.home.name}")
                return True

            with patch.dict(os.environ, {"NOVA_PIPELINE_SKIP_FINAL_RAG": "1"}):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("rag", rag)],
                    runner=lambda command, **kwargs: self.fail("RAG ran while skip override was set"),
                    post_materializer=post,
                )

            self.assertTrue(result.success)
            self.assertEqual(result.succeeded_steps, 1)
            self.assertEqual(observed, ["materialize:2026-05-19:NovaDiary"])

    def test_pipeline_runs_nova_task_materialization_after_technical_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            technical.write_text("", encoding="utf-8")
            learning.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            def nova_task(selected, runtime_paths):
                observed.append(f"nova-task:{selected}:{runtime_paths.home.name}")
                return True

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("technical", technical), PipelineStep("learning", learning)],
                runner=runner,
                nova_task_materializer=nova_task,
            )

            self.assertTrue(result.success)
            self.assertEqual(observed, ["technical_pass.py", "nova-task:2026-05-19:NovaDiary", "learning_pass.py"])

    def test_pipeline_skips_nova_task_materialization_when_feature_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"features": {"novaTask": False}}, paths)
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            technical.write_text("", encoding="utf-8")
            learning.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("technical", technical), PipelineStep("learning", learning)],
                runner=runner,
                nova_task_materializer=lambda selected, runtime_paths: self.fail("Nova-Task materializer should be gated"),
            )

            self.assertTrue(result.success)
            self.assertEqual(observed, ["technical_pass.py", "learning_pass.py"])

    def test_failed_nova_task_materialization_does_not_halt_diary_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            technical = root / "technical_pass.py"
            learning = root / "learning_pass.py"
            technical.write_text("", encoding="utf-8")
            learning.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("technical", technical), PipelineStep("learning", learning)],
                runner=runner,
                nova_task_materializer=lambda selected, runtime_paths: False,
            )

            self.assertTrue(result.success)
            self.assertEqual(observed, ["technical_pass.py", "learning_pass.py"])

    def test_nova_task_pipeline_materializer_runs_reconciliation_from_technical_report_and_exports_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            report_dir = paths.diary_dir / "diary-2026-05-19"
            report_dir.mkdir(parents=True)
            report = report_dir / "技术进展-260519.md"
            report.write_text(
                "# Technical report\n\n## 七、Nova-Task Reconciliation Hooks\n- hook_type: task_candidate\n",
                encoding="utf-8",
            )
            create_task_node(paths, node_id="NT-ACTIVE", title="Active task", actor="operator")
            observed = {}

            def fake_reconciliation(*args, **kwargs):
                observed["paths"] = args[0]
                observed.update(kwargs)
                return SimpleNamespace(
                    event_count=2,
                    candidate_count=1,
                    auto_confirmed_count=1,
                    action_count=3,
                    attached_count=1,
                    rejected_count=1,
                    deferred_count=1,
                    merged_count=0,
                    superseded_count=0,
                    pending_after=0,
                )

            with patch("data_foundation.pipeline.run_work_graph_reconciliation", side_effect=fake_reconciliation):
                self.assertTrue(materialize_nova_task_outputs("2026-05-19", paths))

            with connect(paths, read_only=True) as connection:
                export_count = connection.execute("SELECT COUNT(*) FROM nova_task_exports").fetchone()[0]

        self.assertEqual(observed["paths"], paths)
        self.assertEqual(observed["business_date"], date(2026, 5, 19))
        self.assertTrue(observed["apply"])
        self.assertTrue(observed["auto_confirm_non_l1"])
        self.assertEqual(observed["technical_report_path"], report)
        self.assertEqual(export_count, 1)

    def test_nova_task_materialization_rejects_malformed_reconciliation_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            report_dir = paths.diary_dir / "diary-2026-05-19"
            report_dir.mkdir(parents=True)
            report = report_dir / "技术进展-260519.md"
            report.write_text("# Technical report\n", encoding="utf-8")

            def fake_reconciliation(*args, **kwargs):
                return SimpleNamespace(
                    response_malformed=True,
                    artifact_path=str(root / "bad-recon.md"),
                )

            with patch("data_foundation.pipeline.run_work_graph_reconciliation", side_effect=fake_reconciliation):
                self.assertFalse(materialize_nova_task_outputs("2026-05-19", paths))

            with connect(paths, read_only=True) as connection:
                export_count = connection.execute("SELECT COUNT(*) FROM nova_task_exports").fetchone()[0]

        self.assertEqual(export_count, 0)

    def test_pipeline_subprocess_env_points_config_at_runtime_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs["env"])
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )

            self.assertTrue(result.success)
            self.assertEqual(observed[0]["NOVA_HOME"], str(paths.home))
            self.assertNotIn("DIARY_OUTPUT_DIR", observed[0])

    def test_pipeline_subprocess_env_exports_configured_llm_key_name(self):
        from data_foundation.settings import write_llm_provider

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_llm_provider(
                {
                    "provider": "openai-compatible",
                    "endpoint": "https://llm.local",
                    "model": "m1",
                    "apiKey": "secret",
                    "apiKeyEnv": "CUSTOM_LLM_KEY",
                },
                paths,
            )
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs["env"])
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=runner,
            )

        self.assertTrue(result.success)
        self.assertEqual(observed[0]["LLM_API_KEY"], "secret")
        self.assertEqual(observed[0]["CUSTOM_LLM_KEY"], "secret")

    def test_daily_pipeline_fails_fast_before_narrative_when_provider_key_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            narrative = root / "narrative_pass.py"
            narrative.write_text("", encoding="utf-8")

            with self._real_pipeline_readiness(), patch.dict(os.environ, {"LLM_API_KEY": ""}):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=[PipelineStep("Narrative", narrative)],
                    runner=lambda command, **kwargs: self.fail("narrative subprocess should not be launched"),
                )
            failure = latest_pipeline_failure(paths)

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Narrative")
        self.assertIn("missing apiKey", failure["reason"])

    def test_pipeline_subprocess_env_exports_language_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en"}}, paths)
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs["env"])
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = _run_step(PipelineStep("fixture", script), "2026-05-19", runner, paths)

        self.assertTrue(result.success)
        self.assertEqual(observed[0]["NOVA_PIPELINE_LANGUAGE_PROFILE"], "en")
        self.assertEqual(observed[0]["NOVA_DIARY_SCHEMA_VERSION"], "diary-v1-en")
        self.assertEqual(observed[0]["NOVA_PROMPT_PAYLOAD_PROFILE"], "en-US")
        self.assertEqual(observed[0]["NOVA_DISPLAY_LOCALE"], "en-US")
        self.assertEqual(observed[0]["NOVA_RAG_LANGUAGE_PROFILE"], "en")

    def test_pipeline_subprocess_env_exports_thinking_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"thinkingMode": "medium"}}, paths)
            script = root / "step.py"
            script.write_text("", encoding="utf-8")
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs["env"])
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = _run_step(PipelineStep("fixture", script), "2026-05-19", runner, paths)

        self.assertTrue(result.success)
        self.assertEqual(observed[0]["LLM_THINKING_MODE"], "medium")

    def test_english_pipeline_profile_requires_enable_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en"}}, paths)
            script = root / "step.py"
            script.write_text("", encoding="utf-8")

            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("fixture", script)],
                runner=lambda command, **kwargs: self.fail("gated English pipeline should not execute steps"),
            )

            failure = latest_pipeline_failure(paths)

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Pipeline Language Profile")
        self.assertEqual(failure["failedStep"], "Pipeline Language Profile")
        self.assertIn("pipeline.englishEnabled is false", failure["reason"])

    def test_english_pipeline_enable_gate_executes_english_manifest_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            collect = root / "en" / "unified_source_collector.py"
            narrative = root / "en" / "diary_generator" / "en" / "narrative_pass.py"
            collect.parent.mkdir(parents=True, exist_ok=True)
            narrative.parent.mkdir(parents=True, exist_ok=True)
            collect.write_text("", encoding="utf-8")
            narrative.write_text("", encoding="utf-8")
            en_steps = (
                PipelineStep("0. Unified AI asset collection", collect, ("{date}",)),
                PipelineStep("2. Narrative pass (English)", narrative, ("{date}",)),
            )
            observed = []

            def runner(command, **kwargs):
                script = Path(command[1])
                observed.append(script)
                if "diary_generator/en/narrative_pass.py" in str(script):
                    return subprocess.CompletedProcess(command, 2, '{"status":"not-enabled"}\n', "")
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with (
                patch("data_foundation.pipeline.EN_PRODUCTION_STEPS", en_steps),
                patch("data_foundation.pipeline._llm_provider_blocking_reason", return_value=None),
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=False),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: True,
                    nova_task_materializer=lambda selected, runtime_paths: True,
                    post_materializer=lambda selected, runtime_paths: True,
                )

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "2. Narrative pass (English)")
        observed_text = [str(path) for path in observed]
        self.assertTrue(any("diary_generator/en/narrative_pass.py" in path for path in observed_text))
        self.assertFalse(any("diary_generator/narrative_pass.py" in path and "/en/" not in path for path in observed_text))

    def test_english_pipeline_manifest_writes_expected_profile_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}, "features": {"novaTask": False}}, paths)
            scripts_root = root / "scripts" / "diary_generator" / "en"
            scripts_root.mkdir(parents=True)
            narrative = scripts_root / "narrative_pass.py"
            technical = scripts_root / "technical_pass.py"
            learning = scripts_root / "learning_pass.py"
            for script in (narrative, technical, learning):
                script.write_text("", encoding="utf-8")
            en_steps = (
                PipelineStep("2. Narrative pass (English)", narrative, ("{date}",)),
                PipelineStep("4. Technical pass (English)", technical, ("{date}",)),
                PipelineStep("7. Learning pass (English)", learning, ("{date}",)),
            )
            observed = []

            def runner(command, **kwargs):
                script_name = Path(command[1]).name
                date_str = command[-1]
                observed.append((script_name, kwargs["env"]["NOVA_PIPELINE_LANGUAGE_PROFILE"]))
                if script_name == "narrative_pass.py":
                    out = diary_narrative_report_path(paths.diary_dir, date_str, language_profile="en")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("## Daily Overview\nEnglish narrative.\n", encoding="utf-8")
                elif script_name == "technical_pass.py":
                    out = diary_technical_report_path(paths.diary_dir, date_str, language_profile="en")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("# 2026-05-19 Technical Progress Report (Nova-Task v2)\n", encoding="utf-8")
                elif script_name == "learning_pass.py":
                    out = diary_learning_report_path(paths.diary_dir, date_str, language_profile="en")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text("# 2026-05-19 Learning and Infrastructure Audit\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            with (
                patch("data_foundation.pipeline.EN_PRODUCTION_STEPS", en_steps),
                patch("data_foundation.pipeline._llm_provider_blocking_reason", return_value=None),
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=False),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    runner=runner,
                    pre_materializer=lambda selected, runtime_paths: True,
                    post_materializer=lambda selected, runtime_paths: True,
                )

            self.assertTrue(result.success)
            self.assertEqual(
                observed,
                [("narrative_pass.py", "en"), ("technical_pass.py", "en"), ("learning_pass.py", "en")],
            )
            day_dir = paths.diary_dir / "diary-2026" / "diary-2026-05" / "05-19"
            self.assertTrue((day_dir / "diary-260519.md").exists())
            self.assertTrue((day_dir / "technical-260519.md").exists())
            self.assertTrue((day_dir / "learning-260519.md").exists())
            self.assertFalse((day_dir / "日记-260519.md").exists())
            self.assertFalse((day_dir / "技术进展-260519.md").exists())
            self.assertFalse((day_dir / "智慧沉淀-260519.md").exists())

    def test_llm_steps_fail_fast_when_memory_secret_is_not_cross_process_visible(self):
        from data_foundation.settings import write_llm_provider

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            script = root / "narrative_pass.py"
            script.write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"OPEN_NOVA_SECRET_BACKEND": "memory"}):
                write_llm_provider(
                    {
                        "provider": "openai-compatible",
                        "endpoint": "https://llm.local",
                        "model": "m1",
                        "apiKey": "secret",
                    },
                    paths,
                )

            def runner(command, **kwargs):
                raise AssertionError("LLM step subprocess should not be launched")

            with self._real_pipeline_readiness():
                result = _run_step(PipelineStep("Narrative", script), "2026-05-19", runner, paths)

        self.assertFalse(result.success)
        self.assertIn("process-local memory backend", result.reason)

    def test_english_llm_steps_use_provider_readiness_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            script = root / "en" / "diary_generator" / "en" / "narrative_pass.py"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("", encoding="utf-8")

            with self._real_pipeline_readiness(), patch.dict(os.environ, {"LLM_API_KEY": ""}):
                result = _run_step(
                    PipelineStep("2. Narrative pass (English)", script, ("{date}",)),
                    "2026-05-19",
                    lambda command, **kwargs: self.fail("English narrative subprocess should not be launched"),
                    paths,
                )

        self.assertFalse(result.success)
        self.assertIn("missing apiKey", result.reason)

    def test_legacy_nova_task_scripts_are_not_part_of_open_nova(self):
        for relative in (
            "src/nova_task/achievement_scanner.py",
            "src/nova_task/ingest_to_db.py",
            "src/nova_task/update_task_board.py",
        ):
            self.assertFalse((ROOT / relative).exists())

    def test_failed_pipeline_materialization_stops_before_final_rag_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            rag = root / "rag_v2_sync.py"
            rag.write_text("", encoding="utf-8")
            result = run_daily_pipeline(
                "2026-05-19",
                paths=paths,
                steps=[PipelineStep("rag", rag)],
                runner=lambda command, **kwargs: self.fail("RAG ran after failed Foundation materialization"),
                post_materializer=lambda selected, runtime_paths: False,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.failed_step, "Pipeline Foundation Materialization")

    def test_foundation_preparation_materializes_selected_readers_and_records_gate(self):
        paths = object()
        result = SimpleNamespace(run_id=9, errors=0)
        with (
            patch("data_foundation.pipeline.config.DIARY_METRICS_SOURCE", "foundation"),
            patch("data_foundation.pipeline.config.DIARY_MEMORY_SOURCE", "foundation"),
            patch("data_foundation.pipeline.config.DIARY_TASKS_SOURCE", "foundation"),
            patch("data_foundation.pipeline.run_shadow_ingestion", return_value=result) as ingest,
            patch(
                "data_foundation.pipeline.materialize_workspace_attribution_catalog",
                return_value={"counts": {"projects": 2}},
            ) as workspace_catalog,
            patch("data_foundation.pipeline.materialize_diary_memory_snapshot") as materialize,
            patch(
                "data_foundation.pipeline.write_diary_memory_readiness_report",
                return_value={"status": "ready", "canEnable": {"diaryMemorySourceFoundation": True}},
            ) as memory,
            patch(
                "data_foundation.pipeline.write_diary_metrics_readiness_report",
                return_value={"status": "ready_with_approved_model_usage_change", "canEnable": {"diaryMetricsSourceFoundation": True}},
            ) as metrics,
            patch("data_foundation.pipeline.materialize_diary_tasks_snapshot") as materialize_tasks,
            patch(
                "data_foundation.pipeline.write_diary_tasks_readiness_report",
                return_value={"status": "ready_with_approved_checkbox_normalization", "canEnable": {"diaryTasksSourceFoundation": True}},
            ) as tasks,
        ):
            self.assertTrue(prepare_diary_foundation_inputs("2026-05-19", paths))
        ingest.assert_called_once_with(
            paths,
            date(2026, 5, 19),
            trigger="pipeline-diary-pre-materialization",
            observe_assets=False,
        )
        workspace_catalog.assert_called_once_with(paths)
        materialize.assert_called_once_with(paths, date(2026, 5, 19), 9)
        memory.assert_called_once_with(paths, date(2026, 5, 19))
        metrics.assert_called_once_with(
            paths,
            date(2026, 5, 19),
            approve_model_usage_normalization=True,
            approve_session_count_normalization=True,
        )
        materialize_tasks.assert_called_once_with(
            paths,
            date(2026, 5, 19),
            9,
        )
        tasks.assert_called_once_with(
            paths,
            date(2026, 5, 19),
            approve_checkbox_normalization=True,
        )

    def test_foundation_metrics_parity_mismatch_warns_but_allows_diary_generation(self):
        paths = object()
        result = SimpleNamespace(run_id=9, errors=0)
        with (
            patch("data_foundation.pipeline.resolve_runtime_source", side_effect=lambda name, selected: "foundation"),
            patch("data_foundation.pipeline.run_shadow_ingestion", return_value=result),
            patch("data_foundation.pipeline.materialize_workspace_attribution_catalog", return_value={"counts": {"projects": 2}}),
            patch("data_foundation.pipeline.materialize_diary_memory_snapshot"),
            patch(
                "data_foundation.pipeline.write_diary_memory_readiness_report",
                return_value={"status": "ready", "canEnable": {"diaryMemorySourceFoundation": True}},
            ),
            patch(
                "data_foundation.pipeline.write_diary_metrics_readiness_report",
                return_value={"status": "table_metrics_mismatch", "canEnable": {"diaryMetricsSourceFoundation": False}},
            ),
            patch("data_foundation.pipeline.materialize_diary_tasks_snapshot"),
            patch(
                "data_foundation.pipeline.write_diary_tasks_readiness_report",
                return_value={"status": "ready", "canEnable": {"diaryTasksSourceFoundation": True}},
            ),
        ):
            self.assertTrue(prepare_diary_foundation_inputs("2026-05-19", paths))

    def test_reuse_foundation_inputs_skips_source_collection_and_shadow_ingestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            script_root = root / "scripts"
            script_root.mkdir()
            for script_name in ("unified_source_collector.py", "narrative_pass.py"):
                (script_root / script_name).write_text("", encoding="utf-8")
            steps = [
                PipelineStep("unified", script_root / "unified_source_collector.py"),
                PipelineStep("narrative", script_root / "narrative_pass.py"),
            ]
            called_scripts = []

            def runner(command, **kwargs):
                del kwargs
                called_scripts.append(Path(command[1]).name)
                return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

            with (
                patch("data_foundation.pipeline.resolve_runtime_source", return_value="foundation"),
                patch("data_foundation.pipeline.prepare_existing_diary_foundation_inputs", return_value=True) as existing_inputs,
                patch("data_foundation.pipeline.prepare_diary_foundation_inputs") as fresh_inputs,
                patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=False),
            ):
                result = run_daily_pipeline(
                    "2026-05-19",
                    paths=paths,
                    steps=steps,
                    runner=runner,
                    reuse_foundation_inputs=True,
                )

        self.assertTrue(result.success)
        self.assertEqual(called_scripts, ["narrative_pass.py"])
        existing_inputs.assert_called_once_with("2026-05-19", paths)
        fresh_inputs.assert_not_called()

    def test_foundation_memory_readiness_still_blocks_diary_generation(self):
        paths = object()
        result = SimpleNamespace(run_id=9, errors=0)
        with (
            patch("data_foundation.pipeline.resolve_runtime_source", side_effect=lambda name, selected: "foundation"),
            patch("data_foundation.pipeline.run_shadow_ingestion", return_value=result),
            patch("data_foundation.pipeline.materialize_workspace_attribution_catalog", return_value={"counts": {"projects": 2}}),
            patch("data_foundation.pipeline.materialize_diary_memory_snapshot"),
            patch(
                "data_foundation.pipeline.write_diary_memory_readiness_report",
                return_value={"status": "missing_snapshot", "canEnable": {"diaryMemorySourceFoundation": False}},
            ),
            patch("data_foundation.pipeline.write_diary_metrics_readiness_report") as metrics,
        ):
            self.assertFalse(prepare_diary_foundation_inputs("2026-05-19", paths))

        metrics.assert_not_called()

    def test_stable_command_file_delegates_to_application_service(self):
        with patch("data_foundation.pipeline.command_main", return_value=7) as entry:
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(str(ROOT / "advanced" / "pipeline" / "run_daily_pipeline.py"), run_name="__main__")
        entry.assert_called_once_with()
        self.assertEqual(raised.exception.code, 7)


if __name__ == "__main__":
    unittest.main()
