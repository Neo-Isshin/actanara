import hashlib
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GPL_V3_OFFICIAL_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


class LicenseMetadataTests(unittest.TestCase):
    def test_license_is_unmodified_official_gpl_v3_text(self):
        payload = (ROOT / "LICENSE").read_bytes()

        self.assertEqual(hashlib.sha256(payload).hexdigest(), GPL_V3_OFFICIAL_SHA256)

    def test_pep639_metadata_declares_gpl_v3_or_later(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["version"], "1.0.2")
        self.assertEqual(metadata["project"]["readme"], "README.md")
        self.assertEqual(
            metadata["project"]["authors"],
            [{"name": "Neo-Isshin", "email": "nxc8335@gmail.com"}],
        )
        self.assertEqual(metadata["project"]["license"], "GPL-3.0-or-later")
        self.assertEqual(metadata["project"]["license-files"], ["LICENSE"])
        self.assertEqual(
            metadata["project"]["urls"],
            {
                "Homepage": "https://github.com/Neo-Isshin/open-nova",
                "Repository": "https://github.com/Neo-Isshin/open-nova",
                "Issues": "https://github.com/Neo-Isshin/open-nova/issues",
            },
        )
        self.assertEqual(
            metadata["build-system"]["requires"],
            ["setuptools==83.0.0", "wheel==0.47.0"],
        )

    def test_source_manifest_and_readmes_publish_consistent_notice(self):
        self.assertIn(
            "include LICENSE",
            (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines(),
        )
        for name in ("README.md", "README.zh-CN.md"):
            with self.subTest(name=name):
                content = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("Copyright © 2026 Neo-Isshin.", content)
                self.assertIn("GPL-3.0-or-later", content)
                self.assertIn("](LICENSE)", content)

    def test_public_entrypoints_use_the_version_independent_stable_channel(self):
        stable_install_command = (
            "curl -fsSL https://github.com/Neo-Isshin/open-nova/"
            "releases/latest/download/install.sh | zsh"
        )
        for name in (
            "README.md",
            "README.zh-CN.md",
            "docs/local-operations-runbook.md",
            "docs/local-operations-runbook.zh-CN.md",
            "docs/new-user-onboarding-runbook.md",
            "docs/index.html",
        ):
            with self.subTest(name=name):
                content = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn(stable_install_command, content)
                self.assertNotIn("raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.1", content)
                self.assertNotIn("git" + "ea", content.lower())


if __name__ == "__main__":
    unittest.main()
