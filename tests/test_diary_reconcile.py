import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from data_foundation.db import connect, migrate
from data_foundation.diary_markdown import DIARY_PERIOD_PAGE_PROJECTION, materialize_diary_markdown_day
from data_foundation.diary_reconcile import (
    plan_diary_projection_rebuild,
    rebuild_diary_projections,
    recent_diary_projection_rebuild_jobs,
)
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.period_summary import DIARY_PERIOD_SUMMARY_PROJECTION
from data_foundation.reports import read_period_projection
from app.services import settings as dashboard_settings


class DiaryReconcileTests(unittest.TestCase):
    def test_rebuild_reconciles_markdown_documents_and_period_reports_for_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_diary = root / "OldDiary"
            old_day = old_diary / "diary-2026-06-05"
            old_day.mkdir(parents=True)
            (old_day / "日记-260605.md").write_text(
                "# 旧日记\n\n## 今日概要\n旧路径\n",
                encoding="utf-8",
            )
            (old_day / "技术进展-260605.md").write_text(
                "# 旧技术\n\n## 技术进展\n保留为 stale\n",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary").home,
                generated_diary_root=old_diary,
                legacy_diary_root=old_diary,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 6, 5), source_run_id=None)

            new_diary = root / "NewDiary"
            new_day = new_diary / "diary-2026-06-05"
            new_day.mkdir(parents=True)
            (new_day / "日记-260605.md").write_text(
                """# 2026年06月05日 日记

## 今日概要

### Reconcile landed
- Rebuilt markdown projection
""",
                encoding="utf-8",
            )
            (new_day / "智慧沉淀-260605.md").write_text(
                """# 智慧沉淀

## 黄金教训
- **【codex】**: rebuild 需要审计。解决建议：写入 ingestion run。
""",
                encoding="utf-8",
            )

            plan = plan_diary_projection_rebuild(
                paths,
                date(2026, 6, 5),
                date(2026, 6, 5),
                diary_root=new_diary,
            )
            self.assertTrue(plan["dryRun"])
            self.assertEqual(plan["wouldDeleteRows"], 0)
            self.assertEqual(plan["wouldUpsertDocuments"], 2)
            self.assertEqual(plan["matchedRows"], 1)
            self.assertEqual(plan["missingDiskFiles"], ["diary-2026-06-05/技术进展-260605.md"])
            self.assertEqual(plan["missingDatabaseRows"], ["diary-2026-06-05/智慧沉淀-260605.md"])

            result = rebuild_diary_projections(
                paths,
                date(2026, 6, 5),
                date(2026, 6, 5),
                diary_root=new_diary,
                include_usage=False,
            )
            self.assertFalse(result["dryRun"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["deletedRows"], 0)
            self.assertEqual(result["documents"], 2)
            self.assertEqual(
                result["pageProjection"],
                f"{DIARY_PERIOD_PAGE_PROJECTION}:2026-06-05:2026-06-05",
            )
            self.assertEqual(
                result["summaryProjection"],
                f"{DIARY_PERIOD_SUMMARY_PROJECTION}:2026-06-05:2026-06-05",
            )

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    """
                    SELECT document_key, relative_path, source_run_id, status
                    FROM diary_markdown_documents
                    WHERE business_date = '2026-06-05'
                    ORDER BY relative_path
                    """
                ).fetchall()
                stale_sections = connection.execute(
                    "SELECT COUNT(*) FROM diary_markdown_sections WHERE document_key = ?",
                    (next(row["document_key"] for row in rows if row["status"] == "stale"),),
                ).fetchone()[0]
            self.assertEqual([row["relative_path"] for row in rows], [
                "diary-2026-06-05/技术进展-260605.md",
                "diary-2026-06-05/日记-260605.md",
                "diary-2026-06-05/智慧沉淀-260605.md",
            ])
            self.assertEqual([row["status"] for row in rows], ["stale", "ready", "ready"])
            self.assertIsNone(rows[0]["source_run_id"])
            self.assertEqual([rows[1]["source_run_id"], rows[2]["source_run_id"]], [result["runId"], result["runId"]])
            self.assertEqual(stale_sections, 1)

            page = read_period_projection(
                paths,
                date(2026, 6, 5),
                date(2026, 6, 5),
                projection_type=DIARY_PERIOD_PAGE_PROJECTION,
            )
            summary = read_period_projection(
                paths,
                date(2026, 6, 5),
                date(2026, 6, 5),
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )
            self.assertEqual(page["sourceRunId"], result["runId"])
            self.assertEqual(summary["sourceRunId"], result["runId"])
            self.assertIn("Reconcile landed", summary["metrics"]["summary"]["lead"])
            jobs = recent_diary_projection_rebuild_jobs(paths, limit=5)
            self.assertEqual(jobs[0]["id"], result["runId"])
            self.assertEqual(jobs[0]["trigger_type"], "dashboard-diary-projection-rebuild")
            self.assertEqual(jobs[0]["metadata"]["startDate"], "2026-06-05")
            self.assertEqual(jobs[0]["metadata"]["endDate"], "2026-06-05")

    def test_rebuild_repairs_usage_events_for_diary_hourly_tokens_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-06-05"
            day.mkdir(parents=True)
            (day / "日记-260605.md").write_text("# 日记\n\n## 今日概要\n修复 token 热力图\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)

            fake_result = SimpleNamespace(
                run_id=42,
                artifacts_seen=3,
                events_seen=9,
                events_in_window=7,
                errors=0,
            )
            with patch("data_foundation.diary_reconcile.run_shadow_period_ingestion", return_value=fake_result) as ingest:
                plan = plan_diary_projection_rebuild(paths, date(2026, 6, 5), date(2026, 6, 5))
                result = rebuild_diary_projections(paths, date(2026, 6, 5), date(2026, 6, 5))

            self.assertTrue(plan["wouldRepairUsageEvents"])
            ingest.assert_called_once()
            self.assertEqual(result["usageRepair"]["runId"], 42)
            self.assertEqual(result["usageRepair"]["eventsInWindow"], 7)

    def test_consistency_reports_extra_disk_files_as_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-06-05"
            day.mkdir(parents=True)
            (day / "日记-260605.md").write_text("# 日记\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                result = dashboard_settings.diary_path_consistency()

            self.assertEqual(result["status"], "mismatch")
            self.assertEqual(result["diskMarkdownFiles"], 1)
            self.assertEqual(result["readyRows"], 0)
            self.assertEqual(result["diskDateRange"], {"startDate": "2026-06-05", "endDate": "2026-06-05"})
            self.assertEqual(result["mismatchDateRange"], {"startDate": "2026-06-05", "endDate": "2026-06-05"})
            self.assertTrue(result["requiresProjectionRefresh"])


if __name__ == "__main__":
    unittest.main()
