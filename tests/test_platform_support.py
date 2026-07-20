import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.platform_support import (
    default_timer_provider,
    normalize_architecture,
    platform_capabilities,
)
from data_foundation.paths import initialize_home
from data_foundation.settings import default_settings


class PlatformSupportTests(unittest.TestCase):
    def test_macos_capabilities_preserve_launchd_behavior(self):
        capabilities = platform_capabilities(system="Darwin", machine="arm64")

        self.assertEqual(capabilities.family, "macos")
        self.assertEqual(capabilities.architecture, "arm64")
        self.assertEqual(capabilities.timer_provider, "launchd")
        self.assertEqual(capabilities.user_service_manager, "launchd-user")
        self.assertTrue(capabilities.supported)

    def test_linux_x64_capabilities_select_systemd(self):
        capabilities = platform_capabilities(system="Linux", machine="amd64")

        self.assertEqual(capabilities.family, "linux")
        self.assertEqual(capabilities.architecture, "x86_64")
        self.assertEqual(capabilities.timer_provider, "systemd")
        self.assertEqual(capabilities.user_service_manager, "systemd-user")
        self.assertTrue(capabilities.supported)

    def test_linux_arm64_uses_shared_platform_family(self):
        capabilities = platform_capabilities(system="Linux", machine="aarch64")

        self.assertEqual(capabilities.family, "linux")
        self.assertEqual(capabilities.architecture, "arm64")
        self.assertEqual(capabilities.timer_provider, "systemd")

    def test_unknown_platform_is_explicitly_unsupported(self):
        capabilities = platform_capabilities(system="FreeBSD", machine="riscv64")

        self.assertEqual(capabilities.family, "unsupported")
        self.assertEqual(capabilities.architecture, "riscv64")
        self.assertIsNone(capabilities.timer_provider)
        self.assertFalse(capabilities.supported)

    def test_architecture_aliases_are_normalized(self):
        self.assertEqual(normalize_architecture("AMD64"), "x86_64")
        self.assertEqual(normalize_architecture("aarch64"), "arm64")

    def test_default_timer_provider_tracks_detected_platform(self):
        with patch(
            "data_foundation.platform_support.platform_capabilities",
            return_value=platform_capabilities(system="Linux", machine="x86_64"),
        ):
            self.assertEqual(default_timer_provider(), "systemd")

    def test_new_macos_runtime_defaults_to_launchd(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            with patch("data_foundation.settings.default_timer_provider", return_value="launchd"):
                settings = default_settings(paths)

        self.assertEqual(settings["schedule"]["systemTimer"]["provider"], "launchd")

    def test_new_linux_runtime_defaults_to_systemd(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            with patch("data_foundation.settings.default_timer_provider", return_value="systemd"):
                settings = default_settings(paths)

        self.assertEqual(settings["schedule"]["systemTimer"]["provider"], "systemd")
