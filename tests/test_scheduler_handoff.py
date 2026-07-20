import json
import os
import plistlib
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))
os.environ["ACTANARA_SECRET_BACKEND"] = "memory"

from app.services import scheduler
from data_foundation.onboarding_plan import (
    onboarding_apply_scheduler_plist_write,
    onboarding_apply_scheduler_register,
    onboarding_apply_scheduler_unregister,
)
from data_foundation.paths import initialize_home
from data_foundation.settings import read_settings, write_operator_settings, write_settings
from data_foundation.settings_status import actanara_settings_status
from data_foundation.time import detect_system_timezone_authority


class _LaunchdVector:
    def __init__(self, *, loaded=(), running=(), fail_bootstrap_once: str | None = None):
        self.loaded = set(loaded)
        self.running = set(running)
        self.fail_bootstrap_once = fail_bootstrap_once
        self.failed = False
        self.commands = []

    def operation(self, action, label, plist_path, *, allow_failure=False):
        self.commands.append((action, label, str(plist_path), allow_failure))
        if action == "bootout":
            self.loaded.discard(label)
            self.running.discard(label)
            return
        if action == "bootstrap":
            if label == self.fail_bootstrap_once and not self.failed:
                self.failed = True
                raise RuntimeError("synthetic bootstrap failure")
            self.loaded.add(label)
            self.running.discard(label)
            return
        raise AssertionError(action)

    def kickstart(self, label):
        if label not in self.loaded:
            raise RuntimeError("cannot kickstart unloaded job")
        self.running.add(label)

    def probe(self, label, plist_path, expected_plist):
        loaded = label in self.loaded
        aligned = False
        if loaded and expected_plist is not None and Path(plist_path).exists():
            with Path(plist_path).open("rb") as handle:
                aligned = plistlib.load(handle) == expected_plist
        return {
            "loaded": loaded,
            "running": label in self.running,
            "aligned": aligned,
            "reason": None,
        }


