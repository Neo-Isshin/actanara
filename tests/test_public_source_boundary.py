import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicSourceBoundaryTests(unittest.TestCase):
    def test_private_archives_and_one_time_harnesses_are_absent(self):
        self.assertFalse((ROOT / "docs" / "archive").exists())
        self.assertEqual(
            {path.name for path in (ROOT / "docs").glob("v1-*.md")},
            {"v1-release-assurance.md"},
        )
        for relative in (
            "docs/next-major-version-roadmap.md",
            "docs/onboarding-product-contract-v2.md",
            "docs/llm-provider-operations.md",
            "docs/pipeline-language-parity-matrix.md",
            "docs/foundation-daily-qa-repair-execution-policy.md",
            "advanced/pipeline/run_phase6_enablement_readiness.py",
            "advanced/pipeline/run_phase6_pipeline_output_observation.py",
            "advanced/pipeline/run_phase6_pipeline_smoke.py",
            "advanced/pipeline/run_foundation_source_switching_gate.py",
            "src/data_foundation/source_switching.py",
            "tests/run_installer_live_update_matrix.py",
            "tests/run_keychain_live_matrix.py",
            "tests/test_keychain_live_harness.py",
            "tests/test_phase6_release_gates.py",
            "tests/test_actanara_system_dry_run_smoke.py",
            "tests/test_foundation_source_switching.py",
            "tests/fixtures/onboarding/product-v2-one-liner-dry-run-schema.json",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())
        self.assertTrue(
            (ROOT / "tests" / "fixtures" / "onboarding" / "runtime-dry-run-contract.json").is_file()
        )
        dashboard_assets = ROOT / "docs" / "assets" / "dashboard"
        self.assertEqual(
            {path.name for path in dashboard_assets.iterdir() if path.is_file()},
            {
                "dashboard-ai-assets-long.png",
                "dashboard-ai-assets-overview.png",
                "dashboard-home.png",
                "dashboard-nova-rag.png",
                "dashboard-nova-task.png",
                "dashboard-weekly-full.png",
                "dashboard-weekly-overview.png",
            },
        )
        for path in dashboard_assets.iterdir():
            if path.is_file():
                with self.subTest(curated_dashboard_asset=path.name):
                    self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_supported_advanced_runtime_wrappers_are_exact(self):
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "advanced").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
        self.assertEqual(
            actual,
            {
                "advanced/cli/actanara_diary.py",
                "advanced/cli/actanara.py",
                "advanced/dashboard/dashboard_launch_agent.py",
                "advanced/dashboard/rag_server_launch_agent.py",
                "advanced/dashboard/run_dashboard_server.sh",
                "advanced/pipeline/run_daily_pipeline.py",
                "advanced/pipeline/run_dashboard_foundation_refresh.py",
                "advanced/pipeline/run_actanara_settings_status.py",
                "advanced/pipeline/run_nova_task_work_graph_reconciliation.py",
            },
        )

    def test_tests_are_public_source_but_pruned_from_install_payload(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        installer = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
        runtime_dependency_files = (
            "install/dependency_contract.py",
            "install/runtime-dependencies.lock.json",
        )
        lock_generator = "tools/release/generate_runtime_lock.py"

        self.assertTrue((ROOT / "tests" / "run_isolated_release_suite.py").is_file())
        self.assertTrue((ROOT / "tools" / "release" / "build_release.py").is_file())
        for relative in runtime_dependency_files:
            with self.subTest(runtime_dependency_file=relative):
                self.assertTrue((ROOT / relative).is_file())
                self.assertIn(f'"{relative}"', installer)
        self.assertTrue((ROOT / lock_generator).is_file())
        self.assertNotIn(f'"{lock_generator}"', installer)
        self.assertIn("prune tests", manifest.splitlines())
        self.assertIn("prune tools", manifest.splitlines())
        self.assertNotIn("graft tests", manifest.splitlines())
        self.assertNotIn("graft tools", manifest.splitlines())
        self.assertNotIn('"tests",', installer[installer.index("allowed_top_level"):])


if __name__ == "__main__":
    unittest.main()
