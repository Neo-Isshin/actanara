import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import ai_assets as ai_assets_router
from app.services import backups
from app.services.dashboard_security import is_protected_path, is_session_exempt_path
from data_foundation.backup import BackupError
from data_foundation.paths import initialize_home


def _selection(**enabled):
    result = {
        "database": False,
        "diaryMarkdown": False,
        "periodReports": False,
        "ragV2": False,
        "novaTaskExports": False,
        "settings": False,
        "workspaceAttribution": False,
        "runtimeManifests": False,
    }
    result.update(enabled)
    return result


def _body(response):
    return json.loads(response.body.decode("utf-8"))


class DashboardBackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = initialize_home(root / "runtime")
        self.target = root / "backups-target"
        self.target.mkdir()
        self.paths_patch = patch.object(backups, "load_paths", return_value=self.paths)
        self.paths_patch.start()
        with backups._PENDING_LOCK:
            backups._PENDING.clear()

    def tearDown(self):
        with backups._PENDING_LOCK:
            backups._PENDING.clear()
        self.paths_patch.stop()
        self.temp.cleanup()

    def _save_settings(self, *, scheduled=False, frequency="daily", time_of_day="05:00"):
        return backups.update_backup_settings(
            {
                "targetDirectory": str(self.target),
                "include": _selection(runtimeManifests=True),
                "retention": {"maxBackups": 3, "maxAgeDays": 14},
                "schedule": {
                    "enabled": scheduled,
                    "frequency": frequency,
                    "timeOfDay": time_of_day,
                },
            }
        )

    def test_settings_status_run_and_verify_form_a_safe_facade(self):
        saved = self._save_settings()
        self.assertTrue(saved["targetReadiness"]["ready"])
        self.assertEqual(saved["settings"]["targetDirectory"], str(self.target))
        self.assertFalse(saved["capabilities"]["restore"])

        with self.assertRaises(backups.BackupFacadeError) as mismatch:
            backups.queue_backup({"confirmationText": "yes"})
        self.assertEqual(mismatch.exception.code, "backup-confirmation-mismatch")

        queued = backups.queue_backup({"confirmationText": backups.BACKUP_CONFIRMATION})
        result = backups.execute_backup(queued["jobId"])
        self.assertIn(result["status"], {"completed", "completed_with_warnings"})
        self.assertRegex(result["backupId"], r"^actanara-backup-v1-")
        self.assertTrue(result["verification"]["valid"])
        self.assertNotIn("backupPath", result)
        self.assertNotIn("manifestPath", result)

        verified = backups.verify_backup_by_id(result["backupId"])
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["backupId"], result["backupId"])
        self.assertNotIn("manifestPath", verified)
        self.assertNotIn("sourceRuntimeId", verified)

        status = backups.get_backup_status()
        serialized = json.dumps(status, ensure_ascii=False)
        self.assertNotIn(str(self.paths.home), serialized)
        self.assertNotIn("manifestPath", serialized)
        self.assertNotIn("backupPath", serialized)
        self.assertNotIn("sourceRuntimeId", serialized)
        self.assertEqual(status["latestRun"]["backupId"], result["backupId"])

    def test_verify_rejects_traversal_before_engine_access(self):
        self._save_settings()
        with patch.object(backups.backup_engine, "verify_backup") as verify:
            for unsafe in ("../escape", "..", "/absolute", "a/b", "nul\x00id"):
                with self.subTest(unsafe=unsafe), self.assertRaises(backups.BackupFacadeError) as raised:
                    backups.verify_backup_by_id(unsafe)
                self.assertEqual(raised.exception.code, "backup-id-invalid")
        verify.assert_not_called()

    def test_settings_readiness_uses_engine_validation_and_compensates_failure(self):
        before = backups.get_backup_status()["settings"]
        with patch.object(
            backups.backup_engine,
            "validate_backup_target",
            side_effect=BackupError("target-source-overlap", "target overlaps runtime"),
        ) as validate:
            with self.assertRaises(Exception):
                self._save_settings(scheduled=True)
        self.assertGreaterEqual(validate.call_count, 1)
        after = backups.get_backup_status()["settings"]
        self.assertEqual(after, before)

    def test_due_schedule_waits_for_time_and_deduplicates_success_bucket(self):
        self._save_settings(scheduled=True, frequency="daily", time_of_day="05:00")

        early = backups.run_due_backup(datetime(2026, 7, 19, 4, 59))
        self.assertEqual(early, {"status": "skipped", "reason": "before-scheduled-time", "scheduledTime": "05:00"})
        self.assertEqual(list(self.target.iterdir()), [])

        completed = backups.run_due_backup(datetime(2026, 7, 19, 5, 1))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["scheduleBucket"], "2026-07-19")
        repeated = backups.run_due_backup(datetime(2026, 7, 19, 8, 0))
        self.assertEqual(repeated["reason"], "schedule-bucket-complete")
        self.assertEqual(len(list(self.target.iterdir())), 1)

    def test_failed_scheduled_attempt_keeps_bucket_retryable(self):
        self._save_settings(scheduled=True, frequency="daily", time_of_day="05:00")
        attempt = datetime(2026, 7, 19, 5, 1)
        with patch.object(backups, "_create_backup", side_effect=backups.BackupFacadeError("backup-failed", "failed")):
            with self.assertRaises(backups.BackupFacadeError):
                backups.run_due_backup(attempt)

        status = backups.get_backup_status()
        self.assertIsNone(status["lastSuccessfulScheduleBucket"])
        retried = backups.run_due_backup(attempt)
        self.assertEqual(retried["status"], "completed")


