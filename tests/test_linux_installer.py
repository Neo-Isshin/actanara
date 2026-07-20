import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from install import install_linux


class LinuxInstallerTests(unittest.TestCase):
    def _args(self, *arguments: str) -> argparse.Namespace:
        return install_linux._parser().parse_args(
            ["--source-root", str(ROOT), "--python", sys.executable, *arguments]
        )

    def test_default_plan_uses_shared_dashboard_profile_and_systemd_boundary(self):
        args = self._args()
        plan = install_linux.build_plan(args)

        self.assertEqual(plan.profiles, ("dashboard",))
        self.assertTrue(plan.dashboard_service)
        self.assertTrue(plan.scheduler)
        self.assertFalse(plan.rag_enabled)
        self.assertEqual(plan.rag_embedding_mode, "cloud")

    def test_rag_and_dev_test_profiles_share_linux_installer_code(self):
        args = self._args("--enable-rag", "--enable-dev-test")
        plan = install_linux.build_plan(args)

        self.assertEqual(plan.profiles, ("dashboard", "dev-test", "rag-server"))

    def test_local_rag_is_explicitly_gated_in_linux_phase_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self._args(
                "--runtime",
                str(Path(tmp) / "runtime"),
                "--enable-rag",
                "--rag-embedding-mode",
                "local",
            )
            plan = install_linux.build_plan(args)
            with (
                patch.dict(os.environ, {"ACTANARA_INSTALL_TEST_MODE": "1"}),
                self.assertRaisesRegex(install_linux.LinuxInstallError, "local embedding wheels remain gated"),
            ):
                install_linux._validate_plan(plan, args)

    def test_linux_service_preflight_requires_a_working_user_manager(self):
        args = self._args()
        plan = install_linux.build_plan(args)
        with (
            patch.object(install_linux.shutil, "which", return_value="/usr/bin/systemctl"),
            patch.object(
                install_linux.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, "", "no user bus"),
            ),
            self.assertRaisesRegex(install_linux.LinuxInstallError, "systemd user manager is unavailable"),
        ):
            install_linux._preflight_linux_services(plan)

    def test_linux_service_preflight_rejects_an_occupied_dashboard_port(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        args = self._args("--dashboard-port", str(port))
        plan = install_linux.build_plan(args)
        with (
            patch.object(install_linux.shutil, "which", return_value="/usr/bin/systemctl"),
            patch.object(
                install_linux.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            self.assertRaisesRegex(install_linux.LinuxInstallError, f"Dashboard port {port} is unavailable"),
        ):
            install_linux._preflight_linux_services(plan)

    def test_linux_service_preflight_can_be_skipped_for_cli_only_install(self):
        args = self._args("--no-scheduler", "--no-dashboard-server")
        plan = install_linux.build_plan(args)
        with patch.object(install_linux.subprocess, "run") as run:
            install_linux._preflight_linux_services(plan)

        run.assert_not_called()

    def test_dry_run_reports_exact_targets_without_writing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            args = self._args("--runtime", str(runtime), "--dry-run", "--no-scheduler")
            plan = install_linux.build_plan(args)
            selection = SimpleNamespace(
                environment_id="linux-cpython313-x86-64",
                lock_environment={"architecture": "x86_64"},
            )

            payload = install_linux._install(plan, selection, args)

        self.assertEqual(payload["status"], "planned")
        self.assertFalse(payload["writes"])
        self.assertEqual(payload["schedulerProvider"], "systemd")
        self.assertFalse(runtime.exists())

    def test_runtime_directories_ignore_permissive_login_umask(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "runtime"
            previous = os.umask(0o002)
            try:
                install_linux._secure_directory(target)
            finally:
                os.umask(previous)

            self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_staged_linux_source_has_release_manifest_and_no_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self._args("--runtime", str(root / "runtime"), "--dry-run")
            plan = install_linux.build_plan(args)
            release = root / "release"

            manifest = install_linux._stage_source(plan, release, "a" * 40)

            persisted = json.loads(
                (release / ".actanara-runtime-source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, manifest)
            self.assertEqual(manifest["product"], "actanara")
            self.assertEqual(manifest["deploymentMode"], "release-symlink")
            self.assertEqual(manifest["git"]["commit"], "a" * 40)
            self.assertGreater(manifest["payload"]["fileCount"], 180)
            self.assertFalse(any(path.is_symlink() for path in release.rglob("*")))

    def test_linux_bootstrap_is_posix_truncation_safe_and_never_calls_zsh(self):
        adapter = ROOT / "install" / "bootstrap-linux.sh"
        script = adapter.read_text(encoding="utf-8")

        self.assertTrue(script.startswith("#!/bin/sh\n"))
        self.assertIn("if true; then\nset -eu\numask 077", script)
        self.assertTrue(script.endswith("\nfi\n"))
        self.assertNotIn("/bin/zsh", script)
        syntax = subprocess.run(
            ["sh", "-n", str(adapter)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_systemd_install_selection_respects_linux_service_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            args = self._args("--runtime", str(runtime), "--no-dashboard-server")
            plan = install_linux.build_plan(args)
            settings = {
                "schedule": {
                    "timezone": "UTC",
                    "dailyPipelineTime": "04:00",
                    "dashboardAggregationTime": "04:30",
                    "systemTimer": {"provider": "systemd", "label": "actanara.daily"},
                },
                "dashboard": {"host": "127.0.0.1", "port": 3036},
            }
            paths = SimpleNamespace(home=runtime)
            installed = {}

            def fake_install(selected_paths, units):
                installed["paths"] = selected_paths
                installed["names"] = [unit.name for unit in units]
                return {"status": "installed", "linger": {"enabled": False, "changed": False}}

            with (
                patch("data_foundation.paths.runtime_paths_for_home", return_value=paths),
                patch("data_foundation.settings.read_settings", return_value=settings),
                patch("data_foundation.settings.write_settings") as write_settings,
                patch("data_foundation.systemd_user.install_user_units", side_effect=fake_install),
            ):
                result = install_linux._install_systemd_user_services(plan)

        self.assertEqual(result["status"], "installed")
        self.assertEqual(
            installed["names"],
            [
                "actanara.daily.pipeline.service",
                "actanara.daily.pipeline.timer",
                "actanara.daily.dashboard-aggregation.service",
                "actanara.daily.dashboard-aggregation.timer",
            ],
        )
        update = write_settings.call_args.args[0]
        self.assertTrue(update["schedule"]["systemTimer"]["registered"])
        self.assertEqual(update["schedule"]["systemTimer"]["provider"], "systemd")
        self.assertNotIn("dashboard", update)

    def test_database_initialization_uses_deployed_runtime_and_private_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            database = runtime / "data" / "actanara_data.sqlite3"
            database.parent.mkdir(parents=True)
            database.write_bytes(b"sqlite")
            database.chmod(0o666)
            args = self._args("--runtime", str(runtime))
            plan = install_linux.build_plan(args)
            commands = []

            def fake_run(command, *, env=None):
                commands.append((list(command), dict(env or {})))

            with patch.object(install_linux, "_run", side_effect=fake_run):
                initialized = install_linux._initialize_database(plan)
            database_mode = database.stat().st_mode & 0o777

        self.assertEqual(initialized, database)
        self.assertEqual(commands[0][0][0], str(runtime / ".venv" / "bin" / "python"))
        self.assertEqual(commands[0][0][1], "-c")
        self.assertIn("data_foundation.db import migrate", commands[0][0][2])
        self.assertEqual(commands[0][1]["ACTANARA_HOME"], str(runtime))
        self.assertIn(str(runtime / "app" / "source" / "src"), commands[0][1]["PYTHONPATH"])
        self.assertEqual(database_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
