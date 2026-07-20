import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import scheduler, service_manager
from data_foundation.paths import initialize_home
from data_foundation.settings import read_settings, write_settings
from data_foundation.settings_transaction import SettingsTransactionError
from data_foundation import settings_transaction, systemd_user
from data_foundation.systemd_user import (
    SystemdUserError,
    dashboard_unit,
    install_user_units,
    recover_user_unit_transactions,
)


class SyntheticSystemdCrash(BaseException):
    pass


class StatefulSystemctl:
    def __init__(self):
        self.states = {}
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        verb = command[2]
        if verb == "is-enabled":
            state = self.states.get(command[3], {"enabled": False, "active": False})
            return subprocess.CompletedProcess(command, 0 if state["enabled"] else 4, "", "")
        if verb == "is-active":
            state = self.states.get(command[3], {"enabled": False, "active": False})
            return subprocess.CompletedProcess(command, 0 if state["active"] else 4, "", "")
        if verb in {"enable", "disable"}:
            now = len(command) > 3 and command[3] == "--now"
            names = command[4:] if now else command[3:]
            for name in names:
                state = self.states.setdefault(name, {"enabled": False, "active": False})
                state["enabled"] = verb == "enable"
                if now:
                    state["active"] = verb == "enable"
            return subprocess.CompletedProcess(command, 0, "", "")
        if verb in {"start", "restart", "stop"}:
            for name in command[3:]:
                state = self.states.setdefault(name, {"enabled": False, "active": False})
                state["active"] = verb != "stop"
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class ServiceManagerTests(unittest.TestCase):
    def _runtime(self, root: Path):
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        write_settings({}, paths)
        return paths

    def _linux(self):
        return (
            patch("app.services.service_manager.platform.system", return_value="Linux"),
            patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
            patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
        )

    def test_linux_service_lifecycle_reconciles_definition_and_controls_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            runner = StatefulSystemctl()
            manager = service_manager.PlatformServiceManager(
                paths=paths,
                systemctl_runner=runner,
                unit_dir=unit_dir,
            )
            with self._linux()[0], self._linux()[1], self._linux()[2]:
                before = manager.preview("dashboard")
                installed = manager.install(
                    "dashboard",
                    {"confirmationText": "INSTALL ACTANARA DASHBOARD SERVICE"},
                )
                running = manager.preview("dashboard")
                write_settings({"dashboard": {"port": 4040}}, paths)
                updated = manager.update(
                    "dashboard",
                    {"confirmationText": "INSTALL ACTANARA DASHBOARD SERVICE"},
                )
                stopped = manager.stop(
                    "dashboard",
                    {"confirmationText": "STOP ACTANARA DASHBOARD SERVICE"},
                )
                started = manager.start(
                    "dashboard",
                    {"confirmationText": "START ACTANARA DASHBOARD SERVICE"},
                )
                restarted = manager.restart(
                    "dashboard",
                    {"confirmationText": "RESTART ACTANARA DASHBOARD SERVICE"},
                )
                removed = manager.uninstall(
                    "dashboard",
                    {"confirmationText": "UNINSTALL ACTANARA DASHBOARD SERVICE"},
                )

            unit_path = unit_dir / "actanara-dashboard.service"
            unit_exists_after_uninstall = unit_path.exists()
            settings = read_settings(paths)

        self.assertEqual(before["provider"], "systemd-user")
        self.assertFalse(before["registered"])
        self.assertEqual(installed["status"], "registered")
        self.assertTrue(running["registered"])
        self.assertTrue(running["actualRunning"])
        self.assertTrue(running["definitionsAligned"])
        self.assertIn("actanara-dashboard.service", updated["restartedUnits"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(started["status"], "running")
        self.assertEqual(restarted["status"], "running")
        self.assertEqual(removed["status"], "unregistered")
        self.assertFalse(unit_exists_after_uninstall)
        self.assertFalse(settings["dashboard"]["systemdUser"]["registered"])
        self.assertFalse(any("sudo" in item for command in runner.commands for item in command))

    def test_linux_service_settings_failure_compensates_unit_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            runner = StatefulSystemctl()
            manager = service_manager.PlatformServiceManager(
                paths=paths,
                systemctl_runner=runner,
                unit_dir=unit_dir,
            )

            def fail_after_external(phase, transaction_id):
                if phase == "after-precommit-side-effects":
                    raise OSError("synthetic settings failure")

            with (
                self._linux()[0],
                self._linux()[1],
                self._linux()[2],
                patch.object(settings_transaction, "settings_transaction_checkpoint", side_effect=fail_after_external),
                self.assertRaises(SettingsTransactionError),
            ):
                manager.install(
                    "rag",
                    {"confirmationText": "INSTALL ACTANARA RAG SERVICE"},
                )

            unit_exists = (unit_dir / "actanara-rag-server.service").exists()
            settings = read_settings(paths)

        self.assertFalse(unit_exists)
        self.assertFalse(((settings.get("rag") or {}).get("server") or {}).get("systemdUser", {}).get("registered", False))

    def test_systemd_interruption_recovers_prior_definition_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            runner = StatefulSystemctl()
            before_unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            after_unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 4040})
            target = unit_dir / before_unit.name
            target.write_text(before_unit.content, encoding="utf-8")
            runner.states[before_unit.name] = {"enabled": True, "active": True}

            def interrupt(phase, transaction_id):
                if phase == "after-definitions-applied":
                    raise SyntheticSystemdCrash()

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                patch.object(systemd_user, "systemd_transaction_checkpoint", side_effect=interrupt),
                self.assertRaises(SyntheticSystemdCrash),
            ):
                install_user_units(paths, [after_unit], unit_dir=unit_dir, runner=runner)

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                recovered = recover_user_unit_transactions(paths, runner=runner)
                recovered_again = recover_user_unit_transactions(paths, runner=runner)
            content = target.read_text(encoding="utf-8")

        self.assertEqual(content, before_unit.content)
        self.assertEqual(recovered[0]["status"], "compensated")
        self.assertEqual(recovered_again, [])
        self.assertEqual(runner.states[before_unit.name], {"enabled": True, "active": True})

    def test_interrupted_systemd_recovery_preserves_concurrent_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            runner = StatefulSystemctl()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            target = unit_dir / unit.name

            def interrupt(phase, transaction_id):
                if phase == "after-definitions-applied":
                    raise SyntheticSystemdCrash()

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                patch.object(systemd_user, "systemd_transaction_checkpoint", side_effect=interrupt),
                self.assertRaises(SyntheticSystemdCrash),
            ):
                install_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)
            target.write_text("# Managed by Actanara. Do not edit by hand.\n# concurrent\n", encoding="utf-8")
            concurrent = target.read_text(encoding="utf-8")
            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                recovered = recover_user_unit_transactions(paths, runner=runner)
            final_content = target.read_text(encoding="utf-8")

        self.assertEqual(recovered[0]["status"], "conflict")
        self.assertEqual(final_content, concurrent)

    def test_install_refuses_unmanaged_definition_before_systemctl_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            target = unit_dir / unit.name
            target.write_text("[Unit]\nDescription=operator owned\n", encoding="utf-8")
            runner = StatefulSystemctl()
            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                self.assertRaisesRegex(SystemdUserError, "unmanaged"),
            ):
                install_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)

        self.assertEqual(runner.commands, [])

    def test_macos_backend_delegates_existing_launchd_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._runtime(Path(tmp))
            manager = service_manager.PlatformServiceManager(paths=paths)
            with (
                patch("app.services.service_manager.platform.system", return_value="Darwin"),
                patch.object(service_manager.launcher, "install_dashboard_launch_agent", return_value={"status": "registered"}) as install,
            ):
                result = manager.install("dashboard", {"confirmationText": "existing launchd phrase"})

        self.assertEqual(result["serviceManager"], "launchd-user")
        install.assert_called_once_with({"confirmationText": "existing launchd phrase"})

    def test_linux_scheduler_installs_reconciles_old_label_and_safely_uninstalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            config_home = root / "config"
            unit_dir = config_home / "systemd" / "user"
            runner = StatefulSystemctl()
            write_settings(
                {
                    "schedule": {
                        "enabled": False,
                        "mode": "system",
                        "timezone": "UTC",
                        "dailyPipelineTime": "04:00",
                        "dashboardAggregationTime": "04:30",
                        "systemTimer": {
                            "provider": "systemd",
                            "label": "actanara.test-old",
                            "registered": False,
                        },
                    }
                },
                paths,
            )
            environment = {
                "ACTANARA_HOME": str(paths.home),
                "XDG_CONFIG_HOME": str(config_home),
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch("app.services.scheduler.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.scheduler_preview.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                installed = scheduler.install_system_timer(
                    {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION},
                    systemctl_runner=runner,
                    unit_dir=unit_dir,
                )
                preview = scheduler.preview_system_timer(paths, systemctl_runner=runner)
                write_settings(
                    {
                        "schedule": {
                            "dailyPipelineTime": "05:10",
                            "dashboardAggregationTime": "05:40",
                            "systemTimer": {"label": "actanara.test-new"},
                        }
                    },
                    paths,
                )
                updated = scheduler.install_system_timer(
                    {"confirmationText": scheduler.SCHEDULER_INSTALL_CONFIRMATION},
                    systemctl_runner=runner,
                    unit_dir=unit_dir,
                )
                old_units_exist = any(unit_dir.glob("actanara.test-old.*"))
                new_timer = unit_dir / "actanara.test-new.pipeline.timer"
                new_timer_content = new_timer.read_text(encoding="utf-8")
                removed = scheduler.uninstall_system_timer(
                    {"confirmationText": scheduler.SCHEDULER_UNINSTALL_CONFIRMATION},
                    systemctl_runner=runner,
                    unit_dir=unit_dir,
                )
                remaining = list(unit_dir.glob("actanara.test-*"))
                saved = read_settings(paths)

        self.assertEqual(len(installed["installed"]), 2)
        self.assertTrue(preview["actualRegistered"])
        self.assertTrue(all(job["runtimeStatus"]["definitionsAligned"] for job in preview["jobs"]))
        self.assertEqual(len(updated["handoff"]["transactions"]), 2)
        self.assertFalse(old_units_exist)
        self.assertIn("OnCalendar=*-*-* 05:10:00 UTC", new_timer_content)
        self.assertEqual(len(removed["removed"]), 2)
        self.assertEqual(remaining, [])
        self.assertFalse(saved["schedule"]["systemTimer"]["registered"])
        self.assertFalse(any("sudo" in item for command in runner.commands for item in command))


if __name__ == "__main__":
    unittest.main()