class DashboardBackupRouterTests(unittest.TestCase):
    def test_router_status_and_errors_use_public_facade_contract(self):
        expected = {"schemaVersion": 1, "targetReadiness": {"ready": True}}
        with patch.object(ai_assets_router.backups, "get_backup_status", return_value=expected):
            response = asyncio.run(ai_assets_router.api_ai_assets_backup_status())
        self.assertEqual(response, expected)

        private_detail = "/private/runtime/settings.json apiKey=synthetic-secret"
        with patch.object(ai_assets_router.backups, "get_backup_status", side_effect=RuntimeError(private_detail)):
            response = asyncio.run(ai_assets_router.api_ai_assets_backup_status())
        self.assertEqual(response.status_code, 500)
        self.assertEqual(_body(response)["code"], "backup-status-unavailable")
        self.assertNotIn(private_detail, response.body.decode("utf-8"))

    def test_router_queues_background_run_with_202(self):
        background = BackgroundTasks()
        queued = {"accepted": True, "status": "queued", "jobId": "backup-job-1"}
        with patch.object(ai_assets_router.backups, "queue_backup", return_value=queued):
            response = asyncio.run(
                ai_assets_router.api_ai_assets_backup_run(
                    background,
                    {"confirmationText": backups.BACKUP_CONFIRMATION},
                )
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(_body(response), queued)
        self.assertEqual(len(background.tasks), 1)
        self.assertIs(background.tasks[0].func, ai_assets_router.backups.execute_backup)
        self.assertEqual(background.tasks[0].args, ("backup-job-1",))

    def test_router_settings_and_verification_delegate_to_service(self):
        payload = {"targetDirectory": "/tmp/actanara-backups"}
        with patch.object(ai_assets_router.backups, "update_backup_settings", return_value={"saved": True}) as update:
            response = asyncio.run(ai_assets_router.api_ai_assets_backup_settings(payload))
        self.assertEqual(response, {"saved": True})
        update.assert_called_once_with(payload)

        with patch.object(ai_assets_router.backups, "verify_backup_by_id", return_value={"valid": True}) as verify:
            response = asyncio.run(ai_assets_router.api_ai_assets_backup_verify("backup-1"))
        self.assertEqual(response, {"valid": True})
        verify.assert_called_once_with("backup-1")

        error = backups.BackupFacadeError("backup-id-invalid", "invalid")
        with patch.object(ai_assets_router.backups, "verify_backup_by_id", side_effect=error):
            response = asyncio.run(ai_assets_router.api_ai_assets_backup_verify("bad"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_body(response)["code"], "backup-id-invalid")

    def test_backup_routes_remain_inside_session_csrf_host_origin_boundary(self):
        paths = (
            "/api/ai-assets/backups/status",
            "/api/ai-assets/backups/settings",
            "/api/ai-assets/backups/run",
            "/api/ai-assets/backups/actanara-backup-v1-fixture/verify",
        )
        self.assertTrue(all(is_protected_path(path) for path in paths))
        self.assertTrue(all(not is_session_exempt_path(path) for path in paths))


if __name__ == "__main__":
    unittest.main()
