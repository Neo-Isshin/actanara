import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["OPEN_NOVA_SECRET_BACKEND"] = "memory"

from data_foundation.diary_paths import diary_narrative_report_path
from data_foundation.paths import initialize_home
from data_foundation.pipeline import PipelineStep, run_daily_pipeline
from data_foundation.pipeline_execution import PipelineExecutionContext
from data_foundation.pipeline_runs import (
    append_pipeline_step,
    create_pipeline_run,
    finish_pipeline_run,
    latest_pipeline_run_for_date,
)
from data_foundation.settings import write_settings


class ManualClock:
    def __init__(self, value: float = 100.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class PipelineDeadlineTests(unittest.TestCase):
    def setUp(self):
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()
        self._readiness = patch("data_foundation.pipeline.llm_provider_readiness_error", return_value=None)
        self._readiness.start()

    def tearDown(self):
        self._readiness.stop()
        self._stdout.__exit__(None, None, None)

    def _paths(self, root: Path):
        paths = initialize_home(root / "Runtime", legacy_diary_root=root / "Diary")
        write_settings(
            {
                "pipeline": {"totalWatchdogSeconds": 10, "stepTimeoutSeconds": 100},
                "features": {"novaTask": True},
            },
            paths,
        )
        return paths

    def _script(self, root: Path, name: str) -> Path:
        path = root / name
        path.write_text("", encoding="utf-8")
        return path

    def test_pre_materializer_receives_context_and_completed_stage_is_recorded_before_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            narrative = self._script(root, "narrative_pass.py")
            clock = ManualClock()
            calls = []

            def prepare(day, selected, *, execution_context):
                calls.append((day, selected, execution_context.total_timeout_seconds))
                clock.advance(11)
                return True

            with patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True):
                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("narrative", narrative)],
                    runner=lambda command, **kwargs: self.fail("narrative started after pre deadline"),
                    pre_materializer=prepare,
                    monotonic_clock=clock,
                )
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Daily Pipeline Timeout")
        self.assertEqual(calls, [("2026-07-10", paths, 10.0)])
        self.assertEqual(ledger["status"], "failed")
        self.assertEqual(ledger["failureClass"], "timeout")
        foundation = next(step for step in ledger["steps"] if step["metadata"].get("stageId") == "foundation-inputs")
        self.assertEqual(foundation["status"], "completed")
        self.assertFalse(foundation["metadata"]["committed"])
        self.assertEqual(foundation["metadata"]["artifactProofs"], [])

    def test_post_and_nova_task_materializers_stop_following_stages_after_return(self):
        for boundary in ("post", "nova"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = self._paths(root)
                clock = ManualClock()
                calls = []

                def materializer(day, selected, *, execution_context):
                    self.assertEqual(execution_context.remaining_seconds(), 10.0)
                    calls.append(boundary)
                    clock.advance(11)
                    return True

                if boundary == "post":
                    rag = self._script(root, "rag_v2_sync.py")
                    result = run_daily_pipeline(
                        "2026-07-10",
                        paths=paths,
                        steps=[PipelineStep("rag", rag)],
                        runner=lambda command, **kwargs: self.fail("RAG started after post deadline"),
                        post_materializer=materializer,
                        monotonic_clock=clock,
                    )
                    committed_stage = "foundation-materialization"
                else:
                    technical = self._script(root, "technical_pass.py")
                    learning = self._script(root, "learning_pass.py")

                    def runner(command, **kwargs):
                        name = Path(command[1]).name
                        calls.append(name)
                        if name == "learning_pass.py":
                            self.fail("learning started after Nova-Task deadline")
                        return subprocess.CompletedProcess(command, 0, "ok\n", "")

                    result = run_daily_pipeline(
                        "2026-07-10",
                        paths=paths,
                        steps=[PipelineStep("technical", technical), PipelineStep("learning", learning)],
                        runner=runner,
                        nova_task_materializer=materializer,
                        monotonic_clock=clock,
                    )
                    committed_stage = "nova-task"
                ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

                self.assertFalse(result.success)
                self.assertEqual(ledger["failureClass"], "timeout")
                outcome = next(step for step in ledger["steps"] if step["metadata"].get("stageId") == committed_stage)
                self.assertEqual(outcome["status"], "completed")
                self.assertFalse(outcome["metadata"]["committed"])

    def test_blank_input_and_narrative_boundaries_use_same_deadline(self):
        for boundary in ("blank-inputs", "blank-narrative"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = self._paths(root)
                clock = ManualClock()
                collect = self._script(root, "unified_source_collector.py")
                narrative = self._script(root, "narrative_pass.py")

                def delayed_success(*args, **kwargs):
                    clock.advance(11)
                    return True

                patches = [
                    patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True),
                    patch("data_foundation.pipeline._is_blank_day_after_collect", return_value=True),
                    patch("data_foundation.pipeline.prepare_blank_day_foundation_inputs", return_value=True),
                    patch("data_foundation.pipeline.materialize_blank_day_narrative", return_value=True),
                ]
                delayed_index = 2 if boundary == "blank-inputs" else 3
                patches[delayed_index] = patch(
                    "data_foundation.pipeline."
                    + ("prepare_blank_day_foundation_inputs" if boundary == "blank-inputs" else "materialize_blank_day_narrative"),
                    side_effect=delayed_success,
                )
                with patches[0], patches[1], patches[2], patches[3]:
                    result = run_daily_pipeline(
                        "2026-07-10",
                        paths=paths,
                        steps=[PipelineStep("collect", collect), PipelineStep("narrative", narrative)],
                        runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok\n", ""),
                        monotonic_clock=clock,
                    )
                ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

                self.assertFalse(result.success)
                self.assertEqual(ledger["failureClass"], "timeout")
                outcome = next(step for step in ledger["steps"] if step["metadata"].get("stageId") == boundary)
                self.assertEqual(outcome["status"], "completed")
                self.assertFalse(outcome["metadata"]["committed"])

    def test_cancel_wins_over_timeout_and_does_not_claim_sync_callable_was_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            narrative = self._script(root, "narrative_pass.py")
            clock = ManualClock()
            cancelled = [False]

            def prepare(day, selected):
                clock.advance(11)
                cancelled[0] = True
                return True

            with patch("data_foundation.pipeline._diary_foundation_enabled", return_value=True):
                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("narrative", narrative)],
                    runner=lambda command, **kwargs: self.fail("work started after cancellation boundary"),
                    pre_materializer=prepare,
                    cancellation_requested=lambda: cancelled[0],
                    monotonic_clock=clock,
                )
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Daily Pipeline Cancellation")
        self.assertEqual(ledger["status"], "failed")
        self.assertEqual(ledger["failureClass"], "cancelled")
        committed = next(step for step in ledger["steps"] if step["metadata"].get("stageId") == "foundation-inputs")
        self.assertEqual(committed["status"], "completed")
        self.assertFalse(committed["metadata"]["committed"])

    def test_wall_clock_changes_do_not_change_total_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            first = self._script(root, "first.py")
            second = self._script(root, "second.py")
            clock = ManualClock()
            calls = []

            class JumpingDateTime:
                values = [datetime(2099, 1, 1), datetime(1900, 1, 1), datetime(2200, 1, 1)]

                @classmethod
                def now(cls, tz=None):
                    value = cls.values.pop(0) if cls.values else datetime(1800, 1, 1)
                    return value if tz is None else value.replace(tzinfo=tz)

            with patch("data_foundation.pipeline.datetime", JumpingDateTime):
                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("first", first), PipelineStep("second", second)],
                    runner=lambda command, **kwargs: calls.append(Path(command[1]).name)
                    or subprocess.CompletedProcess(command, 0, "ok\n", ""),
                    monotonic_clock=clock,
                )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["first.py", "second.py"])

    def test_each_subprocess_uses_fresh_remaining_total_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            first = self._script(root, "first.py")
            second = self._script(root, "second.py")
            clock = ManualClock()
            observed = []

            def runner(command, **kwargs):
                observed.append(kwargs["timeout"])
                if Path(command[1]).name == "first.py":
                    clock.advance(4)
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            result = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=runner,
                monotonic_clock=clock,
            )

        self.assertTrue(result.success)
        self.assertEqual(observed, [10.0, 6.0])

    def test_real_subprocess_timeout_waits_for_direct_child_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            write_settings(
                {"pipeline": {"totalWatchdogSeconds": 1, "stepTimeoutSeconds": 10}},
                paths,
            )
            late_write = root / "late-write.txt"
            slow = root / "slow.py"
            slow.write_text(
                "import time\n"
                "from pathlib import Path\n"
                "time.sleep(2)\n"
                f"Path({str(late_write)!r}).write_text('late', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("slow", slow)],
            )
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")
            deadline = time.monotonic() + 1.25
            while time.monotonic() < deadline and not late_write.exists():
                time.sleep(0.05)

            self.assertFalse(result.success)
            self.assertEqual(ledger["failureClass"], "timeout")
            self.assertFalse(late_write.exists())
            self.assertFalse((paths.state_dir / "locks" / "daily-pipeline-2026-07-10.lock").exists())

    def test_bounded_subprocess_timeout_uses_clock_value_at_the_claim_boundary(self):
        values = iter((100.0, 101.0))
        context = PipelineExecutionContext.start(10, monotonic_clock=lambda: next(values))

        timeout = context.bounded_timeout(100, checkpoint="fixture:subprocess-start")

        self.assertEqual(timeout, 9.0)

    def test_final_terminal_checkpoint_catches_deadline_after_artifact_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            clock = ManualClock()

            def artifact_paths(selected, day, *, language_profile):
                clock.advance(11)
                return {"narrative": [], "technical": [], "learning": []}

            with patch("data_foundation.pipeline._pipeline_artifact_paths", side_effect=artifact_paths):
                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[],
                    monotonic_clock=clock,
                )
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(result.success)
        self.assertEqual(ledger["failureClass"], "timeout")
        self.assertEqual(ledger["status"], "failed")

    def test_final_terminal_checkpoint_catches_cancel_after_artifact_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            cancelled = [False]

            def artifact_paths(selected, day, *, language_profile):
                cancelled[0] = True
                return {"narrative": [], "technical": [], "learning": []}

            with patch("data_foundation.pipeline._pipeline_artifact_paths", side_effect=artifact_paths):
                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[],
                    cancellation_requested=lambda: cancelled[0],
                )
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Daily Pipeline Cancellation")
        self.assertEqual(ledger["failureClass"], "cancelled")
        self.assertEqual(ledger["status"], "failed")

    def test_native_retry_reuses_committed_stage_and_links_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            first = self._script(root, "first.py")
            second = self._script(root, "second.py")
            artifact = diary_narrative_report_path(paths.diary_dir, "2026-07-10")
            artifact.parent.mkdir(parents=True, exist_ok=True)
            calls = []

            def first_attempt(command, **kwargs):
                name = Path(command[1]).name
                calls.append(f"first:{name}")
                if name == "first.py":
                    artifact.write_text("committed-once\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")
                return subprocess.CompletedProcess(command, 1, "", "boom")

            initial = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=first_attempt,
            )
            parent = latest_pipeline_run_for_date(paths, "2026-07-10")

            def retry_attempt(command, **kwargs):
                name = Path(command[1]).name
                calls.append(f"retry:{name}")
                if name == "first.py":
                    artifact.write_text("unexpected-rewrite\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "ok\n", "")

            retried = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=retry_attempt,
                retry_of_run_id=parent["id"],
            )
            child = latest_pipeline_run_for_date(paths, "2026-07-10")
            artifact_content = artifact.read_text(encoding="utf-8")

        self.assertFalse(initial.success)
        self.assertTrue(retried.success)
        self.assertEqual(calls, ["first:first.py", "first:second.py", "retry:second.py"])
        self.assertEqual(artifact_content, "committed-once\n")
        self.assertEqual(child["retryOfRunId"], parent["id"])
        self.assertEqual(child["metadata"]["retryMode"], "native")
        reused = next(step for step in child["steps"] if step["metadata"].get("stageId") == "step-first")
        self.assertEqual(reused["status"], "skipped")
        self.assertEqual(reused["metadata"]["reusedFromRunId"], parent["id"])

    def test_native_retry_reruns_stage_when_committed_artifact_is_missing_or_tampered(self):
        for mutation in ("missing", "tampered"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                paths = self._paths(root)
                first = self._script(root, "first.py")
                second = self._script(root, "second.py")
                artifact = diary_narrative_report_path(paths.diary_dir, "2026-07-10")
                artifact.parent.mkdir(parents=True, exist_ok=True)

                def first_attempt(command, **kwargs):
                    if Path(command[1]).name == "first.py":
                        artifact.write_text("committed-once\n", encoding="utf-8")
                        return subprocess.CompletedProcess(command, 0, "ok\n", "")
                    return subprocess.CompletedProcess(command, 1, "", "boom")

                run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("first", first), PipelineStep("second", second)],
                    runner=first_attempt,
                )
                parent = latest_pipeline_run_for_date(paths, "2026-07-10")
                if mutation == "missing":
                    artifact.unlink()
                else:
                    artifact.write_text("tampered\n", encoding="utf-8")
                calls = []

                def retry_attempt(command, **kwargs):
                    name = Path(command[1]).name
                    calls.append(name)
                    if name == "first.py":
                        artifact.write_text(f"repaired-{mutation}\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")

                result = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("first", first), PipelineStep("second", second)],
                    runner=retry_attempt,
                    retry_of_run_id=parent["id"],
                )
                child = latest_pipeline_run_for_date(paths, "2026-07-10")

                self.assertTrue(result.success)
                self.assertEqual(calls, ["first.py", "second.py"])
                self.assertEqual(artifact.read_text(encoding="utf-8"), f"repaired-{mutation}\n")
                first_outcome = next(step for step in child["steps"] if step["metadata"].get("stageId") == "step-first")
                self.assertEqual(first_outcome["status"], "completed")
                self.assertNotIn("reusedFromRunId", first_outcome["metadata"])

    def test_retry_falls_back_to_full_run_when_step_code_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            first = self._script(root, "first.py")
            second = self._script(root, "second.py")
            artifact = diary_narrative_report_path(paths.diary_dir, "2026-07-10")
            artifact.parent.mkdir(parents=True, exist_ok=True)

            def first_attempt(command, **kwargs):
                if Path(command[1]).name == "first.py":
                    artifact.write_text("committed-once\n", encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, "ok\n", "")
                return subprocess.CompletedProcess(command, 1, "", "boom")

            run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=first_attempt,
            )
            parent = latest_pipeline_run_for_date(paths, "2026-07-10")
            first.write_text("# changed implementation\n", encoding="utf-8")
            calls = []
            result = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=lambda command, **kwargs: calls.append(Path(command[1]).name)
                or subprocess.CompletedProcess(command, 0, "ok\n", ""),
                retry_of_run_id=parent["id"],
            )
            child = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertTrue(result.success)
        self.assertEqual(calls, ["first.py", "second.py"])
        self.assertEqual(child["metadata"]["retryMode"], "legacy-full")

    def test_native_retry_rechecks_non_artifact_rag_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            rag = self._script(root, "rag_v2_sync.py")
            readiness_calls = []

            def readiness(selected):
                readiness_calls.append(selected)
                return None

            with (
                patch("data_foundation.pipeline._skip_final_rag_reason", return_value=None),
                patch("data_foundation.pipeline._final_rag_readiness_blocking_reason", side_effect=readiness),
            ):
                initial = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("rag", rag)],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "boom"),
                    post_materializer=lambda day, selected: True,
                )
                parent = latest_pipeline_run_for_date(paths, "2026-07-10")
                retried = run_daily_pipeline(
                    "2026-07-10",
                    paths=paths,
                    steps=[PipelineStep("rag", rag)],
                    runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok\n", ""),
                    post_materializer=lambda day, selected: True,
                    retry_of_run_id=parent["id"],
                )
                child = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(initial.success)
        self.assertTrue(retried.success)
        self.assertEqual(readiness_calls, [paths, paths])
        readiness_outcomes = [
            step for step in child["steps"] if step["metadata"].get("stageId") == "final-rag-readiness"
        ]
        self.assertEqual(len(readiness_outcomes), 1)
        self.assertEqual(readiness_outcomes[0]["status"], "completed")
        self.assertFalse(readiness_outcomes[0]["metadata"]["committed"])

    def test_terminal_cas_loss_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            with patch("data_foundation.pipeline.finish_pipeline_run_if_status", return_value=False):
                result = run_daily_pipeline("2026-07-10", paths=paths, steps=[])
            ledger = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertFalse(result.success)
        self.assertEqual(result.failed_step, "Pipeline Terminal State")
        self.assertEqual(ledger["status"], "running")

    def test_legacy_retry_falls_back_to_full_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._paths(root)
            first = self._script(root, "first.py")
            second = self._script(root, "second.py")
            parent_id = create_pipeline_run(
                paths,
                business_date="2026-07-10",
                run_kind="manual",
                requested_by="cli",
                metadata={"trigger": "legacy"},
            )
            append_pipeline_step(paths, parent_id, name="first", status="completed")
            finish_pipeline_run(paths, parent_id, status="failed", failure_class="internal_error")
            calls = []

            result = run_daily_pipeline(
                "2026-07-10",
                paths=paths,
                steps=[PipelineStep("first", first), PipelineStep("second", second)],
                runner=lambda command, **kwargs: calls.append(Path(command[1]).name)
                or subprocess.CompletedProcess(command, 0, "ok\n", ""),
                retry_of_run_id=parent_id,
            )
            child = latest_pipeline_run_for_date(paths, "2026-07-10")

        self.assertTrue(result.success)
        self.assertEqual(calls, ["first.py", "second.py"])
        self.assertEqual(child["retryOfRunId"], parent_id)
        self.assertEqual(child["metadata"]["retryMode"], "legacy-full")


if __name__ == "__main__":
    unittest.main()