class SchedulerHandoffTests(unittest.TestCase):
    def setUp(self):
        platform_patcher = patch(
            "data_foundation.scheduler_preview.platform.system",
            return_value="Darwin",
        )
        platform_patcher.start()
        self.addCleanup(platform_patcher.stop)

    def _runtime(self, root: Path):
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        write_settings(
            {
                "schedule": {
                    "enabled": False,
                    "mode": "agent",
                    "timezone": "UTC",
                    "systemTimer": {"provider": "launchd", "label": "nova.handoff", "registered": False},
                }
            },
            paths,
        )
        return paths

    def _plist_path(self, root: Path, label: str) -> Path:
        return root / "LaunchAgents" / f"{label}.plist"

    def _patches(self, paths, root: Path, vector: _LaunchdVector):
        return (
            patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False),
            patch("data_foundation.scheduler_preview.detect_system_timezone_authority", return_value="UTC"),
            patch.object(scheduler, "_launch_agent_path", side_effect=lambda label: self._plist_path(root, label)),
            patch.object(scheduler, "_launchctl", side_effect=vector.operation),
            patch.object(scheduler, "_launchctl_kickstart", side_effect=vector.kickstart),
            patch.object(scheduler, "_probe_handoff_job", side_effect=vector.probe),
        )

    def test_install_commits_two_jobs_and_settings_as_one_explicit_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            vector = _LaunchdVector()
            with self._patches(paths, root, vector)[0], self._patches(paths, root, vector)[1], self._patches(paths, root, vector)[2], self._patches(paths, root, vector)[3], self._patches(paths, root, vector)[4], self._patches(paths, root, vector)[5]:
                result = scheduler.install_system_timer(
                    {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                )

            settings = read_settings(paths, redact_secrets=False)
            labels = {"nova.handoff.pipeline", "nova.handoff.dashboard-aggregation"}
            self.assertEqual(vector.loaded, labels)
            self.assertEqual(result["handoff"]["status"], "committed")
            self.assertEqual(set(result["handoff"]["jobs"]), labels)
            self.assertTrue(settings["schedule"]["enabled"])
            self.assertEqual(settings["schedule"]["mode"], "system")
            self.assertTrue(settings["schedule"]["systemTimer"]["registered"])
            journal_path = paths.state_dir / "scheduler-handoffs" / result["handoff"]["id"] / "journal.json"
            journal_text = journal_path.read_text(encoding="utf-8")
            self.assertEqual(json.loads(journal_text)["status"], "committed")
            self.assertNotIn(str(root), journal_text)

    def test_second_bootstrap_failure_restores_both_jobs_plists_modes_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            labels = ["nova.handoff.pipeline", "nova.handoff.dashboard-aggregation"]
            old_payloads = {}
            for index, label in enumerate(labels):
                path = self._plist_path(root, label)
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = {"Label": label, "ProgramArguments": [f"/old/python-{index}"], "WorkingDirectory": "/old"}
                path.write_bytes(plistlib.dumps(payload))
                path.chmod(0o640)
                old_payloads[label] = path.read_bytes()
            vector = _LaunchdVector(
                loaded=labels,
                running=[labels[0]],
                fail_bootstrap_once=labels[1],
            )
            settings_before = (paths.config_dir / "settings.json").read_bytes()
            patches = self._patches(paths, root, vector)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaisesRegex(ValueError, "settings transaction"):
                    scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )

            self.assertEqual((paths.config_dir / "settings.json").read_bytes(), settings_before)
            self.assertEqual(vector.loaded, set(labels))
            self.assertEqual(vector.running, {labels[0]})
            for label in labels:
                path = self._plist_path(root, label)
                self.assertEqual(path.read_bytes(), old_payloads[label])
                self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            journals = list((paths.state_dir / "scheduler-handoffs").glob("*/journal.json"))
            self.assertEqual(len(journals), 1)
            self.assertEqual(json.loads(journals[0].read_text(encoding="utf-8"))["status"], "compensated")

    def test_uninstall_handoff_removes_both_jobs_and_commits_agent_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            vector = _LaunchdVector()
            patches = self._patches(paths, root, vector)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                scheduler.install_system_timer(
                    {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                )
                result = scheduler.uninstall_system_timer(
                    {
                        "confirmationText": scheduler.SCHEDULER_UNINSTALL_CONFIRMATION,
                        "targetMode": "agent",
                    }
                )

            settings = read_settings(paths, redact_secrets=False)
            self.assertEqual(result["handoff"]["status"], "committed")
            self.assertEqual(vector.loaded, set())
            self.assertFalse(any((root / "LaunchAgents").glob("*.plist")))
            self.assertTrue(settings["schedule"]["enabled"])
            self.assertEqual(settings["schedule"]["mode"], "agent")
            self.assertFalse(settings["schedule"]["systemTimer"]["registered"])

    def test_concurrent_settings_write_is_preserved_while_external_vector_is_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            vector = _LaunchdVector()
            changed = False

            def checkpoint(phase, _transaction_id):
                nonlocal changed
                if phase != "after-precommit-side-effects" or changed:
                    return
                raw = json.loads((paths.config_dir / "settings.json").read_text(encoding="utf-8"))
                raw["schedule"]["dailyPipelineTime"] = "05:55"
                (paths.config_dir / "settings.json").write_text(json.dumps(raw), encoding="utf-8")
                changed = True

            patches = self._patches(paths, root, vector)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch(
                "data_foundation.settings_transaction.settings_transaction_checkpoint",
                side_effect=checkpoint,
            ):
                with self.assertRaisesRegex(ValueError, "conflict"):
                    scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )

            self.assertEqual(read_settings(paths)["schedule"]["dailyPipelineTime"], "05:55")
            self.assertEqual(vector.loaded, set())
            self.assertFalse(any((root / "LaunchAgents").glob("*.plist")))

    def test_interrupted_external_phase_recovers_prior_vector_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            vector = _LaunchdVector()
            settings_before = (paths.config_dir / "settings.json").read_bytes()

            def checkpoint(phase, _transaction_id):
                if phase == "after-external-applied":
                    raise SystemExit(91)

            patches = self._patches(paths, root, vector)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch.object(
                scheduler,
                "scheduler_handoff_checkpoint",
                side_effect=checkpoint,
            ):
                with self.assertRaises(SystemExit):
                    scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )
                first = scheduler.recover_scheduler_handoffs(paths)
                second = scheduler.recover_scheduler_handoffs(paths)

            self.assertEqual((paths.config_dir / "settings.json").read_bytes(), settings_before)
            self.assertEqual(vector.loaded, set())
            self.assertFalse(any((root / "LaunchAgents").glob("*.plist")))
            self.assertEqual(first[0]["status"], "compensated")
            self.assertEqual(second, [])

    def test_interrupted_after_settings_commit_recovers_as_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            vector = _LaunchdVector()

            def checkpoint(phase, _transaction_id):
                if phase == "after-settings-committed":
                    raise SystemExit(92)

            patches = self._patches(paths, root, vector)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patch.object(
                scheduler,
                "scheduler_handoff_checkpoint",
                side_effect=checkpoint,
            ):
                with self.assertRaises(SystemExit):
                    scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )
                recovery = scheduler.recover_scheduler_handoffs(paths)

            self.assertTrue(read_settings(paths)["schedule"]["systemTimer"]["registered"])
            self.assertEqual(len(vector.loaded), 2)
            self.assertEqual(recovery[0]["status"], "committed")

    def test_timezone_mismatch_is_read_compatible_but_new_write_register_and_doctor_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            settings_path = paths.config_dir / "settings.json"
            before = settings_path.read_bytes()
            with (
                patch("data_foundation.settings.platform.system", return_value="Darwin"),
                patch("data_foundation.settings.detect_system_timezone_authority", return_value="UTC"),
            ):
                with self.assertRaisesRegex(ValueError, "must match the macOS system timezone"):
                    write_operator_settings({"schedule": {"timezone": "Asia/Hong_Kong"}}, paths)
                write_operator_settings({"schedule": {"timezone": "UTC"}}, paths)
            self.assertNotEqual(settings_path.read_bytes(), before)

            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            raw["schedule"]["timezone"] = "Asia/Hong_Kong"
            settings_path.write_text(json.dumps(raw), encoding="utf-8")
            vector = _LaunchdVector()
            patches = self._patches(paths, root, vector)
            with patches[0], patches[2], patches[3], patches[4], patches[5], patch(
                "data_foundation.scheduler_preview.detect_system_timezone_authority",
                return_value="UTC",
            ):
                with self.assertRaisesRegex(ValueError, "Blocked: scheduler-timezone-mismatch"):
                    scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )
                doctor = actanara_settings_status(paths, doctor_profile="scheduler")

            check = next(item for item in doctor["checks"] if item["id"] == "scheduler-timezone-boundary")
            self.assertEqual(check["status"], "error")
            self.assertIn("Blocked: scheduler-timezone-mismatch", check["message"])
            self.assertEqual(vector.commands, [])
            self.assertEqual(read_settings(paths)["schedule"]["timezone"], "Asia/Hong_Kong")

    def test_ordinary_settings_save_requires_explicit_handoff_for_ownership_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            write_settings(
                {
                    "schedule": {
                        "enabled": True,
                        "mode": "system",
                        "systemTimer": {"registered": True},
                    }
                },
                paths,
            )
            before = (paths.config_dir / "settings.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "scheduler-handoff-required"):
                write_operator_settings({"schedule": {"enabled": True, "mode": "agent"}}, paths)

            self.assertEqual((paths.config_dir / "settings.json").read_bytes(), before)

    @unittest.skipUnless(
        os.getenv("ACTANARA_RUN_REAL_LAUNCHD_TESTS") == "1" and platform.system() == "Darwin",
        "real isolated launchd handoff not requested",
    )
    def test_real_unique_launchd_install_doctor_uninstall_handoff(self):
        with tempfile.TemporaryDirectory(prefix="actanara-scheduler-live-") as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            label = f"com.actanara.session-d.handoff.{os.getpid()}"
            system_timezone = detect_system_timezone_authority()
            self.assertTrue(system_timezone)
            write_settings(
                {
                    "schedule": {
                        "enabled": False,
                        "mode": "agent",
                        "timezone": system_timezone,
                        "dailyPipelineTime": "00:01",
                        "dashboardAggregationTime": "00:02",
                        "systemTimer": {"provider": "launchd", "label": label, "registered": False},
                    }
                },
                paths,
            )

            def plist_path(job_label: str) -> Path:
                return root / "Home" / "Library" / "LaunchAgents" / f"{job_label}.plist"

            labels = [f"{label}.pipeline", f"{label}.dashboard-aggregation"]
            try:
                with (
                    patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home), "HOME": str(root / "Home")}, clear=False),
                    patch.object(scheduler, "_launch_agent_path", side_effect=plist_path),
                ):
                    installed = scheduler.install_system_timer(
                        {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION}
                    )
                    preview = scheduler.preview_system_timer(
                        paths,
                        probe_runtime=True,
                        launch_agent_home=root / "Home",
                    )
                    removed = scheduler.uninstall_system_timer(
                        {
                            "confirmationText": scheduler.SCHEDULER_UNINSTALL_CONFIRMATION,
                            "targetMode": "agent",
                        }
                    )

                self.assertEqual(installed["handoff"]["status"], "committed")
                self.assertTrue(preview["actualRegistered"])
                self.assertFalse(preview["provenanceMismatch"])
                self.assertEqual(removed["handoff"]["status"], "committed")
                self.assertTrue(read_settings(paths)["schedule"]["enabled"])
                self.assertEqual(read_settings(paths)["schedule"]["mode"], "agent")
                for job_label in labels:
                    result = subprocess.run(
                        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{job_label}"],
                        capture_output=True,
                        text=True,
                    )
                    self.assertIn(result.returncode, {3, 113})
            finally:
                for job_label in labels:
                    subprocess.run(
                        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{job_label}"],
                        capture_output=True,
                        text=True,
                    )

    @unittest.skipUnless(
        os.getenv("ACTANARA_RUN_REAL_LAUNCHD_TESTS") == "1" and platform.system() == "Darwin",
        "real isolated launchd handoff not requested",
    )
    def test_real_unique_onboarding_cli_register_unregister_uses_same_handoff(self):
        with tempfile.TemporaryDirectory(prefix="actanara-scheduler-onboarding-live-") as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            launch_agent_home = root / "Home"
            label = f"com.actanara.session-d.onboarding-handoff.{os.getpid()}"
            system_timezone = detect_system_timezone_authority()
            self.assertTrue(system_timezone)
            write_settings(
                {
                    "schedule": {
                        "enabled": False,
                        "mode": "agent",
                        "timezone": system_timezone,
                        "dailyPipelineTime": "00:03",
                        "dashboardAggregationTime": "00:04",
                        "systemTimer": {"provider": "launchd", "label": label, "registered": False},
                    }
                },
                paths,
            )
            labels = [f"{label}.pipeline", f"{label}.dashboard-aggregation"]
            try:
                written = onboarding_apply_scheduler_plist_write(
                    ["dashboard"],
                    paths,
                    launch_agent_home=launch_agent_home,
                    confirmation_text="WRITE ACTANARA LAUNCHAGENTS",
                )
                registered = onboarding_apply_scheduler_register(
                    ["dashboard"],
                    paths,
                    launch_agent_home=launch_agent_home,
                    confirmation_text="REGISTER ACTANARA SCHEDULER",
                )
                preview = scheduler.preview_system_timer(
                    paths,
                    probe_runtime=True,
                    launch_agent_home=launch_agent_home,
                )
                unregistered = onboarding_apply_scheduler_unregister(
                    ["dashboard"],
                    paths,
                    launch_agent_home=launch_agent_home,
                    confirmation_text="UNREGISTER ACTANARA SCHEDULER",
                )

                self.assertEqual(written["status"], "scheduler-plist-applied")
                self.assertEqual(registered["status"], "scheduler-registered")
                self.assertEqual(registered["handoff"]["status"], "committed")
                self.assertTrue(preview["actualRegistered"])
                self.assertFalse(preview["provenanceMismatch"])
                self.assertEqual(unregistered["status"], "scheduler-unregistered")
                self.assertEqual(unregistered["handoff"]["status"], "committed")
                self.assertFalse(any((launch_agent_home / "Library" / "LaunchAgents").glob("*.plist")))
                journal_text = "\n".join(
                    path.read_text(encoding="utf-8")
                    for path in (paths.state_dir / "scheduler-handoffs").glob("*/journal.json")
                )
                self.assertNotIn(str(root), journal_text)
            finally:
                for job_label in labels:
                    subprocess.run(
                        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{job_label}"],
                        capture_output=True,
                        text=True,
                    )


if __name__ == "__main__":
    unittest.main()
