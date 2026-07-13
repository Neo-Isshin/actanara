import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_foundation.paths import import_legacy_assets, initialize_home, load_paths, select_home


class RuntimePathsTests(unittest.TestCase):
    def test_environment_home_wins_over_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "bootstrap-home"
            bootstrap = root / "location.json"
            bootstrap.write_text(json.dumps({"novaHome": str(selected)}), encoding="utf-8")
            env_home = root / "environment-home"
            with patch.dict(
                os.environ,
                {"NOVA_HOME": str(env_home), "NOVA_LOCATION_FILE": str(bootstrap)},
                clear=False,
            ):
                self.assertEqual(load_paths().home, env_home)

    def test_initialized_home_excludes_reserved_rag_and_can_be_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap = root / "location.json"
            home = root / "NovaDiary"
            with patch.dict(os.environ, {"NOVA_LOCATION_FILE": str(bootstrap)}, clear=False):
                paths = initialize_home(home, legacy_diary_root=root / "Diary")
                self.assertTrue(paths.config_dir.joinpath("runtime.json").exists())
                self.assertTrue(paths.config_dir.joinpath("projects-registry.json").exists())
                self.assertTrue(paths.config_dir.joinpath("sources-registry.json").exists())
                self.assertTrue(paths.db_path.parent.exists())
                self.assertFalse((home / "reserved" / "rag").exists())
                selected = select_home(home)
                self.assertEqual(selected.home, home)
                self.assertEqual(json.loads(bootstrap.read_text())["novaHome"], str(home))

    def test_initialized_home_does_not_create_legacy_diary_root_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "NovaDiary"

            paths = initialize_home(home)
            manifest = json.loads((paths.config_dir / "runtime.json").read_text(encoding="utf-8"))

            self.assertEqual(paths.diary_dir, home / "artifacts" / "diary")
            self.assertIsNone(paths.legacy_diary_root)
            self.assertNotIn("legacyDiaryRoot", manifest)

    def test_legacy_import_copies_non_rag_assets_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "Diary"
            (legacy / "__diary_daily" / "2026-05-19" / "_filtered" / "codex").mkdir(parents=True)
            (legacy / "__diary_daily" / "2026-05-19" / "_filtered" / "codex" / "one.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            (legacy / "__diary_daily" / "2026-05-19" / "codex").mkdir(parents=True)
            (legacy / "__diary_daily" / "2026-05-19" / "codex" / "raw.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            (legacy / "__diary_rag").mkdir()
            (legacy / "__diary_rag" / "index.jsonl").write_text("never copy\n", encoding="utf-8")
            (legacy / "nova-task" / "tasks-intelligence").mkdir(parents=True)
            (legacy / "nova-task" / "tasks-intelligence" / "hints-2026-05-19.json").write_text(
                '{"hints": {"codex": ["legacy"]}}\n', encoding="utf-8"
            )
            (legacy / "summary-weekly-2026-05-19.md").write_text("weekly\n", encoding="utf-8")
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=legacy)
            first = import_legacy_assets(paths)
            second = import_legacy_assets(paths)
            self.assertEqual(first.copied, 3)
            self.assertEqual(second.matched, 3)
            self.assertTrue((paths.archives_dir / "2026-05-19" / "filtered" / "codex" / "one.jsonl").exists())
            self.assertTrue((paths.archives_dir / "2026-05-19" / "raw" / "codex" / "raw.jsonl").exists())
            self.assertTrue((paths.reports_dir / "weekly" / "summary-weekly-2026-05-19.md").exists())
            self.assertFalse((paths.task_intelligence_dir / "hints-2026-05-19.json").exists())
            self.assertFalse((paths.home / "reserved" / "rag").exists())


if __name__ == "__main__":
    unittest.main()
