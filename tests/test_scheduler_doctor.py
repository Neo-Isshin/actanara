import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.environ["ACTANARA_SECRET_BACKEND"] = "memory"

from data_foundation.paths import initialize_home
from data_foundation.scheduler_preview import preview_system_timer
from data_foundation.settings import write_settings
from data_foundation import settings_status


def _write_preview_plists(preview: dict) -> None:
    for job in preview["jobs"]:
        path = Path(job["plistPath"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(job["managedPlist"]["plist"], handle)


def _launchctl_output(job: dict) -> str:
    plist = job["managedPlist"]["plist"]
    arguments = plist["ProgramArguments"]
    environment = plist["EnvironmentVariables"]
    return "\n".join(
        [
            f"program = {arguments[0]}",
            "arguments = {",
            *[f"  {argument}" for argument in arguments],
            "}",
            f"working directory = {plist['WorkingDirectory']}",
            "environment = {",
            f"  ACTANARA_HOME => {environment['ACTANARA_HOME']}",
            f"  PYTHONPATH => {environment['PYTHONPATH']}",
            "}",
            "state = not running",
        ]
    )


class SchedulerDoctorTests(unittest.TestCase):
    def _runtime(self, root: Path, *, mode: str = "system", enabled: bool = True, registered: bool = True):
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        release = paths.home / "app" / "releases" / "fixture-release"
        venv = paths.home / "app" / "venvs" / "fixture-venv"
        (release / "src" / "dashboard").mkdir(parents=True)
        (release / "advanced" / "pipeline").mkdir(parents=True)
        for script in ("run_daily_pipeline.py", "run_dashboard_foundation_refresh.py"):
            (release / "advanced" / "pipeline" / script).write_text(
                "# scheduler fixture\n",
                encoding="utf-8",
            )
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        (paths.home / "app" / "source").symlink_to(Path("releases") / release.name)
        (paths.home / ".venv").symlink_to(Path("app") / "venvs" / venv.name)
        write_settings(
            {
                "pipeline": {
                    "pythonExecutable": sys.executable,
                    "workingDirectory": str(ROOT),
                },
                "schedule": {
                    "enabled": enabled,
                    "mode": mode,
                    "systemTimer": {
                        "provider": "launchd",
                        "label": "actanara.test-scheduler",
                        "registered": registered,
                    },
                },
            },
            paths,
        )
        return paths

    def _launchd_preview(self, paths, fake_home: Path, runner):
        with (
            patch("data_foundation.scheduler_preview.platform.system", return_value="Darwin"),
            patch("data_foundation.scheduler_preview.os.getuid", return_value=501),
        ):
            return preview_system_timer(
                paths,
                launch_agent_home=fake_home,
                probe_runtime=True,
                launchctl_runner=runner,
            )

    def test_probe_covers_both_jobs_and_accepts_aligned_loaded_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            fake_home = root / "Home"
            base = preview_system_timer(paths, launch_agent_home=fake_home)
            _write_preview_plists(base)
            jobs = {job["label"]: job for job in base["jobs"]}

            def runner(command, **_kwargs):
                label = command[-1].rsplit("/", 1)[-1]
                return subprocess.CompletedProcess(command, 0, _launchctl_output(jobs[label]), "")

            preview = self._launchd_preview(paths, fake_home, runner)

        self.assertEqual({job["kind"] for job in preview["jobs"]}, {"daily-pipeline", "dashboard-aggregation"})
        self.assertTrue(preview["actualRegistered"])
        self.assertFalse(preview["desiredActualMismatch"])
        self.assertFalse(preview["provenanceMismatch"])
        self.assertEqual(preview["runtimeProbe"]["alignedJobs"], 2)
        for job in preview["jobs"]:
            runtime = job["runtimeStatus"]
            self.assertTrue(runtime["launchctlLoaded"])
            self.assertTrue(runtime["provenanceAligned"])
            self.assertEqual(runtime["issueCodes"], [])
            self.assertEqual(runtime["expectedDefinitionHash"], runtime["loadedDefinition"]["definitionHash"])

    def test_probe_classifies_loaded_deleted_runtime_without_returning_raw_launchctl_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            fake_home = root / "Home"
            base = preview_system_timer(paths, launch_agent_home=fake_home)
            _write_preview_plists(base)
            deleted_root = root / "deleted-test-runtime"

            def runner(command, **_kwargs):
                label = command[-1].rsplit("/", 1)[-1]
                script = "run_daily_pipeline.py" if label.endswith(".pipeline") else "run_dashboard_foundation_refresh.py"
                output = "\n".join(
                    [
                        f"program = {deleted_root / '.venv' / 'bin' / 'python'}",
                        "arguments = {",
                        f"  {deleted_root / '.venv' / 'bin' / 'python'}",
                        f"  {deleted_root / 'advanced' / 'pipeline' / script}",
                        "}",
                        f"working directory = {deleted_root}",
                        "environment = {",
                        f"  ACTANARA_HOME => {deleted_root / 'Actanara'}",
                        f"  PYTHONPATH => {deleted_root}:{deleted_root / 'src'}:{deleted_root / 'src' / 'dashboard'}",
                        "}",
                    ]
                )
                return subprocess.CompletedProcess(command, 0, output, "")

            preview = self._launchd_preview(paths, fake_home, runner)

        self.assertTrue(preview["actualRegistered"])
        self.assertTrue(preview["provenanceMismatch"])
        self.assertEqual(preview["runtimeProbe"]["mismatchedJobs"], 2)
        for job in preview["jobs"]:
            issues = set(job["runtimeStatus"]["issueCodes"])
            self.assertIn("program-mismatch", issues)
            self.assertIn("working-directory-mismatch", issues)
            self.assertIn("actanara-home-mismatch", issues)
            self.assertIn("pythonpath-mismatch", issues)
            self.assertIn("program-target-missing", issues)
            self.assertNotIn(str(deleted_root), json.dumps(job["runtimeStatus"], sort_keys=True))

    def test_scheduler_doctor_reports_redacted_per_job_mismatch_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            fake_home = root / "Home"
            base = preview_system_timer(paths, launch_agent_home=fake_home)
            _write_preview_plists(base)
            deleted_root = root / "private-deleted-runtime"

            def runner(command, **_kwargs):
                output = "\n".join(
                    [
                        f"program = {deleted_root / 'python'}",
                        "arguments = {",
                        f"  {deleted_root / 'python'}",
                        f"  {deleted_root / 'job.py'}",
                        "}",
                        f"working directory = {deleted_root}",
                        "environment = {",
                        f"  ACTANARA_HOME => {deleted_root / 'home'}",
                        f"  PYTHONPATH => {deleted_root / 'src'}",
                        "}",
                    ]
                )
                return subprocess.CompletedProcess(command, 0, output, "")

            raw_preview = self._launchd_preview(paths, fake_home, runner)
            with patch.object(settings_status, "preview_system_timer", return_value=raw_preview):
                payload = settings_status.actanara_settings_status(paths, doctor_profile="scheduler")

        registration = payload["schedulerRegistration"]
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(registration["status"], "mismatch")
        self.assertEqual(len(registration["jobs"]), 2)
        self.assertEqual(checks["scheduler-desired-actual"]["status"], "warn")
        self.assertEqual(checks["scheduler-job:daily-pipeline"]["status"], "warn")
        self.assertEqual(checks["scheduler-job:dashboard-aggregation"]["status"], "warn")
        safe_output = json.dumps(
            {
                "registration": registration,
                "checks": [check for check in payload["checks"] if check["id"].startswith("scheduler-")],
            },
            sort_keys=True,
        )
        self.assertNotIn(str(deleted_root), safe_output)
        self.assertNotIn("job.py", safe_output)
        self.assertRegex(registration["jobs"][0]["definitionHashes"]["loaded"], r"^[0-9a-f]{64}$")

    def test_agent_mode_explicitly_expects_both_system_jobs_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root, mode="agent", enabled=True, registered=False)
            fake_home = root / "Home"

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(command, 113, "", f"not found at {root / 'private-path'}")

            raw_preview = self._launchd_preview(paths, fake_home, runner)
            with patch.object(settings_status, "preview_system_timer", return_value=raw_preview):
                payload = settings_status.actanara_settings_status(paths, doctor_profile="scheduler")

        registration = payload["schedulerRegistration"]
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(registration["expectedActualState"], "absent")
        self.assertEqual(registration["actualState"], "absent")
        self.assertEqual(registration["status"], "aligned")
        self.assertEqual(checks["scheduler-desired-actual"]["status"], "ok")
        self.assertEqual(checks["scheduler-job:daily-pipeline"]["status"], "ok")
        self.assertEqual(checks["scheduler-job:dashboard-aggregation"]["status"], "ok")
        self.assertNotIn(str(root / "private-path"), json.dumps(registration, sort_keys=True))

    def test_agent_mode_flags_even_one_partially_loaded_system_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root, mode="agent", enabled=True, registered=False)
            fake_home = root / "Home"
            base = preview_system_timer(paths, launch_agent_home=fake_home)
            _write_preview_plists(base)
            jobs = {job["label"]: job for job in base["jobs"]}

            def runner(command, **_kwargs):
                label = command[-1].rsplit("/", 1)[-1]
                if label.endswith(".pipeline"):
                    return subprocess.CompletedProcess(command, 0, _launchctl_output(jobs[label]), "")
                return subprocess.CompletedProcess(command, 113, "", "not loaded")

            raw_preview = self._launchd_preview(paths, fake_home, runner)
            with patch.object(settings_status, "preview_system_timer", return_value=raw_preview):
                payload = settings_status.actanara_settings_status(paths, doctor_profile="scheduler")

        registration = payload["schedulerRegistration"]
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertEqual(registration["actualState"], "partial")
        self.assertTrue(registration["desiredActualMismatch"])
        self.assertEqual(checks["scheduler-desired-actual"]["status"], "warn")
        self.assertEqual(checks["scheduler-job:daily-pipeline"]["status"], "warn")
        self.assertEqual(checks["scheduler-job:dashboard-aggregation"]["status"], "ok")

    def test_unexpected_launchctl_failure_stays_unknown_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            fake_home = root / "Home"
            private_text = str(root / "private-launchctl-error")

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(command, 2, "", private_text)

            preview = self._launchd_preview(paths, fake_home, runner)

        self.assertIsNone(preview["actualRegistered"])
        self.assertEqual(preview["runtimeProbe"]["status"], "unknown")
        for job in preview["jobs"]:
            runtime = job["runtimeStatus"]
            self.assertIsNone(runtime["launchctlLoaded"])
            self.assertEqual(runtime["reason"], "launchctl-unexpected-returncode")
            self.assertNotIn(private_text, json.dumps(runtime, sort_keys=True))

    def test_unimplemented_provider_is_clear_and_agent_mode_remains_expected_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            write_settings(
                {
                    "schedule": {
                        "enabled": True,
                        "mode": "agent",
                        "systemTimer": {"provider": "systemd", "registered": False},
                    }
                },
                paths,
            )
            with patch("data_foundation.scheduler_preview.platform.system", return_value="Darwin"):
                raw_preview = preview_system_timer(paths, probe_runtime=True)
            with patch.object(settings_status, "preview_system_timer", return_value=raw_preview):
                payload = settings_status.actanara_settings_status(paths, doctor_profile="scheduler")

        registration = payload["schedulerRegistration"]
        checks = {check["id"]: check for check in payload["checks"]}
        self.assertFalse(registration["registrationImplemented"])
        self.assertFalse(registration["supported"])
        self.assertEqual(registration["expectedActualState"], "absent")
        self.assertEqual(registration["status"], "expected-absent")
        self.assertEqual(checks["scheduler-provider"]["status"], "ok")
        self.assertIn("expected absent", checks["scheduler-provider"]["message"])


if __name__ == "__main__":
    unittest.main()
