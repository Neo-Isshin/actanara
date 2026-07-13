import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseTestHarnessTests(unittest.TestCase):
    def test_dev_test_extra_contains_full_dashboard_test_import_surface(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = pyproject["project"]["optional-dependencies"]["dev-test"]
        normalized = "\n".join(requirements).lower()

        for dependency in ("fastapi", "uvicorn", "pyyaml", "croniter", "numpy"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, normalized)

    def test_rag_benchmark_package_data_contains_all_language_profiles(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        packaged = set(pyproject["tool"]["setuptools"]["package-data"]["agentic_rag"])

        self.assertEqual(
            packaged,
            {
                "rag_eval_queries.jsonl",
                "rag_eval_queries.en.jsonl",
                "rag_eval_queries.zh.jsonl",
            },
        )
        for filename in packaged:
            with self.subTest(filename=filename):
                self.assertTrue((ROOT / "src" / "agentic_rag" / filename).is_file())

    def test_release_runner_isolates_runtime_clock_secret_store_and_launchctl(self):
        runner = (ROOT / "tests" / "run_isolated_release_suite.py").read_text(encoding="utf-8")

        self.assertIn('TemporaryDirectory(prefix="open-nova-release-venv-")', runner)
        self.assertIn('build_source = root / "source"', runner)
        for filename in (
            "pyproject.toml",
            "MANIFEST.in",
            "LICENSE",
            "README.md",
            "README.zh-CN.md",
        ):
            with self.subTest(filename=filename):
                self.assertIn(f'"{filename}"', runner)
        self.assertIn('shutil.copytree(', runner)
        self.assertIn('shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc", "*.pyo")', runner)
        self.assertIn('f"{build_source}[dev-test]"', runner)
        self.assertNotIn('f"{ROOT}[dev-test]"', runner)
        self.assertIn('"PIP_CONFIG_FILE": os.devnull', runner)
        self.assertIn('"PYTHONNOUSERSITE": "1"', runner)
        self.assertIn('for name in INHERITED_RUNTIME_ENV', runner)
        self.assertIn('TemporaryDirectory(prefix="open-nova-release-runtime-")', runner)
        self.assertIn('"NOVA_HOME": str(nova_home)', runner)
        self.assertIn('"NOVA_LOCATION_FILE": str(location_file)', runner)
        self.assertIn('"OPEN_NOVA_SECRET_BACKEND": "memory"', runner)
        self.assertIn('"OPEN_NOVA_RUN_REAL_LAUNCHD_TESTS": "0"', runner)
        self.assertIn('"NOVA_INSTALL_LAUNCHCTL": str(fake_bin / "launchctl")', runner)
        self.assertIn('"TARGET_TIMEZONE"', runner)
        self.assertIn('patch.object(nova_time, "business_now"', runner)
        self.assertIn('patch.object(dashboard_tz, "hkt_now"', runner)


if __name__ == "__main__":
    unittest.main()
