import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.db import connect, migrate
from data_foundation.jobs import begin_ingestion_run
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.snapshots import write_dashboard_snapshot
from data_foundation.sqlite_cache_rebuild import (
    SQLITE_CACHE_REBUILD_CONFIRMATION,
    plan_sqlite_cache_rebuild,
    rebuild_sqlite_cache,
)


class SqliteCacheRebuildTests(unittest.TestCase):
    def test_plan_reports_dangerous_rebuild_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary = root / "Diary"
            day = diary / "diary-2026-06-07"
            day.mkdir(parents=True)
            (day / "日记-260607.md").write_text("# 日记\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary).home, generated_diary_root=diary, legacy_diary_root=diary)

            plan = plan_sqlite_cache_rebuild(paths)

        self.assertTrue(plan["dryRun"])
        self.assertTrue(plan["dangerous"])
        self.assertEqual(plan["confirmationTextRequired"], SQLITE_CACHE_REBUILD_CONFIRMATION)
        self.assertEqual(plan["diaryDates"], 1)
        self.assertEqual(plan["diaryDateRange"], {"startDate": "2026-06-07", "endDate": "2026-06-07"})

    def test_plan_default_end_date_uses_business_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary = root / "Diary"
            day = diary / "diary-2026-06-07"
            day.mkdir(parents=True)
            (day / "日记-260607.md").write_text("# 日记\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary).home, generated_diary_root=diary, legacy_diary_root=diary)

            with patch("data_foundation.sqlite_cache_rebuild.business_today", return_value=date(2026, 6, 9)):
                plan = plan_sqlite_cache_rebuild(paths)

        self.assertEqual(plan["diaryDateRange"], {"startDate": "2026-06-07", "endDate": "2026-06-07"})
        self.assertEqual(plan["rebuildRange"], {"startDate": "2026-06-07", "endDate": "2026-06-09"})

    def test_rebuild_backs_up_and_replaces_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary = root / "Diary"
            day = diary / "diary-2026-06-07"
            day.mkdir(parents=True)
            (day / "日记-260607.md").write_text("# 2026-06-07\n\n## 今日概要\n新缓存\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary).home, generated_diary_root=diary, legacy_diary_root=diary)
            migrate(paths)
            old_run = begin_ingestion_run(paths, trigger_type="old-cache", business_date=date(2026, 5, 14))
            write_dashboard_snapshot(paths, {"diary": {"count": 1}}, source_run_id=old_run)
            old_size = paths.db_path.stat().st_size
            usage_result = SimpleNamespace(
                run_id=99,
                artifacts_seen=2,
                events_seen=3,
                events_in_window=3,
                errors=0,
            )

            with (
                patch("data_foundation.sqlite_cache_rebuild.run_shadow_period_ingestion", return_value=usage_result),
                patch("data_foundation.sqlite_cache_rebuild.materialize_legacy_asset_projection", return_value="legacy-dashboard-assets-v1:2026-06-07:2026-06-07"),
            ):
                result = rebuild_sqlite_cache(
                    paths,
                    confirmation_text=SQLITE_CACHE_REBUILD_CONFIRMATION,
                    start_date=date(2026, 6, 7),
                    end_date=date(2026, 6, 7),
                    ai_assets_builder=lambda: {"diary": {"count": 1}, "tools": []},
                )

            self.assertEqual(result["status"], "completed")
            self.assertTrue(Path(result["backup"]["backupDir"]).exists())
            self.assertTrue((Path(result["backup"]["backupDir"]) / "nova_data.sqlite3").exists())
            self.assertGreaterEqual((Path(result["backup"]["backupDir"]) / "nova_data.sqlite3").stat().st_size, old_size)
            with connect(paths, read_only=True) as connection:
                triggers = [row["trigger_type"] for row in connection.execute("SELECT trigger_type FROM ingestion_runs ORDER BY id")]
                snapshot = connection.execute("SELECT payload_json FROM dashboard_snapshots WHERE snapshot_key = 'ai-assets:latest:non-rag'").fetchone()
                docs = connection.execute("SELECT COUNT(*) FROM diary_markdown_documents").fetchone()[0]
            self.assertNotIn("old-cache", triggers)
            self.assertIn("operator-sqlite-cache-rebuild", triggers)
            self.assertEqual(json.loads(snapshot["payload_json"])["diary"]["count"], 1)
            self.assertEqual(docs, 1)

    def test_rebuild_restores_original_database_when_materialization_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary = root / "Diary"
            day = diary / "diary-2026-06-07"
            day.mkdir(parents=True)
            (day / "日记-260607.md").write_text("# 2026-06-07\n\n## 今日概要\n恢复测试\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary).home,
                generated_diary_root=diary,
                legacy_diary_root=diary,
            )
            migrate(paths)
            old_run = begin_ingestion_run(paths, trigger_type="old-cache", business_date=date(2026, 5, 14))
            write_dashboard_snapshot(paths, {"diary": {"count": 99}}, source_run_id=old_run)
            original_bytes = paths.db_path.read_bytes()
            usage_result = SimpleNamespace(
                run_id=100,
                artifacts_seen=1,
                events_seen=1,
                events_in_window=1,
                errors=0,
            )

            with (
                patch("data_foundation.sqlite_cache_rebuild.run_shadow_period_ingestion", return_value=usage_result),
                patch(
                    "data_foundation.sqlite_cache_rebuild.materialize_diary_markdown_period_documents",
                    side_effect=RuntimeError("materialization failed"),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    rebuild_sqlite_cache(
                        paths,
                        confirmation_text=SQLITE_CACHE_REBUILD_CONFIRMATION,
                        start_date=date(2026, 6, 7),
                        end_date=date(2026, 6, 7),
                    )

            self.assertEqual(paths.db_path.read_bytes(), original_bytes)
            with connect(paths, read_only=True) as connection:
                triggers = [row["trigger_type"] for row in connection.execute("SELECT trigger_type FROM ingestion_runs ORDER BY id")]
                snapshot = connection.execute("SELECT payload_json FROM dashboard_snapshots WHERE snapshot_key = 'ai-assets:latest:non-rag'").fetchone()
            self.assertEqual(triggers, ["old-cache"])
            self.assertEqual(json.loads(snapshot["payload_json"])["diary"]["count"], 99)

    def test_rebuild_requires_exact_confirmation_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            with self.assertRaises(ValueError):
                rebuild_sqlite_cache(paths, confirmation_text="wrong")


if __name__ == "__main__":
    unittest.main()
