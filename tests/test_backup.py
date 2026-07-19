import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.backup import (
    BACKUP_SELECTION_KEYS,
    BackupError,
    apply_retention,
    backup_due_bucket,
    create_backup,
    is_backup_due,
    read_backup_status,
    source_runtime_id,
    validate_backup_target,
    verify_backup,
)
from data_foundation.paths import initialize_home


def _selection(*enabled: str) -> dict[str, bool]:
    return {key: key in enabled for key in BACKUP_SELECTION_KEYS}


def _initialize_runtime(root: Path, name: str = "runtime"):
    paths = initialize_home(root / name)
    connection = sqlite3.connect(paths.db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE backup_fixture(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO backup_fixture(value) VALUES ('initial')")
        connection.commit()
    finally:
        connection.close()
    return paths


def _write_full_fixture(paths, marker: str) -> None:
    paths.diary_dir.mkdir(parents=True, exist_ok=True)
    (paths.diary_dir / "2026-07-19.md").write_text("# Diary\n", encoding="utf-8")
    (paths.diary_dir / "ignored.log").write_text("ignored\n", encoding="utf-8")
    weekly = paths.reports_dir / "weekly"
    monthly = paths.reports_dir / "monthly"
    weekly.mkdir(parents=True, exist_ok=True)
    monthly.mkdir(parents=True, exist_ok=True)
    (weekly / "2026-W29.md").write_text("weekly\n", encoding="utf-8")
    (monthly / "2026-07.json").write_text('{"summary": true}\n', encoding="utf-8")
    paths.task_board_path.parent.mkdir(parents=True, exist_ok=True)
    paths.task_board_path.write_text("# Nova-Task\n", encoding="utf-8")
    paths.task_intelligence_dir.mkdir(parents=True, exist_ok=True)
    (paths.task_intelligence_dir / "projection.json").write_text("{}\n", encoding="utf-8")
    work_graph = paths.state_dir / "nova-task" / "work-graph"
    work_graph.mkdir(parents=True, exist_ok=True)
    (work_graph / "result.md").write_text("result\n", encoding="utf-8")
    attribution = paths.state_dir / "workspace-attribution"
    attribution.mkdir(parents=True, exist_ok=True)
    (attribution / "rules.json").write_text('{"schemaVersion": 1, "rules": []}\n', encoding="utf-8")
    (attribution / "catalog.json").write_text('{"schemaVersion": 1, "projects": []}\n', encoding="utf-8")

    api_key_field = "api" + "Key"
    password_field = "pass" + "word"
    settings = {
        "schemaVersion": 1,
        "general": {"workspaceRoot": marker},
        "llmProvider": {
            api_key_field: "-".join(("raw", "secret", "must", "not", "survive")),
            "apiKeyEnv": "LLM_API_KEY",
            "secretRef": {"backend": "runtime-file", "service": "actanara", "account": "private-account"},
        },
        "llmProviderSecrets": {"custom": {"account": "private-account"}},
        "nested": {password_field: "-".join(("private", "password")), "safe": "value"},
    }
    (paths.config_dir / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (paths.config_dir / "projects-registry.json").write_text(
        json.dumps({"version": 1, "projects": [{"canonical_root": marker}]}) + "\n",
        encoding="utf-8",
    )

    rag_root = paths.home / "reserved" / "rag" / "v2"
    active = rag_root / "indexes" / "active" / "run-1"
    active.mkdir(parents=True, exist_ok=True)
    for name in ("index.jsonl", "chunks.jsonl", "embeddings.jsonl", "sources.jsonl"):
        (active / name).write_text('{"id": 1}\n', encoding="utf-8")
    active_manifest = {
        "schemaVersion": 1,
        "status": "active",
        "activeIndexPath": str(active / "index.jsonl"),
        "activeManifestPath": str(active / "manifest.json"),
        "chunksPath": str(active / "chunks.jsonl"),
        "embeddingsPath": str(active / "embeddings.jsonl"),
        "sourcesPath": str(active / "sources.jsonl"),
    }
    (active / "manifest.json").write_text(json.dumps(active_manifest) + "\n", encoding="utf-8")
    rag_root.mkdir(parents=True, exist_ok=True)
    (rag_root / "manifest.json").write_text(json.dumps(active_manifest) + "\n", encoding="utf-8")
    (rag_root / "config.json").write_text('{"schemaVersion": 1}\n', encoding="utf-8")


class BackupTests(unittest.TestCase):
    def test_complete_backup_is_sanitized_consistent_and_self_verifying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            private_marker = str(root / "private" / "workspace")
            _write_full_fixture(paths, private_marker)
            target = root / "target"
            target.mkdir()

            result = create_backup(
                paths,
                target_directory=target,
                retention={"maxBackups": 10, "maxAgeDays": 365},
                actanara_version="1.2.0-test",
                now=datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(result["status"], "completed")
            backup = Path(result["backupPath"])
            verification = verify_backup(backup, expected_runtime_id=source_runtime_id(paths))
            self.assertTrue(verification["valid"], verification)
            manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["actanaraVersion"], "1.2.0-test")
            self.assertFalse(manifest["restoreContract"]["implemented"])
            self.assertTrue(all(not Path(item["path"]).is_absolute() for item in manifest["files"]))
            self.assertFalse(any("legacy" in item["path"].lower() for item in manifest["files"]))
            self.assertFalse(any("locks/" in item["path"] or "logs/" in item["path"] for item in manifest["files"]))

            sanitized_settings = json.loads(
                (backup / "payload" / "settings" / "settings.json").read_text(encoding="utf-8")
            )
            serialized = json.dumps(sanitized_settings, ensure_ascii=False)
            self.assertNotIn("raw-secret-must-not-survive", serialized)
            self.assertNotIn("private-password", serialized)
            self.assertNotIn("private-account", serialized)
            self.assertNotIn(private_marker, serialized)
            self.assertEqual(sanitized_settings["llmProvider"]["apiKey"], "")
            self.assertEqual(sanitized_settings["llmProvider"]["secretRef"], {"configured": True})
            self.assertEqual(sanitized_settings["llmProvider"]["apiKeyEnv"], "LLM_API_KEY")

            runtime_manifest = json.loads(
                (backup / "payload" / "runtime-manifests" / "runtime.json").read_text(encoding="utf-8")
            )
            self.assertTrue(runtime_manifest["backupSanitized"])
            self.assertEqual(runtime_manifest["instanceId"], source_runtime_id(paths))
            self.assertNotIn(str(paths.home), json.dumps(runtime_manifest))

            connection = sqlite3.connect(backup / "payload" / "database" / "actanara_data.sqlite3")
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT value FROM backup_fixture").fetchone()[0], "initial")
            finally:
                connection.close()
            self.assertFalse((backup / "payload" / "database" / "actanara_data.sqlite3-wal").exists())
            self.assertEqual(read_backup_status(paths)["backupId"], result["backupId"])

    def test_sqlite_backup_is_valid_during_wal_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            started = threading.Event()

            def writer() -> None:
                connection = sqlite3.connect(paths.db_path, timeout=10)
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                    started.set()
                    for index in range(100):
                        connection.execute("INSERT INTO backup_fixture(value) VALUES (?)", (f"value-{index}",))
                        connection.commit()
                finally:
                    connection.close()

            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(started.wait(timeout=5))
            result = create_backup(
                paths,
                target_directory=target,
                include=_selection("database"),
                retention={"maxBackups": 2, "maxAgeDays": 30},
            )
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

            snapshot = Path(result["backupPath"]) / "payload" / "database" / "actanara_data.sqlite3"
            connection = sqlite3.connect(snapshot)
            try:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                count = connection.execute("SELECT COUNT(*) FROM backup_fixture").fetchone()[0]
            finally:
                connection.close()
            self.assertGreaterEqual(count, 1)
            self.assertLessEqual(count, 101)

    def test_target_overlap_traversal_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            inside = paths.home / "backup-target"
            inside.mkdir()
            outside = root / "outside"
            outside.mkdir()
            symlink = root / "target-link"
            symlink.symlink_to(outside, target_is_directory=True)
            traversed = outside / ".." / "outside"

            cases = (inside, root, symlink, traversed)
            for candidate in cases:
                with self.subTest(candidate=candidate), self.assertRaises(BackupError):
                    validate_backup_target(paths, candidate, include=_selection("database"))

    def test_selected_source_symlink_and_fifo_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            paths.diary_dir.mkdir(parents=True, exist_ok=True)
            external = root / "external.md"
            external.write_text("private\n", encoding="utf-8")
            (paths.diary_dir / "escape.md").symlink_to(external)
            with self.assertRaisesRegex(BackupError, "symlink"):
                create_backup(
                    paths,
                    target_directory=target,
                    include=_selection("diaryMarkdown"),
                )

            (paths.diary_dir / "escape.md").unlink()
            fifo = paths.reports_dir / "weekly" / "pipe.md"
            fifo.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(fifo)
            with self.assertRaisesRegex(BackupError, "unsupported file type"):
                create_backup(
                    paths,
                    target_directory=target,
                    include=_selection("periodReports"),
                )

    def test_insufficient_disk_space_aborts_without_final_or_staging(self):
        DiskUsage = namedtuple("DiskUsage", "total used free")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            with patch("data_foundation.backup.shutil.disk_usage", return_value=DiskUsage(1, 1, 0)):
                with self.assertRaisesRegex(BackupError, "enough free space"):
                    create_backup(
                        paths,
                        target_directory=target,
                        include=_selection("database"),
                    )

            self.assertEqual(list(target.iterdir()), [])
            status = read_backup_status(paths)
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["error"]["code"], "insufficient-disk-space")

    def test_failure_after_atomic_publish_removes_success_named_directory(self):
        from data_foundation import backup as backup_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            real_verify = backup_module._verify_backup_directory

            def verification(root_path, *, expected_runtime_id, require_final_name):
                if require_final_name:
                    return {"valid": False, "errors": [{"code": "injected"}]}
                return real_verify(
                    root_path,
                    expected_runtime_id=expected_runtime_id,
                    require_final_name=require_final_name,
                )

            with patch.object(backup_module, "_verify_backup_directory", side_effect=verification):
                with self.assertRaisesRegex(BackupError, "published backup verification"):
                    create_backup(
                        paths,
                        target_directory=target,
                        include=_selection("database"),
                    )

            self.assertFalse(any(path.name.startswith("actanara-backup-v1-") for path in target.iterdir()))
            self.assertFalse(any(path.name.endswith(".staging") for path in target.iterdir()))

    def test_manifest_tamper_and_extra_payload_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            result = create_backup(
                paths,
                target_directory=target,
                include=_selection("database"),
            )
            backup = Path(result["backupPath"])
            extra = backup / "payload" / "extra.txt"
            extra.write_text("unlisted\n", encoding="utf-8")
            invalid = verify_backup(backup)
            self.assertFalse(invalid["valid"])
            self.assertEqual(invalid["errors"][0]["code"], "unlisted-payload")

            extra.unlink()
            database = backup / "payload" / "database" / "actanara_data.sqlite3"
            database.write_bytes(database.read_bytes() + b"tamper")
            invalid = verify_backup(backup)
            self.assertFalse(invalid["valid"])
            self.assertEqual(invalid["errors"][0]["code"], "payload-mismatch")

    def test_retention_deletes_only_verified_backups_for_same_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_paths = _initialize_runtime(root, "runtime-one")
            second_paths = _initialize_runtime(root, "runtime-two")
            target = root / "target"
            target.mkdir()
            old = create_backup(
                first_paths,
                target_directory=target,
                include=_selection("database"),
                retention={"maxBackups": 10, "maxAgeDays": 365},
                now=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            new = create_backup(
                first_paths,
                target_directory=target,
                include=_selection("database"),
                retention={"maxBackups": 10, "maxAgeDays": 365},
                now=datetime(2026, 7, 2, tzinfo=timezone.utc),
            )
            foreign = create_backup(
                second_paths,
                target_directory=target,
                include=_selection("database"),
                retention={"maxBackups": 10, "maxAgeDays": 365},
                now=datetime(2026, 7, 3, tzinfo=timezone.utc),
            )
            corrupt = target / "actanara-backup-v1-20260704T000000Z-aaaaaaaaaaaa"
            corrupt.mkdir()
            (corrupt / "manifest.json").write_text("{}\n", encoding="utf-8")
            arbitrary = target / "photos"
            arbitrary.mkdir()

            retention = apply_retention(
                first_paths,
                target,
                max_backups=1,
                max_age_days=365,
                now=datetime(2026, 7, 5, tzinfo=timezone.utc),
            )

            self.assertIn(old["backupId"], retention["deleted"])
            self.assertFalse(Path(old["backupPath"]).exists())
            self.assertTrue(Path(new["backupPath"]).exists())
            self.assertTrue(Path(foreign["backupPath"]).exists())
            self.assertTrue(corrupt.exists())
            self.assertTrue(arbitrary.exists())

    def test_rag_operation_lock_blocks_backup_without_partial_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            rag_root = paths.home / "reserved" / "rag" / "v2"
            rag_root.mkdir(parents=True)
            (rag_root / "manifest.json").write_text('{"schemaVersion": 1}\n', encoding="utf-8")
            locks = rag_root / "locks"
            locks.mkdir()
            lock_path = locks / "sync-promote.lock"
            target = root / "target"
            target.mkdir()
            with lock_path.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    with self.assertRaisesRegex(BackupError, "nova-RAG v2 is busy"):
                        create_backup(
                            paths,
                            target_directory=target,
                            include=_selection("ragV2"),
                        )
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            self.assertEqual(list(target.iterdir()), [])

    def test_unresolved_settings_transaction_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            (paths.config_dir / "settings.json").write_text('{"schemaVersion": 1}\n', encoding="utf-8")
            transaction = paths.state_dir / "settings-transactions" / ("a" * 32)
            transaction.mkdir(parents=True)
            (transaction / "journal.json").write_text(
                '{"status": "active", "phase": "settings-committed"}\n',
                encoding="utf-8",
            )
            target = root / "target"
            target.mkdir()
            with self.assertRaisesRegex(BackupError, "recovered before backup"):
                create_backup(
                    paths,
                    target_directory=target,
                    include=_selection("settings"),
                )
            self.assertEqual(list(target.iterdir()), [])

    def test_scheduled_success_bucket_is_only_advanced_after_success(self):
        DiskUsage = namedtuple("DiskUsage", "total used free")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _initialize_runtime(root)
            target = root / "target"
            target.mkdir()
            create_backup(
                paths,
                target_directory=target,
                include=_selection("database"),
                trigger="scheduled",
                schedule_bucket="2026-W29",
            )
            with patch("data_foundation.backup.shutil.disk_usage", return_value=DiskUsage(1, 1, 0)):
                with self.assertRaises(BackupError):
                    create_backup(
                        paths,
                        target_directory=target,
                        include=_selection("database"),
                        trigger="scheduled",
                        schedule_bucket="2026-W30",
                    )
            status = read_backup_status(paths)
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["lastSuccessfulScheduleBucket"], "2026-W29")

    def test_schedule_buckets_are_calendar_stable(self):
        now = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)
        self.assertEqual(backup_due_bucket("daily", now), "2026-07-19")
        self.assertEqual(backup_due_bucket("weekly", now), "2026-W29")
        self.assertEqual(backup_due_bucket("monthly", now), "2026-07")
        self.assertFalse(is_backup_due(frequency="weekly", now=now, last_success_bucket="2026-W29"))
        self.assertTrue(is_backup_due(frequency="weekly", now=now + timedelta(days=7), last_success_bucket="2026-W29"))
        local_midnight = datetime(2026, 7, 19, 0, 30, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(backup_due_bucket("daily", local_midnight), "2026-07-19")


if __name__ == "__main__":
    unittest.main()
