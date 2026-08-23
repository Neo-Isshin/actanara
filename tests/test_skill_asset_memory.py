from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentic_rag.rag_settings import resolve_rag_settings
from agentic_rag.rag_v2_indexer import _collect_skill_assets, collect_candidate_chunks
from data_foundation.daily_completeness import evaluate_daily_completeness
from data_foundation.environment_assets import write_environment_asset_ledger
from data_foundation.paths import initialize_home
from data_foundation.skill_asset_memory import (
    collect_skill_asset_memory_records,
    skill_asset_ledger_ready,
)


class SkillAssetMemoryTests(unittest.TestCase):
    def _row(self, asset_class: str, review_id: str, *, prompt: str = "minimal-v21") -> dict:
        return {
            "schema": "actanara.skill-asset-ledger.v1",
            "businessDate": "2026-08-12",
            "promptVersion": prompt,
            "assetClass": asset_class,
            "disposition": "adjudicated",
            "candidateId": "candidate-001",
            "reviewId": review_id,
            "originalDecision": asset_class,
            "title": f"{asset_class} title",
            "summary": "A reusable observation",
            "reason": "token=synthetic-secret must not enter retrieval",
            "evidence": ["thread-0001:000001"],
            "evidenceRecords": [1],
            "scores": {"evidence": 3, "value": 3, "reuse": 3, "program": 2},
            "completion": None,
            "completionReason": None,
            "portfolioReason": None,
            "libraryAction": None,
            "existingAssetId": None,
            "existingSkillName": None,
            "skillName": None,
            "skillDescription": None,
            "skillMarkdown": None,
        }

    def _write(self, root: Path, version: int, rows: list[dict]) -> Path:
        path = root / f"skill-harness-minimal-v{version}-assets-2026-08-12.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def test_latest_ledger_projects_three_retrieval_tiers_and_excludes_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            root = paths.home / "artifacts" / "skills"
            self._write(root, 20, [self._row("lesson", "review-001", prompt="minimal-v20")])
            latest = self._write(
                root,
                21,
                [
                    self._row("skill", "review-001"),
                    self._row("lesson", "review-002"),
                    self._row("reference", "review-003"),
                    self._row("discard", "review-004"),
                ],
            )

            records, sources = collect_skill_asset_memory_records(paths)

            self.assertEqual([item.asset_class for item in records], ["lesson", "reference", "discard"])
            self.assertEqual(sources, (latest,))
            self.assertTrue(skill_asset_ledger_ready(paths, "2026-08-12"))
            self.assertNotIn("synthetic-secret", "\n".join(item.text for item in records))
            self.assertIn("token=[redacted]", records[0].text)

            chunks, source_rows = _collect_skill_assets(SimpleNamespace(runtime_home=paths.home))
            self.assertEqual(
                [item["sourceSet"] for item in chunks],
                ["skill-lessons", "skill-references", "skill-discards"],
            )
            self.assertEqual([item["governance"]["authorityRank"] for item in chunks], [82, 68, 48])
            self.assertTrue(all(item["privacyClass"] == "local-private" for item in chunks))
            self.assertEqual(sum(item["chunkCount"] for item in source_rows), 3)

            all_chunks, all_sources = collect_candidate_chunks(
                resolve_rag_settings(paths),
                ["lessons"],
            )
            self.assertEqual(
                [
                    item["sourceSet"]
                    for item in all_chunks
                    if str(item["sourceSet"]).startswith("skill-")
                ],
                ["skill-lessons", "skill-references", "skill-discards"],
            )
            self.assertEqual(
                {
                    item["sourceSet"]
                    for item in all_sources
                    if str(item["sourceSet"]).startswith("skill-")
                },
                {"skill-lessons", "skill-references", "skill-discards"},
            )

    def test_unsafe_or_malformed_latest_ledger_is_not_ready_or_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            root = paths.home / "artifacts" / "skills"
            path = self._write(root, 21, [self._row("lesson", "review-001")])
            os.link(path, root / "hardlink.jsonl")

            records, sources = collect_skill_asset_memory_records(paths)

            self.assertEqual(records, ())
            self.assertEqual(sources, ())
            self.assertFalse(skill_asset_ledger_ready(paths, "2026-08-12"))

    def test_empty_safe_ledger_proves_skill_pass_completed_without_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara", legacy_diary_root=Path(tmp) / "Diary")
            documents = [
                {"report_type": "narrative", "relative_path": "narrative.md"},
                {"report_type": "technical", "relative_path": "technical.md"},
            ]
            with (
                patch("data_foundation.daily_completeness._foundation_materialized", return_value=True),
                patch("data_foundation.daily_completeness._rag_required", return_value=False),
                patch("data_foundation.daily_completeness._nova_task_enabled", return_value=False),
                patch("data_foundation.daily_completeness._nova_task_ready", return_value=False),
            ):
                missing = evaluate_daily_completeness(
                    paths,
                    date(2026, 8, 12),
                    documents=documents,
                )
                self._write(paths.home / "artifacts" / "skills", 21, [])
                write_environment_asset_ledger(
                    paths,
                    business_date="2026-08-12",
                    assets=[],
                )
                complete = evaluate_daily_completeness(
                    paths,
                    date(2026, 8, 12),
                    documents=documents,
                )

            self.assertIn("diary-skill", missing["missingKeys"])
            self.assertIn("diary-environment", missing["missingKeys"])
            self.assertEqual(missing["llmCalls"], 6)
            self.assertNotIn("diary-skill", complete["missingKeys"])
            self.assertNotIn("diary-environment", complete["missingKeys"])
            self.assertTrue(complete["documentsReady"]["skill"])
            self.assertTrue(complete["documentsReady"]["environment"])
            self.assertIn("diary-skill", complete["existingItems"])
            self.assertIn("diary-environment", complete["existingItems"])


if __name__ == "__main__":
    unittest.main()
