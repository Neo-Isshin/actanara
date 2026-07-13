import plistlib
import os
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

from data_foundation.paths import initialize_home
from data_foundation.settings import read_settings, write_settings
from app.services import launcher
from advanced.dashboard import dashboard_launch_agent as agent
from advanced.dashboard import rag_server_launch_agent as rag_agent


class DashboardLaunchAgentTests(unittest.TestCase):
    def test_service_plist_uses_keepalive_and_foundation_env(self):
        plist = agent.build_service_plist(
            label="com.example.dashboard",
            python=Path("/tmp/venv/bin/python"),
            project_root=Path("/repo"),
            nova_home=Path("/nova"),
            host="127.0.0.1",
            port=3036,
            foundation=True,
            logs_dir=Path("/tmp/logs"),
        )

        args = plist["ProgramArguments"]
        env = plist["EnvironmentVariables"]
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(args[:2], ["/bin/zsh", "-lc"])
        self.assertIn("/tmp/venv/bin/python -m uvicorn app.main:app", args[2])
        self.assertIn("/repo/src/dashboard", args[2])
        self.assertIn("--host 127.0.0.1", args[2])
        self.assertIn("--port 3036", args[2])
        self.assertEqual(env["NOVA_DASHBOARD_PYTHON"], "/tmp/venv/bin/python")
        self.assertEqual(env["NOVA_DASHBOARD_PORT"], "3036")
        self.assertEqual(env["NOVA_HOME"], "/nova")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertNotIn("WORKSPACE_DIR", env)
        self.assertNotIn("DIARY_OUTPUT_DIR", env)
        self.assertNotIn("TMP_WORKSPACE", env)
        self.assertNotIn("NOVA_DATA_DB_PATH", env)
        self.assertNotIn("NOVA_DATA_EXPORT_DIR", env)
        self.assertEqual(env["NOVA_DATA_FOUNDATION_ENABLED"], "true")
        self.assertEqual(env["DASHBOARD_READ_SOURCE"], "foundation")
        self.assertEqual(plist["StandardOutPath"], "/tmp/logs/dashboard-server.out.log")

    def test_watchdog_plist_restarts_service_on_health_failure(self):
        plist = agent.build_watchdog_plist(
            label="com.example.dashboard.watchdog",
            service_label="com.example.dashboard",
            python=Path("/tmp/venv/bin/python"),
            script=Path("/repo/advanced/dashboard/dashboard_launch_agent.py"),
            url="http://127.0.0.1:3036/health",
            interval=60,
            nova_home=Path("/nova"),
            logs_dir=Path("/tmp/logs"),
        )

        args = plist["ProgramArguments"]
        self.assertEqual(plist["StartInterval"], 60)
        self.assertIn("check", args)
        self.assertIn("--restart", args)
        self.assertIn("com.example.dashboard", args)
        self.assertEqual(plist["EnvironmentVariables"]["NOVA_HOME"], "/nova")
        self.assertEqual(plist["EnvironmentVariables"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(plist["StandardErrorPath"], "/tmp/logs/dashboard-watchdog.err.log")

    def test_launch_defaults_read_dashboard_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            write_settings(
                {
                    "dashboard": {
                        "projectRoot": str(root / "project"),
                        "pythonExecutable": str(root / "venv" / "bin" / "python"),
                        "host": "0.0.0.0",
                        "port": 4545,
                        "healthPath": "/healthz",
                        "logsDir": str(root / "logs"),
                        "serviceLabel": "com.example.dashboard",
                        "watchdogLabel": "com.example.dashboard.watchdog",
                    }
                },
                paths,
            )
            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False):
                defaults = agent.dashboard_launch_defaults()

        self.assertEqual(defaults["project_root"], root / "project")
        self.assertEqual(defaults["python"], root / "venv" / "bin" / "python")
        self.assertEqual(defaults["host"], "0.0.0.0")
        self.assertEqual(defaults["port"], 4545)
        self.assertEqual(defaults["url"], "http://0.0.0.0:4545/healthz")
        self.assertEqual(defaults["logs_dir"], root / "logs")
        self.assertEqual(defaults["label"], "com.example.dashboard")
        self.assertEqual(defaults["watchdog_label"], "com.example.dashboard.watchdog")

    def test_write_plist_round_trips(self):
        payload = {"Label": "com.example.dashboard", "RunAtLoad": True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.plist"
            agent.write_plist(path, payload)
            self.assertEqual(plistlib.loads(path.read_bytes()), payload)

    def test_check_health_returns_false_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertFalse(agent.check_health("http://127.0.0.1:3036/health", timeout=0.1))

    def test_rag_service_plist_runs_lifecycle_wrapper(self):
        plist = rag_agent.build_service_plist(
            label="com.example.rag-server",
            python=Path("/tmp/venv/bin/python"),
            project_root=Path("/repo"),
            nova_home=Path("/nova"),
            script=Path("/repo/advanced/dashboard/rag_server_launch_agent.py"),
            logs_dir=Path("/tmp/logs"),
        )

        args = plist["ProgramArguments"]
        env = plist["EnvironmentVariables"]
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(args[:3], ["/tmp/venv/bin/python", "/repo/advanced/dashboard/rag_server_launch_agent.py", "run"])
        self.assertIn("--project-root", args)
        self.assertIn("--nova-home", args)
        self.assertEqual(env["NOVA_HOME"], "/nova")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertIn("/repo/src", env["PYTHONPATH"])
        self.assertEqual(plist["StandardOutPath"], "/tmp/logs/rag-server-launchagent.out.log")

    def test_rag_launcher_does_not_restart_a_run_at_load_manager_after_bootstrap(self):
        with patch.object(rag_agent, "rag_launch_defaults") as defaults:
            defaults.return_value = {
                "label": "com.example.rag-server",
                "python": Path("/tmp/venv/bin/python"),
                "project_root": Path("/repo"),
                "nova_home": Path("/nova"),
                "logs_dir": Path("/tmp/logs"),
            }
            jobs = launcher._jobs("rag")

        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0]["kickstart"])

    def test_managed_python_wrappers_disable_source_bytecode_before_project_imports(self):
        wrappers = (
            ROOT / "advanced" / "dashboard" / "dashboard_launch_agent.py",
            ROOT / "advanced" / "dashboard" / "rag_server_launch_agent.py",
            ROOT / "advanced" / "pipeline" / "run_daily_pipeline.py",
            ROOT / "advanced" / "pipeline" / "run_dashboard_foundation_refresh.py",
        )
        for path in wrappers:
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertIn("sys.dont_write_bytecode = True", source)

    def test_launcher_install_requires_confirmation_and_supports_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            plist_path = root / "LaunchAgents" / "com.open-nova.rag-server.plist"
            job = {
                "kind": "rag-server",
                "label": "com.open-nova.rag-server",
                "plistPath": str(plist_path),
                "plist": {"Label": "com.open-nova.rag-server", "ProgramArguments": ["python", "rag"]},
            }
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False),
                patch.object(launcher, "_jobs", return_value=[job]),
                patch.object(launcher, "_launchctl") as launchctl,
            ):
                dry_run = launcher.install_rag_launch_agent({"dryRun": True})
                with self.assertRaisesRegex(ValueError, "confirmationText"):
                    launcher.install_rag_launch_agent({"confirmationText": "wrong"})

            self.assertEqual(dry_run["confirmationTextRequired"], launcher.RAG_INSTALL_CONFIRMATION)
            self.assertFalse(plist_path.exists())
            launchctl.assert_not_called()

    def test_launcher_uses_stable_import_paths_for_managed_scripts(self):
        source = Path(launcher.__file__).read_text(encoding="utf-8")

        self.assertIn('script=defaults["project_root"] / "advanced" / "dashboard" / "dashboard_launch_agent.py"', source)
        self.assertIn('script=defaults["project_root"] / "advanced" / "dashboard" / "rag_server_launch_agent.py"', source)
        self.assertNotIn("script=Path(dashboard_launch_agent.__file__)", source)
        self.assertNotIn("script=Path(rag_server_launch_agent.__file__)", source)
        self.assertNotIn("script=Path(dashboard_launch_agent.__file__).resolve()", source)
        self.assertNotIn("script=Path(rag_server_launch_agent.__file__).resolve()", source)

    def test_launcher_preview_reports_actual_launchd_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            plist_path = root / "LaunchAgents" / "com.open-nova.rag-server.plist"
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("placeholder", encoding="utf-8")
            job = {
                "kind": "rag-server",
                "label": "com.open-nova.rag-server",
                "plistPath": str(plist_path),
                "plist": {"Label": "com.open-nova.rag-server", "ProgramArguments": ["python", "rag"]},
            }
            commands = []

            def loaded_runner(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "state = running", "")

            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False),
                patch.object(launcher, "_jobs", return_value=[job]),
                patch.object(launcher.platform, "system", return_value="Darwin"),
                patch.object(launcher.os, "getuid", return_value=501),
            ):
                preview = launcher.preview_rag_launch_agent(launchctl_runner=loaded_runner)

        self.assertTrue(preview["registered"])
        self.assertTrue(preview["actualRegistered"])
        self.assertFalse(preview["configuredRegistered"])
        self.assertTrue(preview["registrationMismatch"])
        self.assertEqual(preview["registrationSource"], "launchd-probe")
        self.assertEqual(preview["runtimeProbe"]["status"], "loaded")
        self.assertEqual(preview["runtimeProbe"]["loadedJobs"], 1)
        self.assertEqual(preview["jobs"][0]["runtimeStatus"]["status"], "loaded")
        self.assertEqual(commands, [["launchctl", "print", "gui/501/com.open-nova.rag-server"]])

    def test_launcher_install_backs_up_existing_plist_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            plist_path = root / "LaunchAgents" / "com.open-nova.rag-server.plist"
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("old", encoding="utf-8")
            job = {
                "kind": "rag-server",
                "label": "com.open-nova.rag-server",
                "plistPath": str(plist_path),
                "plist": {"Label": "com.open-nova.rag-server", "ProgramArguments": ["python", "rag"]},
            }
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False),
                patch.object(launcher, "_jobs", return_value=[job]),
                patch.object(launcher, "_launchctl") as launchctl,
            ):
                result = launcher.install_rag_launch_agent({"confirmationText": launcher.RAG_INSTALL_CONFIRMATION})
                settings = read_settings(paths)

            self.assertEqual(result["status"], "registered")
            self.assertTrue(plist_path.exists())
            self.assertEqual(plistlib.loads(plist_path.read_bytes())["Label"], "com.open-nova.rag-server")
            self.assertTrue(Path(result["backupDir"]).joinpath(plist_path.name).exists())
            self.assertTrue(settings["rag"]["server"]["launchAgent"]["registered"])
            self.assertEqual(settings["rag"]["server"]["launchAgent"]["registrationManagedBy"], "dashboard")
            self.assertGreaterEqual(launchctl.call_count, 2)

    def test_launcher_install_failure_writes_failed_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            plist_path = root / "LaunchAgents" / "com.open-nova.rag-server.plist"
            job = {
                "kind": "rag-server",
                "label": "com.open-nova.rag-server",
                "plistPath": str(plist_path),
                "plist": {"Label": "com.open-nova.rag-server", "ProgramArguments": ["python", "rag"]},
            }

            def fail_bootstrap(action, label, plist_path, allow_failure=False):
                if action == "bootstrap":
                    raise RuntimeError("bootstrap failed")
                return subprocess.CompletedProcess(["launchctl"], 0, "", "")

            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False),
                patch.object(launcher, "_jobs", return_value=[job]),
                patch.object(launcher, "_launchctl", side_effect=fail_bootstrap),
            ):
                with self.assertRaisesRegex(RuntimeError, "bootstrap failed"):
                    launcher.install_rag_launch_agent({"confirmationText": launcher.RAG_INSTALL_CONFIRMATION})
                settings = read_settings(paths)

            audit = settings["rag"]["server"]["launchAgent"]
            self.assertFalse(audit["registered"])
            self.assertEqual(audit["lastAction"], "install")
            self.assertEqual(audit["lastActionStatus"], "failed")
            self.assertIn("bootstrap failed", audit["lastError"])
            self.assertTrue(audit["operationResults"])
            self.assertIn("rollbackHint", audit)

    def test_launcher_uninstall_moves_plist_to_backup_and_marks_unregistered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            plist_path = root / "LaunchAgents" / "com.open-nova.dashboard.plist"
            plist_path.parent.mkdir(parents=True)
            plist_path.write_text("old", encoding="utf-8")
            job = {
                "kind": "dashboard-service",
                "label": "com.open-nova.dashboard",
                "plistPath": str(plist_path),
                "plist": {"Label": "com.open-nova.dashboard", "ProgramArguments": ["python", "dashboard"]},
            }
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}, clear=False),
                patch.object(launcher, "_jobs", return_value=[job]),
                patch.object(launcher, "_launchctl") as launchctl,
            ):
                result = launcher.uninstall_dashboard_launch_agent({"confirmationText": launcher.DASHBOARD_UNINSTALL_CONFIRMATION})
                settings = read_settings(paths)

            self.assertEqual(result["status"], "unregistered")
            self.assertFalse(plist_path.exists())
            self.assertTrue(Path(result["backupDir"]).joinpath(plist_path.name).exists())
            self.assertFalse(settings["dashboard"]["launchAgent"]["registered"])
            launchctl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
