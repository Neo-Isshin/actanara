import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from data_foundation.scheduler_preview import preview_system_timer
from data_foundation.settings import write_settings
from data_foundation.systemd_user import (
    SystemdUserError,
    dashboard_unit,
    install_user_units,
    linger_status,
    rag_unit,
    scheduler_units,
)


class SystemdUserTests(unittest.TestCase):
    def _runtime(self, root: Path):
        return initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")

    def test_scheduler_units_bind_only_stable_runtime_pointers(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._runtime(Path(tmp))
            units = scheduler_units(
                paths,
                {
                    "timezone": "Asia/Hong_Kong",
                    "dailyPipelineTime": "03:10",
                    "dashboardAggregationTime": "03:40",
                },
                {"label": "actanara.test"},
            )

        by_name = {unit.name: unit for unit in units}
        self.assertEqual(
            set(by_name),
            {
                "actanara.test.pipeline.service",
                "actanara.test.pipeline.timer",
                "actanara.test.dashboard-aggregation.service",
                "actanara.test.dashboard-aggregation.timer",
            },
        )
        service = by_name["actanara.test.pipeline.service"].content
        timer = by_name["actanara.test.pipeline.timer"].content
        self.assertIn(str(paths.home / ".venv" / "bin" / "python"), service)
        self.assertIn(str(paths.home / "app" / "source"), service)
        self.assertIn("OnCalendar=*-*-* 03:10:00 Asia/Hong_Kong", timer)
        self.assertFalse(by_name["actanara.test.pipeline.service"].enable_now)
        self.assertTrue(by_name["actanara.test.pipeline.timer"].enable_now)

    def test_dashboard_and_rag_units_are_user_services_without_shell_wrappers(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._runtime(Path(tmp))
            dashboard = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            rag = rag_unit(paths)

        self.assertEqual(dashboard.name, "actanara-dashboard.service")
        self.assertIn("uvicorn", dashboard.content)
        self.assertIn("Restart=on-failure", dashboard.content)
        self.assertNotIn("/bin/zsh", dashboard.content)
        self.assertEqual(rag.name, "actanara-rag-server.service")
        self.assertIn("rag_server_launch_agent.py", rag.content)

    def test_install_writes_private_units_and_uses_only_systemctl_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "Home" / ".config" / "systemd" / "user"
            units = scheduler_units(
                paths,
                {"timezone": "UTC", "dailyPipelineTime": "04:00", "dashboardAggregationTime": "04:30"},
                {"label": "actanara.test"},
            )
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                if "show-user" in command:
                    return subprocess.CompletedProcess(command, 0, "no\n", "")
                return subprocess.CompletedProcess(command, 0, "enabled\n", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                patch("data_foundation.systemd_user.shutil.which", return_value="/usr/bin/loginctl"),
            ):
                result = install_user_units(paths, units, unit_dir=unit_dir, runner=runner)

            self.assertEqual(result["status"], "installed")
            self.assertFalse(result["linger"]["enabled"])
            self.assertFalse(result["linger"]["changed"])
            self.assertTrue(all(command[1] == "--user" for command in commands if "systemctl" in command[0]))
            self.assertFalse(any("sudo" in command for command in commands))
            self.assertEqual(unit_dir.stat().st_mode & 0o777, 0o700)
            for unit in units:
                self.assertEqual((unit_dir / unit.name).stat().st_mode & 0o777, 0o600)

    def test_install_failure_restores_preexisting_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            units = scheduler_units(
                paths,
                {"timezone": "UTC", "dailyPipelineTime": "04:00", "dashboardAggregationTime": "04:30"},
                {"label": "actanara.test"},
            )
            existing = unit_dir / units[0].name
            existing.write_text("prior\n", encoding="utf-8")

            def runner(command, **kwargs):
                if "enable" in command:
                    return subprocess.CompletedProcess(command, 9, "", "failed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                with self.assertRaises(SystemdUserError):
                    install_user_units(paths, units, unit_dir=unit_dir, runner=runner)
            self.assertEqual(existing.read_text(encoding="utf-8"), "prior\n")
            self.assertFalse((unit_dir / units[1].name).exists())

    def test_install_failure_restores_prior_enablement_and_stops_new_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            units = scheduler_units(
                paths,
                {"timezone": "UTC", "dailyPipelineTime": "04:00", "dashboardAggregationTime": "04:30"},
                {"label": "actanara.test"},
            )
            timer_names = [unit.name for unit in units if unit.enable_now]
            commands = []
            status_calls = {name: 0 for name in timer_names}

            def runner(command, **kwargs):
                commands.append(command)
                verb = command[2]
                if verb in {"is-enabled", "is-active"}:
                    name = command[3]
                    status_calls[name] += 1
                    if status_calls[name] <= 2 and name == timer_names[0]:
                        return subprocess.CompletedProcess(command, 0, "yes\n", "")
                    return subprocess.CompletedProcess(command, 4, "no\n", "")
                if verb == "enable" and len(command) > 4:
                    return subprocess.CompletedProcess(command, 9, "", "failed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                with self.assertRaises(SystemdUserError):
                    install_user_units(paths, units, unit_dir=unit_dir, runner=runner)
            units_present_after_rollback = [
                unit.name for unit in units if (unit_dir / unit.name).exists()
            ]

        self.assertTrue(any(command[2:4] == ["disable", "--now"] for command in commands))
        self.assertTrue(
            any(command[2:] == ["enable", "--now", timer_names[0]] for command in commands)
        )
        self.assertEqual(units_present_after_rollback, [])

    def test_linger_probe_is_diagnostic_only(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "no\n", "")

        with (
            patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
            patch("data_foundation.systemd_user.shutil.which", return_value="/usr/bin/loginctl"),
        ):
            result = linger_status(runner=runner)

        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["enabled"])
        self.assertFalse(result["changed"])

    def test_scheduler_preview_probes_systemd_without_mutating_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            write_settings(
                {
                    "schedule": {
                        "enabled": True,
                        "mode": "system",
                        "timezone": "UTC",
                        "systemTimer": {
                            "provider": "systemd",
                            "label": "actanara.test",
                            "registered": True,
                        },
                    }
                },
                paths,
            )
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "active\n", "")

            with (
                patch("data_foundation.scheduler_preview.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
            ):
                preview = preview_system_timer(
                    paths,
                    probe_runtime=True,
                    systemctl_runner=runner,
                )

        self.assertTrue(preview["supported"])
        self.assertTrue(preview["installerRegistrationImplemented"])
        self.assertFalse(preview["registrationImplemented"])
        self.assertTrue(preview["actualRegistered"])
        self.assertFalse(preview["registrationMismatch"])
        self.assertEqual(len(commands), 4)
        self.assertTrue(all(command[1] == "--user" for command in commands))


if __name__ == "__main__":
    unittest.main()
