import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.source_identity import loaded_source_commit
from data_foundation import source_identity


class LoadedSourceIdentityTests(unittest.TestCase):
    def _release_fixture(self, root: Path, commit: str) -> tuple[Path, Path, Path]:
        release = root / "runtime" / "app" / "releases" / "release-a"
        module = release / "src" / "dashboard" / "app" / "main.py"
        module.parent.mkdir(parents=True)
        module.write_text("# fixture\n", encoding="utf-8")
        (release / "pyproject.toml").write_text(
            '[project]\nname = "open-nova"\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        manifest = release / ".open-nova-runtime-source.json"
        manifest.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "product": "open-nova",
                    "git": {"available": True, "commit": commit},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        stable = root / "runtime" / "app" / "source"
        stable.symlink_to(Path("releases") / release.name)
        return stable / module.relative_to(release), module, manifest

    def test_reads_full_commit_from_concrete_release_behind_stable_source_symlink(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            stable_module, concrete_module, _manifest = self._release_fixture(Path(tmp), commit)

            stable_result = loaded_source_commit(stable_module)
            concrete_result = loaded_source_commit(concrete_module)

        self.assertEqual(stable_result, commit)
        self.assertEqual(concrete_result, commit)
        self.assertNotIn(tmp, stable_result)

    def test_rejects_missing_short_uppercase_or_unavailable_commit_without_path_output(self):
        invalid_git_values = (
            {"available": True, "commit": "a" * 39},
            {"available": True, "commit": "A" * 40},
            {"available": False, "commit": "a" * 40},
            {"available": True, "commit": None},
        )
        for index, git in enumerate(invalid_git_values):
            with self.subTest(git=git), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stable_module, _concrete_module, manifest = self._release_fixture(
                    root,
                    "b" * 40,
                )
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                payload["git"] = git
                manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

                result = loaded_source_commit(stable_module)

            self.assertIsNone(result, index)

    def test_rejects_symlinked_or_malformed_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stable_module, _concrete_module, manifest = self._release_fixture(root, "c" * 64)
            external = root / "external.json"
            external.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
            manifest.unlink()
            manifest.symlink_to(external)
            self.assertIsNone(loaded_source_commit(stable_module))

            manifest.unlink()
            manifest.write_text("{not-json\n", encoding="utf-8")
            self.assertIsNone(loaded_source_commit(stable_module))

    def test_rejects_non_integer_schema_and_oversize_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stable_module, _concrete_module, manifest = self._release_fixture(root, "f" * 40)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["schemaVersion"] = 2.0
            manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            self.assertIsNone(loaded_source_commit(stable_module))

            manifest.write_bytes(
                b" " * (source_identity._MAX_RUNTIME_SOURCE_MANIFEST_BYTES + 1)
            )
            self.assertIsNone(loaded_source_commit(stable_module))

    def test_nearest_foreign_or_manifestless_project_blocks_unrelated_ancestor_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp) / "outer"
            (outer / "pyproject.toml").parent.mkdir(parents=True)
            (outer / "pyproject.toml").write_text(
                '[project]\nname = "open-nova"\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            (outer / ".open-nova-runtime-source.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "product": "open-nova",
                        "git": {"available": True, "commit": "a" * 40},
                    }
                ),
                encoding="utf-8",
            )

            foreign = outer / "vendor" / "foreign"
            foreign_module = foreign / "src" / "package" / "module.py"
            foreign_module.parent.mkdir(parents=True)
            foreign_module.write_text("# foreign\n", encoding="utf-8")
            (foreign / "pyproject.toml").write_text(
                '[project]\nname = "different-project"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
            self.assertIsNone(loaded_source_commit(foreign_module))

            nested = outer / "vendor" / "nested-open-nova"
            nested_module = nested / "src" / "package" / "module.py"
            nested_module.parent.mkdir(parents=True)
            nested_module.write_text("# nested\n", encoding="utf-8")
            (nested / "pyproject.toml").write_text(
                '[project]\nname = "open-nova"\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            self.assertIsNone(loaded_source_commit(nested_module))

    def test_accepts_manifest_at_non_runtime_staging_project_root(self):
        commit = "9" * 64
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "public-staging"
            module = candidate / "src" / "agentic_rag" / "embedding_server.py"
            module.parent.mkdir(parents=True)
            module.write_text("# staging fixture\n", encoding="utf-8")
            (candidate / "pyproject.toml").write_text(
                '[project]\nname = "open-nova"\nversion = "9.9.9"\n',
                encoding="utf-8",
            )
            (candidate / ".open-nova-runtime-source.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "product": "open-nova",
                        "git": {"available": True, "commit": commit},
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(loaded_source_commit(module), commit)

    def test_call_site_can_freeze_identity_at_import_or_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            stable_module, _concrete_module, manifest = self._release_fixture(
                Path(tmp),
                "d" * 40,
            )
            frozen_at_import = loaded_source_commit(stable_module)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["git"]["commit"] = "e" * 40
            manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            reread = loaded_source_commit(stable_module)

        self.assertEqual(frozen_at_import, "d" * 40)
        self.assertEqual(reread, "e" * 40)


if __name__ == "__main__":
    unittest.main()
