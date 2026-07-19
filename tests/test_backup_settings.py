import json
import tempfile
import unittest
from pathlib import Path

from data_foundation.paths import initialize_home
from data_foundation.settings import (
    default_settings,
    normalize_backup_settings_update,
    validate_operator_settings_update,
    write_backup_settings,
)


class BackupSettingsTests(unittest.TestCase):
    def test_defaults_are_safe_and_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "runtime")
            backup = default_settings(paths)["backup"]

        self.assertEqual(backup["targetDirectory"], "")
        self.assertFalse(backup["schedule"]["enabled"])
        self.assertEqual(backup["schedule"]["frequency"], "weekly")
        self.assertTrue(backup["include"]["database"])
        self.assertEqual(backup["retention"], {"maxBackups": 7, "maxAgeDays": 30})

    def test_generic_operator_endpoint_cannot_mutate_backup_policy(self):
        with self.assertRaisesRegex(ValueError, "dedicated API"):
            validate_operator_settings_update({"backup": {"schedule": {"enabled": True}}})

    def test_normalizer_rejects_relative_traversal_and_unknown_fields(self):
        for payload in (
            {"targetDirectory": "relative/backups"},
            {"targetDirectory": "/tmp/safe/../escape"},
            {"schedule": {"frequency": "hourly"}},
            {"retention": {"maxBackups": 0}},
            {"include": {"legacyIndex": True}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_backup_settings_update(payload)

    def test_dedicated_writer_uses_settings_transaction_and_keeps_schema_additive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "runtime")
            target = root / "backups"
            target.mkdir()
            saved = write_backup_settings(
                {
                    "targetDirectory": str(target),
                    "retention": {"maxBackups": 3, "maxAgeDays": 14},
                    "schedule": {"enabled": True, "frequency": "daily", "timeOfDay": "06:15"},
                },
                paths,
                readiness_verifier=lambda: None,
            )

            persisted = json.loads((paths.config_dir / "settings.json").read_text(encoding="utf-8"))

        self.assertEqual(saved["backup"]["targetDirectory"], str(target))
        self.assertEqual(saved["backup"]["retention"]["maxBackups"], 3)
        self.assertTrue(saved["backup"]["schedule"]["enabled"])
        self.assertEqual(saved["schemaVersion"], 1)
        self.assertIn("settingsTransaction", saved)
        self.assertEqual(persisted["backup"], saved["backup"])

    def test_schedule_requires_target_and_one_selected_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "runtime")
            with self.assertRaisesRegex(ValueError, "targetDirectory"):
                write_backup_settings({"schedule": {"enabled": True}}, paths)
            with self.assertRaisesRegex(ValueError, "select at least one"):
                write_backup_settings(
                    {"include": {key: False for key in default_settings(paths)["backup"]["include"]}},
                    paths,
                )


if __name__ == "__main__":
    unittest.main()
