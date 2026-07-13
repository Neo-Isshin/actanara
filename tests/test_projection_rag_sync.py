import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_rag.rag_settings import resolve_rag_settings
from agentic_rag.rag_v2_indexer import build_v2_candidate_index
from agentic_rag.rag_v2_promote import promote_v2_candidate, required_v2_promotion_confirmation
from data_foundation.db import connect, migrate
from data_foundation.diary_markdown import materialize_diary_markdown_day
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.settings import write_settings


class ProjectionRagSyncTests(unittest.TestCase):
    def test_deleted_projection_source_disappears_after_next_candidate_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day_dir = diary_root / "diary-2026" / "diary-2026-06" / "06-20"
            day_dir.mkdir(parents=True)
            (day_dir / "日记-260620.md").write_text(
                "# narrative\n\n## Overview\ncurrent narrative\n",
                encoding="utf-8",
            )
            technical = day_dir / "技术进展-260620.md"
            technical.write_text(
                "# technical\n\n## Progress\nDELETED-TECH-MARKER\n",
                encoding="utf-8",
            )
            (day_dir / "智慧沉淀-260620.md").write_text(
                "# learning\n\n## Lesson\ncurrent lesson\n",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "NovaDiary", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings(
                {
                    "pipeline": {"languageProfile": "zh"},
                    "rag": {
                        "enabled": True,
                        "mode": "v2",
                        "languageProfile": "zh",
                        "embedding": {"model": "all-MiniLM-L6-v2", "dimension": 2},
                        "indexing": {"enabled": True},
                    },
                },
                paths,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 6, 20), source_run_id=None)
            settings = resolve_rag_settings(paths)

            first = build_v2_candidate_index(
                settings,
                requested_by="projection-rag-test",
                source_sets=["diary-markdown-sections"],
                embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts],
            )
            first_run_id = first["run"]["runId"]
            first_promotion = promote_v2_candidate(
                settings,
                run_id=first_run_id,
                confirm=True,
                confirmation_text=required_v2_promotion_confirmation(first_run_id),
                requested_by="projection-rag-test",
            )
            first_text = Path(first_promotion["activeIndexPath"]).read_text(encoding="utf-8")

            technical.unlink()
            materialize_diary_markdown_day(paths, date(2026, 6, 20), source_run_id=None)
            second = build_v2_candidate_index(
                settings,
                requested_by="projection-rag-test",
                source_sets=["diary-markdown-sections"],
                embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts],
            )
            second_run_id = second["run"]["runId"]
            second_sources = [
                json.loads(line)
                for line in Path(second["sourcesPath"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            second_promotion = promote_v2_candidate(
                settings,
                run_id=second_run_id,
                confirm=True,
                confirmation_text=required_v2_promotion_confirmation(second_run_id),
                requested_by="projection-rag-test",
            )
            second_text = Path(second_promotion["activeIndexPath"]).read_text(encoding="utf-8")
            root_manifest = json.loads((settings.v2_store_path / "manifest.json").read_text(encoding="utf-8"))
            with connect(paths, read_only=True) as connection:
                technical_status = connection.execute(
                    "SELECT status FROM diary_markdown_documents WHERE report_type = 'technical'",
                ).fetchone()[0]

        self.assertIn("DELETED-TECH-MARKER", first_text)
        self.assertNotIn("DELETED-TECH-MARKER", second_text)
        self.assertNotIn("技术进展-260620.md", {Path(source["path"]).name for source in second_sources})
        self.assertEqual(technical_status, "stale")
        self.assertEqual(root_manifest["activeRunId"], second_run_id)
        self.assertEqual(Path(root_manifest["activeIndexPath"]), Path(second_promotion["activeIndexPath"]))


if __name__ == "__main__":
    unittest.main()
