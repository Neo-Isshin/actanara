import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home, runtime_paths_for_home
from data_foundation.scheduler_preview import preview_system_timer
from data_foundation.settings import write_settings
from data_foundation.settings_status import actanara_settings_status
from data_foundation.systemd_user import (
    SystemdUserError,
    dashboard_unit,
    enable_linger,
    install_user_units,
    linger_status,
    rag_unit,
    scheduler_units,
    uninstall_user_units,
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

    def test_working_directory_is_an_unquoted_scalar_path(self):
        paths = runtime_paths_for_home(Path("/tmp/Actanara path%prod"))
        dashboard = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})

        self.assertIn(
            "WorkingDirectory=/tmp/Actanara path%%prod/app/source",
            dashboard.content,
        )
        self.assertNotIn('WorkingDirectory="', dashboard.content)

    def test_systemctl_failure_includes_the_available_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})

            def runner(command, **kwargs):
                if command[2] == "is-enabled":
                    return subprocess.CompletedProcess(command, 4, "", "")
                if command[2] == "is-active":
                    return subprocess.CompletedProcess(command, 4, "", "")
                if command[2] == "enable":
                    return subprocess.CompletedProcess(command, 1, "", "unit rejected\n")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch(
                    "data_foundation.systemd_user._systemctl_binary",
                    return_value="/usr/bin/systemctl",
                ),
                self.assertRaisesRegex(SystemdUserError, "unit rejected"),
            ):
                install_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)

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
                    status_calls.setdefault(name, 0)
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

    def test_install_compensates_when_service_exits_during_stability_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            enabled = False
            active_probes = 0
            commands = []

            def runner(command, **kwargs):
                nonlocal enabled, active_probes
                commands.append(command)
                verb = command[2]
                if verb == "is-enabled":
                    return subprocess.CompletedProcess(command, 0 if enabled else 4, "", "")
                if verb == "is-active":
                    if not enabled:
                        return subprocess.CompletedProcess(command, 4, "", "")
                    active_probes += 1
                    return subprocess.CompletedProcess(
                        command,
                        0 if active_probes == 1 else 4,
                        "",
                        "",
                    )
                if verb == "enable":
                    enabled = True
                if verb == "disable":
                    enabled = False
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch(
                    "data_foundation.systemd_user._systemctl_binary",
                    return_value="/usr/bin/systemctl",
                ),
                patch("data_foundation.systemd_user.time.sleep"),
                self.assertRaisesRegex(SystemdUserError, "did not become enabled and active"),
            ):
                install_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)

            definition_exists = (unit_dir / unit.name).exists()

        self.assertFalse(definition_exists)
        self.assertFalse(enabled)
        self.assertTrue(any(command[2:4] == ["disable", "--now"] for command in commands))

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

    def test_enable_linger_targets_current_user_without_sudo_and_verifies_state(self):
        commands = []
        probes = iter(("no\n", "yes\n"))

        def runner(command, **kwargs):
            commands.append(command)
            if command[1] == "show-user":
                return subprocess.CompletedProcess(command, 0, next(probes), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
            patch("data_foundation.systemd_user.shutil.which", return_value="/usr/bin/loginctl"),
        ):
            result = enable_linger(runner=runner)

        self.assertTrue(result["enabled"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["action"], "enabled")
        self.assertEqual(
            commands[1],
            ["/usr/bin/loginctl", "enable-linger", str(os.getuid())],
        )
        self.assertFalse(any("sudo" in item for command in commands for item in command))

    def test_enable_linger_reports_authorization_failure_without_sudo_fallback(self):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            if command[1] == "show-user":
                return subprocess.CompletedProcess(command, 0, "no\n", "")
            return subprocess.CompletedProcess(command, 1, "", "access denied\n")

        with (
            patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
            patch("data_foundation.systemd_user.shutil.which", return_value="/usr/bin/loginctl"),
            self.assertRaisesRegex(SystemdUserError, "access denied"),
        ):
            enable_linger(runner=runner)

        self.assertFalse(any("sudo" in item for command in commands for item in command))

    def test_uninstall_removes_only_managed_units_and_stops_all_jobs(self):
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
            states = {unit.name: {"enabled": unit.enable_now, "active": True} for unit in units}
            commands = []
            for unit in units:
                (unit_dir / unit.name).write_text(unit.content, encoding="utf-8")

            def runner(command, **kwargs):
                commands.append(command)
                verb = command[2]
                if verb == "is-enabled":
                    return subprocess.CompletedProcess(command, 0 if states[command[3]]["enabled"] else 4, "", "")
                if verb == "is-active":
                    return subprocess.CompletedProcess(command, 0 if states[command[3]]["active"] else 4, "", "")
                if verb == "disable":
                    for name in command[4:]:
                        states[name] = {"enabled": False, "active": False}
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                patch("data_foundation.systemd_user.shutil.which", return_value=None),
            ):
                result = uninstall_user_units(paths, units, unit_dir=unit_dir, runner=runner)

            remaining = [unit.name for unit in units if (unit_dir / unit.name).exists()]

        self.assertEqual(result["status"], "uninstalled")
        self.assertFalse(result["probe"]["actualRegistered"])
        self.assertEqual(set(result["removedUnits"]), {unit.name for unit in units})
        self.assertEqual(remaining, [])
        self.assertTrue(any(command[2:4] == ["disable", "--now"] for command in commands))

    def test_uninstall_refuses_an_unmanaged_unit_without_systemctl_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            target = unit_dir / unit.name
            target.write_text("[Unit]\nDescription=operator owned\n", encoding="utf-8")
            runner = Mock()

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                self.assertRaisesRegex(SystemdUserError, "unmanaged systemd unit"),
            ):
                uninstall_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)

            content = target.read_text(encoding="utf-8")

        self.assertEqual(content, "[Unit]\nDescription=operator owned\n")
        runner.assert_not_called()

    def test_uninstall_failure_restores_unit_files_and_prior_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            unit_dir = root / "units"
            unit_dir.mkdir()
            unit = dashboard_unit(paths, {"host": "127.0.0.1", "port": 3036})
            target = unit_dir / unit.name
            target.write_text(unit.content, encoding="utf-8")
            states = {unit.name: {"enabled": True, "active": True}}
            commands = []
            daemon_reloads = 0

            def runner(command, **kwargs):
                nonlocal daemon_reloads
                commands.append(command)
                verb = command[2]
                if verb == "is-enabled":
                    return subprocess.CompletedProcess(command, 0 if states[command[3]]["enabled"] else 4, "", "")
                if verb == "is-active":
                    return subprocess.CompletedProcess(command, 0 if states[command[3]]["active"] else 4, "", "")
                if verb == "disable":
                    states[unit.name] = {"enabled": False, "active": False}
                if verb == "daemon-reload":
                    daemon_reloads += 1
                    if daemon_reloads == 1:
                        return subprocess.CompletedProcess(command, 9, "", "failed")
                if verb == "enable":
                    states[unit.name] = {"enabled": True, "active": "--now" in command}
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch("data_foundation.systemd_user.platform.system", return_value="Linux"),
                patch("data_foundation.systemd_user._systemctl_binary", return_value="/usr/bin/systemctl"),
                self.assertRaises(SystemdUserError),
            ):
                uninstall_user_units(paths, [unit], unit_dir=unit_dir, runner=runner)

            restored = target.read_text(encoding="utf-8")

        self.assertEqual(restored, unit.content)
        self.assertEqual(states[unit.name], {"enabled": True, "active": True})
        self.assertTrue(any(command[2:] == ["enable", "--now", unit.name] for command in commands))

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
        self.assertTrue(preview["registrationImplemented"])
        self.assertTrue(preview["actualRegistered"])
        self.assertFalse(preview["registrationMismatch"])
        self.assertTrue(all(not job["runtimeStatus"]["definitionsAligned"] for job in preview["jobs"]))
        self.assertTrue(
            all("systemd-unit-missing" in job["runtimeStatus"]["issueCodes"] for job in preview["jobs"])
        )
        self.assertEqual(len(commands), 4)
        self.assertTrue(all(command[1] == "--user" for command in commands))

    def test_linux_scheduler_doctor_uses_systemd_registration_and_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._runtime(root)
            config_home = root / "config"
            unit_dir = config_home / "systemd" / "user"
            unit_dir.mkdir(parents=True)
            schedule = {
                "enabled": True,
                "mode": "system",
                "timezone": "UTC",
                "dailyPipelineTime": "04:00",
                "dashboardAggregationTime": "04:30",
                "systemTimer": {
                    "provider": "systemd",
                    "label": "actanara.test",
                    "registered": True,
                },
            }
            dashboard = {
                "server": {"enabled": True},
                "systemdUser": {
                    "registered": True,
                    "registrationManagedBy": "linux-installer",
                    "units": ["actanara-dashboard.service"],
                },
            }
            timer = schedule["systemTimer"]
            units = scheduler_units(paths, schedule, timer)
            units.append(dashboard_unit(paths, dashboard))
            for unit in units:
                (unit_dir / unit.name).write_text(unit.content, encoding="utf-8")
            write_settings(
                {
                    "features": {"dashboard": True},
                    "dashboard": dashboard,
                    "schedule": schedule,
                },
                paths,
            )
            probe = {
                "status": "registered",
                "actualRegistered": True,
                "units": [
                    {"name": unit.name, "enabled": True, "active": True}
                    for unit in units
                    if unit.enable_now and unit.name.endswith(".timer")
                ],
            }
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}),
                patch("data_foundation.settings_status.platform.system", return_value="Linux"),
                patch("data_foundation.scheduler_preview.platform.system", return_value="Linux"),
                patch("data_foundation.scheduler_preview.probe_user_units", return_value=probe),
            ):
                payload = actanara_settings_status(paths, doctor_profile="scheduler")

        checks = {item["id"]: item for item in payload["checks"]}
        dashboard_status = {
            item["id"]: item for item in payload["serviceRegistration"]["services"]
        }["dashboard"]
        self.assertEqual(payload["serviceRegistration"]["provider"], "systemd-user")
        self.assertEqual(dashboard_status["status"], "registered")
        self.assertTrue(dashboard_status["unitFilesPresent"])
        self.assertEqual(checks["systemd-registration:dashboard"]["status"], "ok")
        self.assertEqual(checks["scheduler-provider"]["status"], "ok")
        self.assertEqual(checks["scheduler-job:daily-pipeline"]["status"], "ok")
        self.assertEqual(checks["scheduler-job:dashboard-aggregation"]["status"], "ok")
        self.assertEqual(payload["summary"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
