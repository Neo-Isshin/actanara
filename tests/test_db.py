import hashlib
import json
import sqlite3
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_foundation.adapters.registry import ToolRegistry
from data_foundation.db import MIGRATIONS_DIR, connect, migrate, seed_projects
from data_foundation.paths import initialize_home

EXPECTED_MIGRATIONS = [
    "0001_initial",
    "0002_shadow_usage",
    "0003_period_reports",
    "0004_dashboard_snapshots",
    "0005_task_shadow",
    "0006_task_report_events",
    "0007_task_board_mutations",
    "0008_diary_markdown_documents",
    "0009_task_board_projection",
    "0010_system_component_registry",
    "0011_nova_task_authority",
    "0012_foundation_repair_runs",
    "0013_ai_assets_usage_cache",
    "0014_nova_task_status_vocabulary",
    "0015_nova_task_l1_review_view",
    "0016_nova_task_node_management",
    "0017_infrastructure_graph",
    "0018_pipeline_runs",
]


class FoundationDatabaseTests(unittest.TestCase):
    def test_release_migration_contract_exactly_covers_immutable_bodies(self):
        contract = json.loads(
            (MIGRATIONS_DIR.parent / "migration_compatibility.json").read_text(encoding="utf-8")
        )
        records = contract["migrations"]
        declared = {record["version"]: record for record in records}
        actual = {
            path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in MIGRATIONS_DIR.glob("*.sql")
        }

        self.assertEqual(contract["schemaVersion"], 1)
        self.assertEqual(contract["policy"], "rollback-compatible-additive-only")
        self.assertEqual(contract["preCommitWriterContract"], "prior-reader-compatible-v1")
        self.assertEqual(contract["minimumReadableSchema"], "unversioned")
        self.assertEqual(contract["maximumReadableSchema"], EXPECTED_MIGRATIONS[-1])
        self.assertEqual([record["version"] for record in records], EXPECTED_MIGRATIONS)
        self.assertEqual({version: record["sha256"] for version, record in declared.items()}, actual)
        self.assertEqual(
            {
                version
                for version, record in declared.items()
                if record["rollbackClass"] == "breaking"
            },
            {"0014_nova_task_status_vocabulary", "0016_nova_task_node_management"},
        )

    def test_migration_is_repeatable_and_registry_lifecycle_is_audit_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            self.assertEqual(migrate(paths), EXPECTED_MIGRATIONS)
            self.assertEqual(migrate(paths), [])
            with connect(paths) as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
            registry = ToolRegistry(paths)
            registry.register(
                tool_key="codex",
                display_name="Codex",
                adapter_version="contract-only-v1",
                capabilities={"usage_events", "workspace_metadata"},
            )
            registry.set_enabled("codex", True)
            self.assertTrue(registry.list()[0].enabled)
            registry.retire("codex")
            self.assertFalse(registry.list()[0].enabled)
            self.assertIsNotNone(registry.list()[0].retired_at)

    def test_manual_project_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            migrate(paths)
            projects = [
                {
                    "canonical_name": "actanara",
                    "canonical_root": "/workspace/example/actanara",
                    "aliases": ["nova"],
                }
            ]
            seed_projects(paths, projects)
            seed_projects(paths, projects)
            with connect(paths, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM project_aliases").fetchone()[0], 1)

    def test_migration_recovers_after_ddl_was_applied_without_version_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            with connect(paths) as connection:
                connection.executescript((MIGRATIONS_DIR / "0001_initial.sql").read_text(encoding="utf-8"))
            self.assertEqual(migrate(paths), EXPECTED_MIGRATIONS)

    def test_failed_migration_rolls_back_schema_data_and_version_then_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara")
            migrations = root / "migrations"
            migrations.mkdir()
            migration = migrations / "0001_failure_atomicity.sql"
            migration.write_text(
                "CREATE TABLE migration_probe(value TEXT NOT NULL);\n"
                "INSERT INTO migration_probe(value) VALUES ('before-failure');\n"
                "THIS IS NOT VALID SQL;\n",
                encoding="utf-8",
            )

            with patch("data_foundation.db.MIGRATIONS_DIR", migrations):
                with self.assertRaises(sqlite3.Error):
                    migrate(paths)
                with connect(paths, read_only=True) as connection:
                    table_count = connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='migration_probe'"
                    ).fetchone()[0]
                    version_count = connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version='0001_failure_atomicity'"
                    ).fetchone()[0]
                self.assertEqual(table_count, 0)
                self.assertEqual(version_count, 0)

                migration.write_text(
                    "CREATE TABLE migration_probe(value TEXT NOT NULL);\n"
                    "INSERT INTO migration_probe(value) VALUES ('after-retry');\n",
                    encoding="utf-8",
                )
                self.assertEqual(migrate(paths), ["0001_failure_atomicity"])
                self.assertEqual(migrate(paths), [])

            with connect(paths, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT value FROM migration_probe").fetchone()[0], "after-retry")

    def test_concurrent_migrate_calls_serialize_without_partial_or_duplicate_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            env = {
                **os.environ,
                "ACTANARA_HOME": str(paths.home),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            }
            command = [
                sys.executable,
                "-c",
                "from data_foundation.db import migrate; print(len(migrate()))",
            ]
            processes = [
                subprocess.Popen(command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(2)
            ]
            results = [process.communicate(timeout=30) for process in processes]

            self.assertEqual([process.returncode for process in processes], [0, 0], results)
            self.assertEqual(sorted(int(stdout.strip()) for stdout, _ in results), [0, len(EXPECTED_MIGRATIONS)])
            with connect(paths, read_only=True) as connection:
                versions = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(versions, len(EXPECTED_MIGRATIONS))
            self.assertEqual(integrity, "ok")

    def test_sigkill_during_migration_body_rolls_back_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara")
            migrations = root / "migrations"
            migrations.mkdir()
            marker = root / "migration-paused"
            migration = migrations / "0001_sigkill_atomicity.sql"
            migration.write_text(
                "CREATE TABLE sigkill_probe(value TEXT NOT NULL);\n"
                "INSERT INTO sigkill_probe(value) VALUES ('must-rollback');\n"
                "SELECT test_pause_migration();\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "ACTANARA_HOME": str(paths.home),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "ACTANARA_TEST_MIGRATIONS": str(migrations),
                "ACTANARA_TEST_MARKER": str(marker),
            }
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    """
from contextlib import contextmanager
import os
from pathlib import Path
import time
import data_foundation.db as db

db.MIGRATIONS_DIR = Path(os.environ["ACTANARA_TEST_MIGRATIONS"])
original_connect = db.connect

@contextmanager
def test_connect(*args, **kwargs):
    with original_connect(*args, **kwargs) as connection:
        def pause():
            Path(os.environ["ACTANARA_TEST_MARKER"]).write_text("paused", encoding="utf-8")
            time.sleep(60)
            return 0
        connection.create_function("test_pause_migration", 0, pause)
        yield connection

db.connect = test_connect
db.migrate()
""",
                ],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                if not marker.exists():
                    stdout, stderr = child.communicate(timeout=1)
                    self.fail((stdout, stderr))
                os.kill(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)

            self.assertEqual(child.returncode, -signal.SIGKILL)
            with connect(paths, read_only=True) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sigkill_probe'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version='0001_sigkill_atomicity'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

            migration.write_text(
                "CREATE TABLE sigkill_probe(value TEXT NOT NULL);\n"
                "INSERT INTO sigkill_probe(value) VALUES ('after-retry');\n",
                encoding="utf-8",
            )
            with patch("data_foundation.db.MIGRATIONS_DIR", migrations):
                self.assertEqual(migrate(paths), ["0001_sigkill_atomicity"])
            with connect(paths, read_only=True) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sigkill_probe").fetchone()[0], "after-retry")

    def test_version_registry_insert_failure_rolls_back_migration_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara")
            migrations = root / "migrations"
            migrations.mkdir()
            migration = migrations / "0001_version_failure.sql"
            migration.write_text(
                "CREATE TABLE version_failure_probe(value TEXT NOT NULL);\n"
                "INSERT INTO version_failure_probe(value) VALUES ('must-rollback');\n",
                encoding="utf-8",
            )
            with patch("data_foundation.db.MIGRATIONS_DIR", migrations):
                empty = root / "empty-migrations"
                empty.mkdir()
                with patch("data_foundation.db.MIGRATIONS_DIR", empty):
                    self.assertEqual(migrate(paths), [])
                with connect(paths) as connection:
                    connection.execute(
                        """
                        CREATE TRIGGER reject_schema_version
                        BEFORE INSERT ON schema_migrations
                        BEGIN
                            SELECT RAISE(ABORT, 'synthetic version write failure');
                        END
                        """
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    migrate(paths)
                with connect(paths, read_only=True) as connection:
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='version_failure_probe'"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 0)
                with connect(paths) as connection:
                    connection.execute("DROP TRIGGER reject_schema_version")
                self.assertEqual(migrate(paths), ["0001_version_failure"])

    def test_nova_task_status_vocabulary_migrates_existing_candidate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            with connect(paths) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if migration.stem >= "0014_nova_task_status_vocabulary":
                        continue
                    connection.executescript(migration.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, '2026-07-02T00:00:00+08:00')",
                        (migration.stem,),
                    )
                connection.execute(
                    """
                    INSERT INTO nova_task_nodes(
                        node_id, node_type, title, status, progress, scope_json, metadata_json, created_at, updated_at
                    ) VALUES ('NT-OLD', 'track', 'Old root', 'active', 0, '{}', '{}', '2026-07-02T00:00:00+08:00', '2026-07-02T00:00:00+08:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_candidates(
                        candidate_id, candidate_type, proposed_title, status, confidence, reason,
                        evidence_json, source_fingerprint, metadata_json, created_at, updated_at
                    ) VALUES (
                        'NTC-OLD', 'parent_task', 'Old candidate', 'pending', 'high', 'old status',
                        '[]', 'fp-old', '{}', '2026-07-02T00:00:00+08:00', '2026-07-02T00:00:00+08:00'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_reconciliation_decisions(
                        decision_id, candidate_id, decision_type, actor, reason, before_json, after_json, created_at
                    ) VALUES ('NTD-OLD', 'NTC-OLD', 'attach_as_subtask', 'test', 'old attach', '{}', '{}', '2026-07-02T00:00:00+08:00')
                    """
                )

            self.assertEqual(
                migrate(paths),
                [
                    "0014_nova_task_status_vocabulary",
                    "0015_nova_task_l1_review_view",
                    "0016_nova_task_node_management",
                    "0017_infrastructure_graph",
                    "0018_pipeline_runs",
                ],
            )
            with connect(paths) as connection:
                candidate_status = connection.execute(
                    "SELECT status FROM nova_task_candidates WHERE candidate_id = 'NTC-OLD'"
                ).fetchone()["status"]
                old_decision = connection.execute(
                    "SELECT decision_type FROM nova_task_reconciliation_decisions WHERE decision_id = 'NTD-OLD'"
                ).fetchone()["decision_type"]
                connection.execute("UPDATE nova_task_nodes SET status = 'paused' WHERE node_id = 'NT-OLD'")
                connection.execute("UPDATE nova_task_candidates SET status = 'superseded' WHERE candidate_id = 'NTC-OLD'")
                connection.execute(
                    """
                    INSERT INTO nova_task_reconciliation_decisions(
                        decision_id, candidate_id, decision_type, actor, reason, before_json, after_json, created_at
                    ) VALUES ('NTD-NEW', 'NTC-OLD', 'attached', 'test', 'new attach', '{}', '{}', '2026-07-02T00:01:00+08:00')
                    """
                )

        self.assertEqual(candidate_status, "pending_review")
        self.assertEqual(old_decision, "attach_as_subtask")

    def test_nova_task_l1_review_view_filters_parent_task_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            migrate(paths)
            with connect(paths) as connection:
                connection.execute(
                    """
                    INSERT INTO nova_task_candidates(
                        candidate_id, candidate_type, proposed_title, status, confidence, reason,
                        evidence_json, source_fingerprint, metadata_json, created_at, updated_at
                    ) VALUES
                    ('NTC-L1', 'parent_task', 'L1 proposal', 'pending_review', 'high', 'root', '[]', 'fp-l1', '{}', '2026-07-02T00:00:00+08:00', '2026-07-02T00:00:00+08:00'),
                    ('NTC-L2', 'subtask', 'L2 legacy candidate', 'pending_review', 'high', 'legacy', '[]', 'fp-l2', '{}', '2026-07-02T00:00:00+08:00', '2026-07-02T00:00:00+08:00')
                    """
                )
                rows = connection.execute(
                    "SELECT review_id, proposed_title FROM nova_task_l1_review_items ORDER BY review_id"
                ).fetchall()

        self.assertEqual([(row["review_id"], row["proposed_title"]) for row in rows], [("NTC-L1", "L1 proposal")])


if __name__ == "__main__":
    unittest.main()
