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
        self.assertEqual(plan.linger_policy, "prompt")

    def test_rag_and_dev_test_profiles_share_linux_installer_code(self):
        args = self._args("--enable-rag", "--enable-dev-test")
        plan = install_linux.build_plan(args)

        self.assertEqual(plan.profiles, ("dashboard", "dev-test", "rag-server"))

    def test_local_rag_selects_both_server_and_local_dependency_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self._args(
                "--runtime",
                str(Path(tmp) / "runtime"),
                "--enable-rag",
                "--rag-embedding-mode",
                "local",
            )
            plan = install_linux.build_plan(args)
            self.assertEqual(plan.profiles, ("dashboard", "rag-local", "rag-server"))

            update = install_linux._runtime_settings_update(plan)

            self.assertEqual(
                update["rag"]["embedding"],
                {
                    "mode": "local",
                    "provider": "local",
                    "providerId": "local",
                    "model": "intfloat/multilingual-e5-small",
                    "dimension": 384,
                    "device": "auto",
                },
            )

    def test_upgrade_inherits_runtime_profiles_services_and_preserves_linger(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            (runtime / "config").mkdir(parents=True)
            settings = {
                "schemaVersion": 1,
                "features": {"rag": True},
                "rag": {
                    "enabled": True,
                    "embedding": {"mode": "local", "provider": "local"},
                    "server": {"enabled": False},
                },
                "dashboard": {
                    "host": "127.0.0.1",
                    "port": 43123,
                    "server": {"enabled": True},
                },
                "schedule": {
                    "enabled": True,
                    "systemTimer": {"provider": "systemd", "registered": True},
                },
            }
            settings_path = runtime / "config" / "settings.json"
            settings_path.write_text(json.dumps(settings) + "\n", encoding="utf-8")
            settings_path.chmod(0o600)
            args = self._args("--runtime", str(runtime), "--upgrade")
            inherited = {
                "profiles": ["dashboard", "dev-test", "rag-local", "rag-server"],
                "rag": {"enabled": True, "embeddingMode": "local"},
                "evidence": {
                    "settingsSha256": "a" * 64,
                    "activeVenvTarget": str(runtime / "app" / "venvs" / "old"),
                    "activeMarkerStatus": "trusted",
                    "activeMarkerSha256": "b" * 64,
                },
            }
            with patch.object(
                install_linux.dependency_contract,
                "runtime_dependency_profiles",
                return_value=inherited,
            ):
                plan = install_linux.build_plan(args)

            self.assertEqual(plan.update_mode, "upgrade")
            self.assertEqual(plan.profiles, tuple(inherited["profiles"]))
            self.assertEqual(plan.dashboard_port, 43123)
            self.assertTrue(plan.dashboard_service)
            self.assertTrue(plan.scheduler)
            self.assertFalse(plan.rag_enabled)
            self.assertEqual(plan.rag_embedding_mode, "local")
            self.assertEqual(plan.linger_policy, "preserve")

    def test_upgrade_rejects_runtime_setting_changes_disguised_as_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            (runtime / "config").mkdir(parents=True)
            settings_path = runtime / "config" / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "features": {"rag": False},
                        "rag": {"enabled": False},
                        "dashboard": {
                            "host": "127.0.0.1",
                            "port": 3036,
                            "server": {"enabled": True},
                        },
                        "schedule": {"enabled": False},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            settings_path.chmod(0o600)
            args = self._args(
                "--runtime",
                str(runtime),
                "--upgrade",
                "--dashboard-port",
                "4040",
            )
            inherited = {
                "profiles": ["dashboard"],
                "rag": {"enabled": False, "embeddingMode": None},
                "evidence": {
                    "settingsSha256": "a" * 64,
                    "activeVenvTarget": str(runtime / ".venv"),
                    "activeMarkerStatus": "missing",
                    "activeMarkerSha256": None,
                },
            }
            with (
                patch.object(
                    install_linux.dependency_contract,
                    "runtime_dependency_profiles",
                    return_value=inherited,
                ),
                self.assertRaisesRegex(install_linux.LinuxInstallError, "--dashboard-port"),
            ):
                install_linux.build_plan(args)

    def test_repair_requires_explicit_yes_and_conflicts_with_upgrade(self):
        with self.assertRaisesRegex(install_linux.LinuxInstallError, "requires --yes"):
            install_linux.build_plan(self._args("--repair-existing"))
        with self.assertRaisesRegex(install_linux.LinuxInstallError, "cannot be combined"):
            install_linux.build_plan(self._args("--repair-existing", "--upgrade", "--yes"))

    def test_force_rebuild_requires_upgrade_and_conflicts_with_source_only(self):
        with self.assertRaisesRegex(install_linux.LinuxInstallError, "requires --upgrade"):
            install_linux._requested_update_mode(self._args("--force-rebuild"))
        with self.assertRaisesRegex(install_linux.LinuxInstallError, "mutually exclusive"):
            install_linux._requested_update_mode(
                self._args("--source-only", "--force-rebuild")
            )
        self.assertEqual(
            install_linux._requested_update_mode(
                self._args("--upgrade", "--force-rebuild")
            ),
            "upgrade",
        )

    def test_force_rebuild_selects_locked_candidate_dependency_plan(self):
        plan = SimpleNamespace(
            update_mode="upgrade",
            force_rebuild=True,
            runtime=Path("/tmp/actanara-update-fixture"),
            offline=False,
        )
        ready = {
            "status": "ready",
            "updateMode": "rebuild-candidate-venv",
            "reason": "explicit-force-rebuild",
        }
        with patch.object(
            install_linux.dependency_contract,
            "plan_update",
            return_value=(ready, 0),
        ) as planner:
            result = install_linux._dependency_update_plan(plan, object())

        self.assertEqual(result, ready)
        self.assertEqual(planner.call_args.kwargs["mode"], "force-rebuild")

    def test_cloud_rag_configuration_does_not_claim_local_model_runtime(self):
        args = self._args("--enable-rag", "--rag-embedding-mode", "cloud")
        plan = install_linux.build_plan(args)

        update = install_linux._runtime_settings_update(plan)

        self.assertEqual(
            update["rag"]["embedding"],
            {"mode": "cloud", "provider": "cloud"},
        )

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

    def test_cli_only_install_does_not_probe_or_change_linger(self):
        args = self._args("--no-scheduler", "--no-dashboard-server")
        plan = install_linux.build_plan(args)
        with patch("data_foundation.systemd_user.linger_status") as linger_status:
            result = install_linux._prepare_linger(plan)

        self.assertEqual(result["action"], "not-required")
        self.assertFalse(result["sudoInvoked"])
        linger_status.assert_not_called()

    def test_default_linger_prompt_preserves_state_when_declined(self):
        args = self._args()
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            patch.object(install_linux, "_prompt_enable_linger", return_value=False),
            patch("data_foundation.systemd_user.enable_linger") as enable_linger,
        ):
            result = install_linux._prepare_linger(plan)

        self.assertEqual(result["action"], "declined")
        self.assertEqual(result["requestedPolicy"], "prompt")
        self.assertFalse(result["sudoInvoked"])
        enable_linger.assert_not_called()

    def test_default_linger_prompt_enables_only_after_explicit_acceptance(self):
        args = self._args()
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            patch.object(install_linux, "_prompt_enable_linger", return_value=True),
            patch(
                "data_foundation.systemd_user.enable_linger",
                return_value={
                    "status": "enabled",
                    "enabled": True,
                    "changed": True,
                    "action": "enabled",
                    "authorization": "explicit-user-choice",
                },
            ) as enable_linger,
        ):
            result = install_linux._prepare_linger(plan)

        self.assertTrue(result["enabled"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["requestedPolicy"], "prompt")
        self.assertFalse(result["sudoInvoked"])
        enable_linger.assert_called_once_with()

    def test_noninteractive_default_preserves_linger(self):
        args = self._args()
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            patch.object(install_linux, "_prompt_enable_linger", return_value=None),
            patch("data_foundation.systemd_user.enable_linger") as enable_linger,
        ):
            result = install_linux._prepare_linger(plan)

        self.assertEqual(result["action"], "non-interactive-preserved")
        enable_linger.assert_not_called()

    def test_explicit_enable_linger_does_not_depend_on_yes_or_prompt(self):
        args = self._args("--enable-linger", "--yes")
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            patch.object(install_linux, "_prompt_enable_linger") as prompt,
            patch(
                "data_foundation.systemd_user.enable_linger",
                return_value={"status": "enabled", "enabled": True, "changed": True},
            ) as enable_linger,
        ):
            result = install_linux._prepare_linger(plan)

        self.assertEqual(plan.linger_policy, "enable")
        self.assertTrue(result["enabled"])
        prompt.assert_not_called()
        enable_linger.assert_called_once_with()

    def test_require_linger_fails_before_install_when_not_enabled(self):
        args = self._args("--require-linger")
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            self.assertRaisesRegex(install_linux.LinuxInstallError, "linger is required"),
        ):
            install_linux._prepare_linger(plan)

    def test_dry_run_never_prompts_or_changes_linger(self):
        args = self._args("--dry-run", "--enable-linger")
        plan = install_linux.build_plan(args)
        with (
            patch(
                "data_foundation.systemd_user.linger_status",
                return_value={"status": "disabled", "enabled": False, "changed": False},
            ),
            patch.object(install_linux, "_prompt_enable_linger") as prompt,
            patch("data_foundation.systemd_user.enable_linger") as enable_linger,
        ):
            result = install_linux._prepare_linger(plan)

        self.assertEqual(result["action"], "planned-enable")
        self.assertTrue(result["wouldChange"])
        prompt.assert_not_called()
        enable_linger.assert_not_called()

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

    def test_standard_update_rejects_stale_or_drifted_systemd_definitions(self):
        plan = SimpleNamespace(runtime=Path("/tmp/actanara-update-fixture"))
        desired = [SimpleNamespace(name="actanara-dashboard.service", content="managed\n")]

        with self.assertRaisesRegex(install_linux.LinuxInstallError, "inventory is stale"):
            install_linux._validate_existing_systemd_units_for_update(
                plan,
                desired,
                ("actanara-dashboard.service", "actanara-stale.service"),
            )

        with (
            patch(
                "data_foundation.systemd_user.inspect_user_units",
                return_value={
                    "definitionsPresent": True,
                    "definitionsManaged": True,
                    "definitionsAligned": False,
                },
            ),
            self.assertRaisesRegex(install_linux.LinuxInstallError, "have drifted"),
        ):
            install_linux._validate_existing_systemd_units_for_update(
                plan,
                desired,
                ("actanara-dashboard.service",),
            )

    def test_update_alignment_accepts_a_deliberately_stopped_managed_service(self):
        plan = SimpleNamespace(runtime=Path("/tmp/actanara-update-fixture"))
        inspection = {
            "definitionsPresent": True,
            "definitionsManaged": True,
            "definitionsAligned": True,
            "actualEnabled": True,
            "actualActive": False,
            "actualRegistered": False,
        }
        with (
            patch.object(
                install_linux,
                "_desired_systemd_units",
                return_value=[SimpleNamespace(name="actanara-dashboard.service")],
            ),
            patch(
                "data_foundation.systemd_user.inspect_user_units",
                return_value=inspection,
            ),
        ):
            result = install_linux._verify_updated_systemd_units(plan, {})

        self.assertEqual(result, inspection)

    def test_update_health_checks_only_services_active_before_the_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = SimpleNamespace(
                runtime=Path(tmp) / "runtime",
                dashboard_service=True,
                rag_enabled=False,
            )
            settings = {
                "dashboard": {
                    "host": "127.0.0.1",
                    "port": 65534,
                    "systemdUser": {"units": ["actanara-test-dashboard.service"]},
                }
            }
            with patch.object(install_linux.http.client, "HTTPConnection") as connection:
                install_linux._wait_for_update_service_health(
                    plan,
                    settings,
                    active_units=set(),
                )

        connection.assert_not_called()

    def test_update_doctor_is_captured_as_bounded_machine_readable_evidence(self):
        plan = SimpleNamespace(runtime=Path("/tmp/actanara-update-fixture"))
        completed = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "doctorProfile": "installer",
                    "summary": {
                        "status": "warn",
                        "errors": 0,
                        "warnings": 1,
                        "checks": 6,
                    },
                }
            ),
            "",
        )
        with patch.object(install_linux.subprocess, "run", return_value=completed) as run:
            result = install_linux._run_update_doctor(plan)

        self.assertEqual(
            result,
            {
                "profile": "installer",
                "status": "warn",
                "errors": 0,
                "warnings": 1,
                "checks": 6,
            },
        )
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_linux_result_envelope_matches_platform_neutral_update_cli_contract(self):
        envelope = install_linux._result_envelope(
            payload={
                "status": "updated",
                "updateMode": "source-only",
                "dependenciesInstalled": False,
                "reusesRuntimeVenv": True,
                "reason": "dependency-fingerprint-match",
                "systemdUser": {"units": [{"name": "actanara-dashboard.service"}]},
            },
            requested_mode="source-only",
        )

        self.assertEqual(envelope["status"], "completed")
        self.assertEqual(envelope["updateMode"], "source-only")
        self.assertTrue(envelope["sourceUpdated"])
        self.assertTrue(envelope["reusesRuntimeVenv"])
        self.assertTrue(envelope["servicesStopped"])
        self.assertTrue(envelope["stateCertain"])
        self.assertEqual(len(envelope), 14)


if __name__ == "__main__":
    unittest.main()
