import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/dashboard/app/static/js/app.js").read_text(encoding="utf-8")
HTML = (ROOT / "src/dashboard/app/static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "src/dashboard/app/static/css/style.css").read_text(encoding="utf-8")


class DashboardBackupStaticContractTests(unittest.TestCase):
    def test_ai_assets_has_local_backup_entry_and_lucide_archive_icon(self):
        self.assertIn('id="aiAssetsBackupBtn"', HTML)
        self.assertIn("openAiAssetsBackupModal()", HTML)
        self.assertIn('data-share-icon="archive"', HTML)
        self.assertIn("archive: '<rect", APP)

    def test_backup_ui_uses_only_service_router_endpoints_and_has_no_restore_action(self):
        self.assertIn("fetch('/api/ai-assets/backups/status')", APP)
        self.assertIn("fetch('/api/ai-assets/backups/settings'", APP)
        self.assertIn("fetch('/api/ai-assets/backups/run'", APP)
        self.assertIn("'/verify'", APP)
        self.assertNotIn("/api/ai-assets/backups/restore", APP)
        self.assertIn("backupRestoreUnavailable", APP)

    def test_backup_form_covers_selection_retention_schedule_and_status(self):
        for marker in (
            "data-backup-include",
            "backupTargetDirectory",
            "backupRetentionCount",
            "backupRetentionDays",
            "backupScheduleEnabled",
            "backupScheduleFrequency",
            "backupScheduleTime",
            "backupConfirmationText",
            "backupActionStatus",
            "verifyLatestAiAssetsBackup",
        ):
            self.assertIn(marker, APP)

    def test_backup_copy_is_bilingual_and_security_boundary_is_visible(self):
        for text in (
            "secret、缓存、日志、legacy index 与源码目录不会进入备份",
            "Secrets, caches, logs, the legacy index, and source checkout are excluded",
            "当前版本不提供 restore",
            "Restore is not available in this version",
            "BACK UP ACTANARA DATA",
        ):
            self.assertIn(text, APP)

    def test_backup_modal_has_mobile_and_action_state_styles(self):
        self.assertIn(".backup-modal", CSS)
        self.assertIn(".backup-check-grid", CSS)
        self.assertIn(".backup-action-status", CSS)
        mobile = CSS.rsplit("@media(max-width:720px)", 1)[-1]
        self.assertIn(".backup-settings-grid", mobile)
        self.assertIn(".backup-run-controls", mobile)


if __name__ == "__main__":
    unittest.main()
