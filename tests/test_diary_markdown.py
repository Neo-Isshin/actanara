import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_foundation.db import connect, migrate
from data_foundation.diary_markdown import (
    DIARY_PERIOD_PAGE_PROJECTION,
    _DIARY_MARKDOWN_STALE_REASON,
    _period_lessons,
    _period_summary_topics,
    materialize_diary_markdown_day,
    materialize_diary_markdown_period_documents,
    materialize_diary_period_page_snapshot,
    parse_diary_markdown,
    read_diary_markdown_document,
    read_diary_markdown_documents,
)
from data_foundation.diary_paths import (
    diary_day_dir,
    diary_markdown_paths,
    diary_report_type_for_filename,
    period_report_path,
    plan_diary_layout_migration,
)
from data_foundation.period_summary import (
    DIARY_PERIOD_SUMMARY_PROJECTION,
    build_period_summary_payload,
    generate_period_summary_markdown,
    materialize_period_summary_snapshot,
)
from data_foundation.paths import initialize_home, update_runtime_manifest_paths
from data_foundation.jobs import begin_ingestion_run, ingestion_run_status
from data_foundation.reports import LEGACY_ASSET_PROJECTION, read_period_projection, write_period_projection
from data_foundation.settings import write_settings


class DiaryMarkdownIngestionTests(unittest.TestCase):
    def test_diary_markdown_paths_prefers_no_activity_narrative_over_standard_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = diary_day_dir(root, date(2026, 6, 20))
            day.mkdir(parents=True)
            standard = day / "日记-260620.md"
            no_activity = day / "日记-260620-no-activity.md"
            technical = day / "技术进展-260620.md"
            standard.write_text("standard", encoding="utf-8")
            no_activity.write_text("empty", encoding="utf-8")
            technical.write_text("technical", encoding="utf-8")

            paths = diary_markdown_paths(root, date(2026, 6, 20))

        self.assertIn(no_activity, paths)
        self.assertIn(technical, paths)
        self.assertNotIn(standard, paths)

    def test_english_report_filename_contract_is_strict(self):
        self.assertEqual(diary_report_type_for_filename("diary-260620.md", language_profile="en"), "narrative")
        self.assertEqual(diary_report_type_for_filename("technical-260620.md", language_profile="en"), "technical")
        self.assertEqual(diary_report_type_for_filename("learning-260620.md", language_profile="en"), "learning")
        self.assertEqual(diary_report_type_for_filename("narrative-260620.md", language_profile="en"), "unknown")
        self.assertEqual(diary_report_type_for_filename("technical-progress-260620.md", language_profile="en"), "unknown")
        self.assertEqual(diary_report_type_for_filename("learning-audit-260620.md", language_profile="en"), "unknown")

    def test_parser_preserves_sections_and_extracts_trailing_embedded_json(self):
        parsed = parse_diary_markdown(
            """# 2026年05月19日 日记

## 今日概要
正文

### 子项
- 细节

```json
{"date": "2026-05-19", "metrics": {"total": 1}}
```
"""
        )
        self.assertEqual(parsed.title, "2026年05月19日 日记")
        self.assertEqual(parsed.embedded_json, {"date": "2026-05-19", "metrics": {"total": 1}})
        self.assertEqual([section.heading for section in parsed.sections], ["今日概要", "子项"])
        self.assertEqual(parsed.sections[1].heading_path, ("今日概要", "子项"))
        self.assertNotIn("```json", parsed.sections[-1].body_markdown)

    def test_parser_preserves_english_diary_heading_structure(self):
        parsed = parse_diary_markdown(
            """# 2026-05-19 Diary

## Daily Overview
* **Runtime language gate**: English profile stayed isolated.
  - Chinese production pipeline remained unchanged.

## Agent Work
### codex
**[Implementation 10:18-11:05] - English prompt payloads**
- Added isolated English Narrative, Technical, and Learning payloads.

## Important Notices
None

## Scheduled Jobs
None

## Notes
No generated artifacts were written.
"""
        )

        self.assertEqual(parsed.title, "2026-05-19 Diary")
        self.assertEqual(
            [(section.heading_level, section.heading) for section in parsed.sections],
            [
                (2, "Daily Overview"),
                (2, "Agent Work"),
                (3, "codex"),
                (2, "Important Notices"),
                (2, "Scheduled Jobs"),
                (2, "Notes"),
            ],
        )
        self.assertEqual(parsed.sections[2].heading_path, ("Agent Work", "codex"))
        self.assertIn("* **Runtime language gate**", parsed.sections[0].body_markdown)
        self.assertIn("- Added isolated English", parsed.sections[2].body_markdown)

    def test_period_semantic_extractors_read_english_headings(self):
        narrative = parse_diary_markdown(
            """# 2026-05-19 Diary

## Daily Overview
* **Runtime language gate**: English profile stayed isolated.
  - Chinese production pipeline remained unchanged.
"""
        )
        learning = parse_diary_markdown(
            """# 2026-05-19 Learning and Infrastructure Audit

## Lessons
### [codex] Partial YAML drift
#### Problem
Partial packets emitted YAML.
#### Root Cause
The prompt did not forbid intermediate YAML.
#### Recommendation
Only final integration should emit Nova-Task YAML.

## Infrastructure Updates
None
"""
        )
        narrative_document = {
            "document_key": "fixture:narrative",
            "business_date": "2026-05-19",
            "sections": [
                {
                    "headingLevel": section.heading_level,
                    "heading": section.heading,
                    "headingPath": section.heading_path,
                    "bodyMarkdown": section.body_markdown,
                }
                for section in narrative.sections
            ],
        }
        learning_document = {
            "document_key": "fixture:learning",
            "business_date": "2026-05-19",
            "sections": [
                {
                    "headingLevel": section.heading_level,
                    "heading": section.heading,
                    "headingPath": section.heading_path,
                    "bodyMarkdown": section.body_markdown,
                }
                for section in learning.sections
            ],
        }

        topics = _period_summary_topics(narrative_document)
        lessons = _period_lessons(learning_document)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["title"], "Runtime language gate: English profile stayed isolated.")
        self.assertEqual(topics[0]["items"], ["Chinese production pipeline remained unchanged."])
        self.assertEqual(
            lessons,
            [
                {
                    "date": "2026-05-19",
                    "agent": "codex",
                    "problem": "Partial packets emitted YAML.",
                    "rootCause": "The prompt did not forbid intermediate YAML.",
                    "suggestion": "Only final integration should emit Nova-Task YAML.",
                    "sourceDocumentKey": "fixture:learning",
                }
            ],
        )

    def test_period_summary_topics_read_english_dash_bold_overview(self):
        narrative = parse_diary_markdown(
            """## Daily Overview

- **[Model Upgrade Strategy]**: Upgraded the default agent model.
  - Updated `agents.defaults.model.primary`.
  - The change applies only to newly opened sessions.

- **[Monthly Report Generation]**: Produced the May 2026 monthly report.
  - Report captured 358.5M tokens.
"""
        )
        document = {
            "document_key": "fixture:english-dash-overview",
            "business_date": "2026-06-01",
            "sections": [
                {
                    "headingLevel": section.heading_level,
                    "heading": section.heading,
                    "headingPath": section.heading_path,
                    "bodyMarkdown": section.body_markdown,
                }
                for section in narrative.sections
            ],
        }

        topics = _period_summary_topics(document)

        self.assertEqual(len(topics), 2)
        self.assertEqual(topics[0]["title"], "Model Upgrade Strategy: Upgraded the default agent model.")
        self.assertEqual(
            topics[0]["items"],
            ["Updated `agents.defaults.model.primary`.", "The change applies only to newly opened sessions."],
        )
        self.assertEqual(topics[1]["title"], "Monthly Report Generation: Produced the May 2026 monthly report.")

    def test_materialize_day_writes_documents_and_replaces_sections_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            narrative.write_text(
                """# 2026年05月19日 日记

## 今日概要
第一版

```json
{"date": "2026-05-19"}
```
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)

            first = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            document = read_diary_markdown_document(paths, first["documentKeys"][0])
            self.assertEqual(first["documents"], 1)
            self.assertEqual(document["report_type"], "narrative")
            self.assertEqual(document["embeddedJson"], {"date": "2026-05-19"})
            self.assertEqual(document["sections"][0]["bodyMarkdown"], "第一版")

            narrative.write_text(
                """# 2026年05月19日 日记

## 今日概要
第二版

## 备注
已更新
""",
                encoding="utf-8",
            )
            second = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            updated = read_diary_markdown_document(paths, second["documentKeys"][0])
            self.assertEqual(first["documentKeys"], second["documentKeys"])
            self.assertEqual([section["heading"] for section in updated["sections"]], ["今日概要", "备注"])
            self.assertEqual(updated["sections"][0]["bodyMarkdown"], "第二版")
            self.assertIsNone(updated["embeddedJson"])

    def test_materialize_day_stales_deleted_document_and_reactivates_restored_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            technical = day / "技术进展-260519.md"
            narrative.write_text("# 日记\n\n## 今日概要\nready\n", encoding="utf-8")
            technical.write_text("# 技术\n\n## 进展\nfirst\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            first_run = begin_ingestion_run(paths, trigger_type="test-projection", business_date=date(2026, 5, 19))
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=first_run)

            technical.unlink()
            stale_run = begin_ingestion_run(paths, trigger_type="test-projection", business_date=date(2026, 5, 19))
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=stale_run)

            with connect(paths, read_only=True) as connection:
                raw_rows = connection.execute(
                    """
                    SELECT document_key, report_type, status, source_run_id
                    FROM diary_markdown_documents
                    WHERE business_date = '2026-05-19'
                    ORDER BY report_type
                    """
                ).fetchall()
                technical_key = next(row["document_key"] for row in raw_rows if row["report_type"] == "technical")
                technical_sections = connection.execute(
                    "SELECT COUNT(*) FROM diary_markdown_sections WHERE document_key = ?",
                    (technical_key,),
                ).fetchone()[0]
            active = read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19))
            stale_metadata = ingestion_run_status(paths, stale_run)["metadata"]["diaryMarkdownReconciliation"]

            self.assertEqual([(row["report_type"], row["status"]) for row in raw_rows], [("narrative", "ready"), ("technical", "stale")])
            self.assertEqual(next(row["source_run_id"] for row in raw_rows if row["report_type"] == "technical"), first_run)
            self.assertEqual(technical_sections, 1)
            self.assertEqual([document["report_type"] for document in active], ["narrative"])
            self.assertEqual(stale_metadata["staleReason"], _DIARY_MARKDOWN_STALE_REASON)
            self.assertEqual(stale_metadata["staledDocuments"], 1)

            technical.write_text("# 技术\n\n## 进展\nrestored\n", encoding="utf-8")
            restore_run = begin_ingestion_run(paths, trigger_type="test-projection", business_date=date(2026, 5, 19))
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=restore_run)
            restored = read_diary_markdown_document(paths, technical_key)
            with connect(paths, read_only=True) as connection:
                restored_row = connection.execute(
                    "SELECT status, source_run_id FROM diary_markdown_documents WHERE document_key = ?",
                    (technical_key,),
                ).fetchone()

            self.assertEqual(restored_row["status"], "ready")
            self.assertEqual(restored_row["source_run_id"], restore_run)
            self.assertEqual(restored["sections"][0]["bodyMarkdown"], "restored")
            self.assertEqual(
                ingestion_run_status(paths, restore_run)["metadata"]["diaryMarkdownReconciliation"]["reactivatedDocuments"],
                1,
            )

    def test_materialize_day_rename_stales_old_document_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            old_day = diary_root / "diary-2026-05-19"
            old_day.mkdir(parents=True)
            source = old_day / "技术进展-260519.md"
            source.write_text("# 技术\n\n## 进展\nmove me\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            first = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            new_day = diary_day_dir(diary_root, date(2026, 5, 19))
            new_day.mkdir(parents=True)
            source.rename(new_day / source.name)
            second = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)

            with connect(paths, read_only=True) as connection:
                rows = connection.execute(
                    "SELECT document_key, relative_path, status FROM diary_markdown_documents ORDER BY relative_path"
                ).fetchall()

            self.assertNotEqual(first["documentKeys"], second["documentKeys"])
            self.assertEqual(
                [(row["relative_path"], row["status"]) for row in rows],
                [
                    ("diary-2026-05-19/技术进展-260519.md", "stale"),
                    ("diary-2026/diary-2026-05/05-19/技术进展-260519.md", "ready"),
                ],
            )

    def test_materialize_day_rejects_duplicate_logical_source_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            preferred = diary_day_dir(diary_root, date(2026, 5, 19))
            preferred.mkdir(parents=True)
            narrative = preferred / "日记-260519.md"
            narrative.write_text("# preferred\n\n## 今日概要\nfirst\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                before = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, status FROM diary_markdown_documents ORDER BY document_key"
                )]

            compact = diary_root / "2026" / "05" / "19"
            compact.mkdir(parents=True)
            (compact / "日记-260519.md").write_text("# compact\n\n## 今日概要\nconflict\n", encoding="utf-8")
            narrative.write_text("# preferred\n\n## 今日概要\nchanged but uncommitted\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "duplicate diary Markdown sources"):
                materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                after = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, status FROM diary_markdown_documents ORDER BY document_key"
                )]

            self.assertEqual(after, before)

    def test_materialize_day_parse_failure_preserves_entire_day_preimage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            technical = day / "技术进展-260519.md"
            learning = day / "智慧沉淀-260519.md"
            narrative.write_text("# 日记\n\n## 今日概要\nfirst\n", encoding="utf-8")
            technical.write_text("# 技术\n\n## 进展\nfirst\n", encoding="utf-8")
            learning.write_text("# 学习\n\n## 教训\nfirst\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                before_documents = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents ORDER BY document_key"
                )]
                before_sections = [tuple(row) for row in connection.execute(
                    "SELECT document_key, ordinal, body_markdown FROM diary_markdown_sections ORDER BY document_key, ordinal"
                )]

            narrative.write_text("# 日记\n\n## 今日概要\nchanged before failure\n", encoding="utf-8")
            technical.write_bytes(b"\xff\xfeinvalid-utf8")
            learning.unlink()

            with self.assertRaises(UnicodeDecodeError):
                materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                after_documents = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents ORDER BY document_key"
                )]
                after_sections = [tuple(row) for row in connection.execute(
                    "SELECT document_key, ordinal, body_markdown FROM diary_markdown_sections ORDER BY document_key, ordinal"
                )]

            self.assertEqual(after_documents, before_documents)
            self.assertEqual(after_sections, before_sections)

    def test_materialize_day_scan_failure_preserves_preimage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text("# 日记\n\n## 今日概要\nready\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                before = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents"
                )]

            diary_root.rename(root / "Diary-unavailable")
            with self.assertRaisesRegex(FileNotFoundError, "generated diary root is unavailable"):
                materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                after = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents"
                )]

            self.assertEqual(after, before)

    def test_materialize_day_stat_change_preserves_preimage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            narrative.write_text("# 日记\n\n## 今日概要\nfirst\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                before = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents"
                )]

            original_read_text = Path.read_text

            def mutate_after_read(path, *args, **kwargs):
                content = original_read_text(path, *args, **kwargs)
                if path == narrative:
                    path.write_text(content + "changed-during-read\n", encoding="utf-8")
                return content

            with patch.object(Path, "read_text", mutate_after_read):
                with self.assertRaisesRegex(RuntimeError, "changed during inventory"):
                    materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                after = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, parsed_at, status FROM diary_markdown_documents"
                )]

            self.assertEqual(after, before)

    def test_materialize_day_empty_authoritative_inventory_stales_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            narrative.write_text("# 日记\n\n## 今日概要\nready\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            first = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            narrative.unlink()

            empty = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                row = connection.execute(
                    "SELECT document_key, status FROM diary_markdown_documents WHERE document_key = ?",
                    (first["documentKeys"][0],),
                ).fetchone()
                sections = connection.execute(
                    "SELECT COUNT(*) FROM diary_markdown_sections WHERE document_key = ?",
                    (first["documentKeys"][0],),
                ).fetchone()[0]

            self.assertEqual(empty, {"businessDate": "2026-05-19", "documents": 0, "documentKeys": []})
            self.assertEqual((row["document_key"], row["status"]), (first["documentKeys"][0], "stale"))
            self.assertEqual(sections, 1)
            self.assertEqual(read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19)), [])

    def test_materialize_day_database_failure_rolls_back_upsert_and_stale_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            narrative = day / "日记-260519.md"
            technical = day / "技术进展-260519.md"
            narrative.write_text("# 日记\n\n## 今日概要\nfirst\n", encoding="utf-8")
            technical.write_text("# 技术\n\n## 进展\nfirst\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                before_documents = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, status FROM diary_markdown_documents ORDER BY document_key"
                )]
                before_sections = [tuple(row) for row in connection.execute(
                    "SELECT document_key, ordinal, body_markdown FROM diary_markdown_sections ORDER BY document_key, ordinal"
                )]
            with connect(paths) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER fail_diary_stale
                    BEFORE UPDATE OF status ON diary_markdown_documents
                    WHEN NEW.status = 'stale'
                    BEGIN
                        SELECT RAISE(ABORT, 'injected stale failure');
                    END
                    """
                )
            narrative.write_text("# 日记\n\n## 今日概要\nchanged\n", encoding="utf-8")
            technical.unlink()

            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected stale failure"):
                materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            with connect(paths, read_only=True) as connection:
                after_documents = [tuple(row) for row in connection.execute(
                    "SELECT document_key, content_sha256, status FROM diary_markdown_documents ORDER BY document_key"
                )]
                after_sections = [tuple(row) for row in connection.execute(
                    "SELECT document_key, ordinal, body_markdown FROM diary_markdown_sections ORDER BY document_key, ordinal"
                )]

            self.assertEqual(after_documents, before_documents)
            self.assertEqual(after_sections, before_sections)

    def test_materialize_day_language_switch_exposes_only_active_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            for filename, title in (
                ("日记-260519.md", "中文日记"),
                ("技术进展-260519.md", "中文技术"),
                ("智慧沉淀-260519.md", "中文学习"),
                ("diary-260519.md", "English Diary"),
                ("technical-260519.md", "English Technical"),
                ("learning-260519.md", "English Learning"),
            ):
                (day / filename).write_text(f"# {title}\n\n## Body\nready\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            migrate(paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            english = read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19))

            write_settings({"pipeline": {"languageProfile": "zh", "englishEnabled": False}}, paths)
            before_chinese_materialization = read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19))
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            chinese = read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19))
            with connect(paths, read_only=True) as connection:
                raw = connection.execute(
                    "SELECT relative_path, status FROM diary_markdown_documents ORDER BY relative_path"
                ).fetchall()

            self.assertEqual(sorted(document["title"] for document in english), ["English Diary", "English Learning", "English Technical"])
            self.assertEqual(before_chinese_materialization, [])
            self.assertEqual(sorted(document["title"] for document in chinese), ["中文学习", "中文技术", "中文日记"])
            statuses = {Path(row["relative_path"]).name: row["status"] for row in raw}
            self.assertEqual({statuses[name] for name in ("diary-260519.md", "technical-260519.md", "learning-260519.md")}, {"stale"})
            self.assertEqual({statuses[name] for name in ("日记-260519.md", "技术进展-260519.md", "智慧沉淀-260519.md")}, {"ready"})

            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            english_restored = read_diary_markdown_documents(paths, date(2026, 5, 19), date(2026, 5, 19))
            with connect(paths, read_only=True) as connection:
                restored_raw = connection.execute(
                    "SELECT relative_path, status FROM diary_markdown_documents ORDER BY relative_path"
                ).fetchall()
            restored_statuses = {Path(row["relative_path"]).name: row["status"] for row in restored_raw}

            self.assertEqual(sorted(document["title"] for document in english_restored), ["English Diary", "English Learning", "English Technical"])
            self.assertEqual({restored_statuses[name] for name in ("diary-260519.md", "technical-260519.md", "learning-260519.md")}, {"ready"})
            self.assertEqual({restored_statuses[name] for name in ("日记-260519.md", "技术进展-260519.md", "智慧沉淀-260519.md")}, {"stale"})

    def test_materialize_day_recognizes_english_report_filename_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "diary-260519.md").write_text("# 2026-05-19 Diary\n\n## Daily Overview\nsummary\n", encoding="utf-8")
            (day / "technical-260519.md").write_text(
                "# 2026-05-19 Technical Progress Report\n\n## Engineering Objectives and Outcomes\nready\n",
                encoding="utf-8",
            )
            (day / "learning-260519.md").write_text(
                "# 2026-05-19 Learning and Infrastructure Audit\n\n## Lessons\nNone\n",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            migrate(paths)

            result = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            documents = [read_diary_markdown_document(paths, key) for key in result["documentKeys"]]

        by_type = {document["report_type"]: document for document in documents}
        self.assertEqual(sorted(by_type), ["learning", "narrative", "technical"])
        self.assertEqual(by_type["narrative"]["sections"][0]["heading"], "Daily Overview")
        self.assertEqual(by_type["technical"]["sections"][0]["heading"], "Engineering Objectives and Outcomes")
        self.assertEqual(by_type["learning"]["sections"][0]["heading"], "Lessons")

    def test_materialize_day_reads_year_month_day_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text("# 2026年05月19日 日记\n\n## 今日概要\n新布局\n", encoding="utf-8")
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)

            result = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            document = read_diary_markdown_document(paths, result["documentKeys"][0])

            self.assertEqual(result["documents"], 1)
            self.assertEqual(document["relative_path"], "diary-2026/diary-2026-05/05-19/日记-260519.md")
            self.assertEqual(document["sections"][0]["bodyMarkdown"], "新布局")

    def test_materialize_day_defaults_to_generated_diary_when_legacy_is_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "generated"
            legacy = root / "legacy"
            generated_day = generated / "diary-2026-05-19"
            legacy_day = legacy / "diary-2026-05-19"
            generated_day.mkdir(parents=True)
            legacy_day.mkdir(parents=True)
            (generated_day / "日记-260519.md").write_text(
                "# 2026年05月19日 日记\n\n## 今日概要\ncurrent generated\n",
                encoding="utf-8",
            )
            (legacy_day / "日记-260519.md").write_text(
                "# 2026年05月19日 日记\n\n## 今日概要\nretired legacy\n",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=legacy).home,
                generated_diary_root=generated,
            )
            migrate(paths)

            result = materialize_diary_markdown_day(paths, date(2026, 5, 19), source_run_id=None)
            document = read_diary_markdown_document(paths, result["documentKeys"][0])

            self.assertEqual(result["documents"], 1)
            self.assertEqual(paths.legacy_diary_root, legacy)
            self.assertEqual(document["sections"][0]["bodyMarkdown"], "current generated")

    def test_diary_layout_migration_plan_is_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            old_day = diary_root / "diary-2026-05-19"
            old_day.mkdir(parents=True)
            source = old_day / "日记-260519.md"
            source.write_text("# old\n", encoding="utf-8")

            plan = plan_diary_layout_migration(diary_root)

            self.assertTrue(plan["dryRun"])
            self.assertEqual(plan["wouldMove"], 1)
            self.assertEqual(plan["conflicts"], 0)
            self.assertEqual(plan["moves"][0]["destination"], str(diary_root / "diary-2026" / "diary-2026-05" / "05-19" / "日记-260519.md"))
            self.assertTrue(source.exists())

    def test_diary_layout_migration_plan_moves_compact_layout_to_named_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            compact_day = diary_root / "2026" / "05" / "19"
            compact_day.mkdir(parents=True)
            source = compact_day / "日记-260519.md"
            source.write_text("# compact\n", encoding="utf-8")

            plan = plan_diary_layout_migration(diary_root)

            self.assertEqual(plan["wouldMove"], 1)
            self.assertEqual(plan["moves"][0]["destination"], str(diary_root / "diary-2026" / "diary-2026-05" / "05-19" / "日记-260519.md"))

    def test_period_report_paths_use_month_directory_and_readable_names(self):
        root = Path("/Diary")

        self.assertEqual(
            period_report_path(root, date(2026, 5, 13), date(2026, 5, 19), label="周报"),
            root / "diary-2026" / "diary-2026-05" / "summary-2026-W20-周报.md",
        )
        self.assertEqual(
            period_report_path(root, date(2026, 5, 1), date(2026, 5, 31), label="月报"),
            root / "diary-2026" / "diary-2026-05" / "summary-2026-05-月报.md",
        )
        self.assertEqual(
            period_report_path(root, date(2026, 5, 28), date(2026, 6, 3), label="周报"),
            root / "diary-2026" / "diary-2026-05" / "summary-2026-W22-周报.md",
        )

    def test_materialize_period_page_snapshot_from_structured_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

### TokenClock 稳定版修复完成
- 修复设置窗口崩溃
- 推送 2 个 commit
""",
                encoding="utf-8",
            )
            (day / "智慧沉淀-260519.md").write_text(
                """# 2026-05-19 智慧沉淀与基建审计

## 🧠 黄金教训 (Lessons)
- **【claude-code】**: 设置窗口崩溃修复后缺少回归验证。解决建议：发布前补快速验证清单。
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            start = date(2026, 5, 19)
            end = date(2026, 5, 20)

            materialize_diary_markdown_period_documents(paths, start, end, source_run_id=None)
            report_key = materialize_diary_period_page_snapshot(paths, start, end, source_run_id=None)
            projection = read_period_projection(
                paths,
                start,
                end,
                projection_type=DIARY_PERIOD_PAGE_PROJECTION,
            )

            self.assertEqual(report_key, f"{DIARY_PERIOD_PAGE_PROJECTION}:2026-05-19:2026-05-20")
            self.assertEqual(projection["metrics"]["documentCount"], 2)
            self.assertEqual(projection["metrics"]["sectionCount"], 3)
            self.assertEqual(projection["metrics"]["summaryTopics"][0]["title"], "TokenClock 稳定版修复完成")
            self.assertEqual(projection["metrics"]["summaryTopics"][0]["items"], ["修复设置窗口崩溃", "推送 2 个 commit"])
            self.assertEqual(projection["metrics"]["lessons"][0]["agent"], "claude-code")
            self.assertEqual(len(projection["metrics"]["days"]), 2)

    def test_period_page_preserves_star_summary_items_with_nested_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

* **TokenClock 稳定版修复完成**：Claude Code 完成了 TokenClock 的紧急修复工作。
  - 核心修复：解决设置窗口崩溃问题。
  - 推送记录：2 个 commit 成功同步。
  - 质量提示：建议发布前回归测试。

* **actanara 前端架构解构**：gemini-cli 完成 Dashboard 前端拆分。
  - 提取规模：约 3,500 行代码。
  - 文件结构：新增 CSS 与 JS 文件。
  - 待验证项：确认静态文件服务路由。
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            start = date(2026, 5, 19)
            materialize_diary_markdown_period_documents(paths, start, start, source_run_id=None)
            materialize_diary_period_page_snapshot(paths, start, start, source_run_id=None)
            projection = read_period_projection(
                paths,
                start,
                start,
                projection_type=DIARY_PERIOD_PAGE_PROJECTION,
            )

            topics = projection["metrics"]["summaryTopics"]
            self.assertEqual(len(topics), 2)
            self.assertEqual(
                topics[0]["title"],
                "TokenClock 稳定版修复完成: Claude Code 完成了 TokenClock 的紧急修复工作。",
            )
            self.assertEqual(
                topics[0]["items"],
                ["核心修复：解决设置窗口崩溃问题。", "推送记录：2 个 commit 成功同步。", "质量提示：建议发布前回归测试。"],
            )
            self.assertEqual(topics[1]["items"][0], "提取规模：约 3,500 行代码。")

    def test_materialize_period_summary_snapshot_from_period_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_root / "diary-2026-05-19"
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

### TokenClock 稳定版修复完成
- 修复设置窗口崩溃
- 推送 2 个 commit
""",
                encoding="utf-8",
            )
            (day / "智慧沉淀-260519.md").write_text(
                """# 2026-05-19 智慧沉淀与基建审计

## 🧠 黄金教训 (Lessons)
- **【codex】**: 快照刷新后缺少展示验证。解决建议：补充端到端按钮检查。
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            start = date(2026, 5, 19)
            end = date(2026, 5, 19)

            materialize_diary_markdown_period_documents(paths, start, end, source_run_id=None)
            materialize_diary_period_page_snapshot(paths, start, end, source_run_id=None)
            report_key = materialize_period_summary_snapshot(paths, start, end, source_run_id=None)
            projection = read_period_projection(
                paths,
                start,
                end,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )

            self.assertEqual(report_key, f"{DIARY_PERIOD_SUMMARY_PROJECTION}:2026-05-19:2026-05-19")
            self.assertEqual(projection["metrics"]["sourceProjection"], DIARY_PERIOD_PAGE_PROJECTION)
            self.assertIn("TokenClock 稳定版修复完成", projection["metrics"]["summary"]["lead"])
            self.assertEqual(projection["metrics"]["summary"]["highlights"][0], "TokenClock 稳定版修复完成：修复设置窗口崩溃；推送 2 个 commit")
            self.assertIn("端到端按钮检查", projection["metrics"]["summary"]["lessons"][0])
            self.assertIn("## 本周期总览", projection["metrics"]["summary"]["markdown"])
            self.assertNotIn("## 本周总结", projection["metrics"]["summary"]["markdown"])
            self.assertIn("## 工作强度与深夜投入", projection["metrics"]["summary"]["markdown"])
            self.assertIn("## 关怀与鼓励", projection["metrics"]["summary"]["markdown"])
            self.assertEqual(projection["metrics"]["generation"]["mode"], "deterministic")
            self.assertEqual(
                projection["metrics"]["summary"]["markdownPath"],
                str(diary_root / "diary-2026" / "diary-2026-05" / "summary-2026-W21-周报.md"),
            )
            self.assertTrue((diary_root / "diary-2026" / "diary-2026-05" / "summary-2026-W21-周报.md").exists())

    def test_materialize_period_summary_snapshot_uses_english_content_for_english_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "diary-260519.md").write_text(
                """# 2026-05-19 Diary

## Daily Overview
* **Runtime language gate**: ready
  - English profile stayed isolated.
""",
                encoding="utf-8",
            )
            (day / "learning-260519.md").write_text(
                """# 2026-05-19 Learning and Infrastructure Audit

## Lessons
### [codex] Prompt drift
#### Problem
Prompt payload drift.
#### Recommendation
Keep English prompts isolated.
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(
                initialize_home(root / "Actanara", legacy_diary_root=diary_root).home,
                generated_diary_root=diary_root,
                legacy_diary_root=diary_root,
            )
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            migrate(paths)
            start = date(2026, 5, 19)
            end = date(2026, 5, 19)

            materialize_diary_markdown_period_documents(paths, start, end, source_run_id=None)
            materialize_diary_period_page_snapshot(paths, start, end, source_run_id=None)
            materialize_period_summary_snapshot(paths, start, end, source_run_id=None)
            projection = read_period_projection(
                paths,
                start,
                end,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )

            metrics = projection["metrics"]
            self.assertEqual(metrics["languageProfile"], "en")
            self.assertEqual(metrics["summary"]["title"], "This Period Summary")
            self.assertIn("This Period captured", metrics["summary"]["lead"])
            self.assertIn("Runtime language gate: ready", metrics["summary"]["highlights"][0])
            self.assertIn("recommendation: Keep English prompts isolated.", metrics["summary"]["lessons"][0])
            self.assertIn("## Workload and Late-Hour Focus", metrics["summary"]["markdown"])
            self.assertIn("## Care and Encouragement", metrics["summary"]["markdown"])
            self.assertEqual(
                metrics["summary"]["markdownPath"],
                str(diary_root / "diary-2026" / "diary-2026-05" / "summary-2026-W21-周报.md"),
            )

    def test_period_summary_generator_receives_asset_and_comparison_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

### Runtime path hardening
- Guarded settings writes
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            start = date(2026, 5, 19)
            end = date(2026, 5, 19)
            write_period_projection(
                paths,
                start,
                end,
                {
                    "kpi": {"totalTokens": 1200, "totalMessages": 12},
                    "dailyTokenSeries": [{"date": "2026-05-19", "tokens": 1200, "messages": 12}],
                    "workspaceUsage": [{"name": "actanara", "tokens": 1200, "messages": 12}],
                    "models": [{"name": "MiniMax-M3", "tokens": 1200}],
                    "assetHourlyHeatmap": {"dates": ["2026-05-19"], "periods": [{"hour": 23, "tokens": 300}]},
                    "taskStats": {"completed": 3, "inProgress": 1},
                    "cronStats": {"success": 2, "failed": 0},
                },
                source_run_id=None,
                projection_type=LEGACY_ASSET_PROJECTION,
            )
            write_period_projection(
                paths,
                date(2026, 5, 18),
                date(2026, 5, 18),
                {"kpi": {"totalTokens": 800, "totalMessages": 8}},
                source_run_id=None,
                projection_type=LEGACY_ASSET_PROJECTION,
            )
            materialize_diary_markdown_period_documents(paths, start, end, source_run_id=None)
            materialize_diary_period_page_snapshot(paths, start, end, source_run_id=None)
            contexts = []

            def fake_generator(context):
                contexts.append(context)
                return "## LLM 周报\n\n- actanara 本周投入最高。"

            materialize_period_summary_snapshot(paths, start, end, source_run_id=None, generator=fake_generator)
            projection = read_period_projection(
                paths,
                start,
                end,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )

            self.assertEqual(contexts[0]["kpi"]["totalTokens"], 1200)
            self.assertEqual(contexts[0]["previousKpi"]["totalTokens"], 800)
            self.assertEqual(contexts[0]["currentPeriod"]["workspaceUsage"][0]["name"], "actanara")
            self.assertEqual(contexts[0]["currentPeriod"]["models"][0]["name"], "MiniMax-M3")
            self.assertEqual(contexts[0]["currentPeriod"]["topics"][0]["title"], "Runtime path hardening")
            self.assertEqual(contexts[0]["previousPeriod"]["kpi"]["totalTokens"], 800)
            self.assertEqual(contexts[0]["previousPeriod"]["period"]["startDate"], "2026-05-18")
            self.assertEqual(projection["metrics"]["generation"]["mode"], "llm")
            self.assertEqual(projection["metrics"]["summary"]["lead"], "actanara 本周投入最高。")
            self.assertEqual(projection["metrics"]["summary"]["highFrequencyTopics"], [])
            self.assertEqual(projection["metrics"]["highFrequencyTopics"], [])
            self.assertEqual((diary_root / "diary-2026" / "diary-2026-05" / "summary-2026-W21-周报.md").read_text(encoding="utf-8"), "## LLM 周报\n\n- actanara 本周投入最高。\n")

    def test_materialize_period_summary_snapshot_stores_llm_topics_from_structured_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 19))
            day.mkdir(parents=True)
            (day / "日记-260519.md").write_text(
                """# 2026年05月19日 日记

## 今日概要

### Runtime path hardening
- 固化 runtime source 路径。
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            start = date(2026, 5, 19)
            end = date(2026, 5, 19)
            materialize_diary_markdown_period_documents(paths, start, end, source_run_id=None)
            materialize_diary_period_page_snapshot(paths, start, end, source_run_id=None)

            def fake_generator(context):
                return {
                    "markdown": "## LLM 周报\n\n- runtime hardening completed.",
                    "highFrequencyTopics": [
                        {"topic": "Runtime hardening", "count": 3, "reason": "日记主题和 workspace 投入均指向 runtime"}
                    ],
                }

            materialize_period_summary_snapshot(paths, start, end, source_run_id=None, generator=fake_generator)
            projection = read_period_projection(
                paths,
                start,
                end,
                projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
            )

            expected_topics = [{"topic": "Runtime hardening", "count": 3, "reason": "日记主题和 workspace 投入均指向 runtime"}]
            self.assertEqual(projection["metrics"]["generation"]["mode"], "llm")
            self.assertEqual(projection["metrics"]["summary"]["lead"], "runtime hardening completed.")
            self.assertEqual(projection["metrics"]["summary"]["highFrequencyTopics"], expected_topics)
            self.assertEqual(projection["metrics"]["highFrequencyTopics"], expected_topics)
            self.assertEqual((diary_root / "diary-2026" / "diary-2026-05" / "summary-2026-W21-周报.md").read_text(encoding="utf-8"), "## LLM 周报\n\n- runtime hardening completed.\n")

    def test_month_period_summary_context_uses_previous_calendar_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            diary_root = root / "Diary"
            day = diary_day_dir(diary_root, date(2026, 5, 31))
            day.mkdir(parents=True)
            (day / "日记-260531.md").write_text(
                """# 2026年05月31日 日记

## 今日概要

### Monthly report improvements
- Added comparison context
""",
                encoding="utf-8",
            )
            paths = update_runtime_manifest_paths(initialize_home(root / "Actanara", legacy_diary_root=diary_root).home, generated_diary_root=diary_root, legacy_diary_root=diary_root)
            migrate(paths)
            write_period_projection(
                paths,
                date(2026, 5, 1),
                date(2026, 5, 31),
                {"kpi": {"totalTokens": 3100}, "workspaceUsage": [{"name": "MayProject", "tokens": 3100}]},
                source_run_id=None,
                projection_type=LEGACY_ASSET_PROJECTION,
            )
            write_period_projection(
                paths,
                date(2026, 4, 1),
                date(2026, 4, 30),
                {"kpi": {"totalTokens": 2400}, "workspaceUsage": [{"name": "AprilProject", "tokens": 2400}]},
                source_run_id=None,
                projection_type=LEGACY_ASSET_PROJECTION,
            )
            materialize_diary_markdown_period_documents(paths, date(2026, 5, 1), date(2026, 5, 31), source_run_id=None)
            materialize_diary_period_page_snapshot(paths, date(2026, 5, 1), date(2026, 5, 31), source_run_id=None)

            payload = build_period_summary_payload(paths, date(2026, 5, 1), date(2026, 5, 31))
            context = payload["insightContext"]

            self.assertEqual(context["comparisonPeriod"]["startDate"], "2026-04-01")
            self.assertEqual(context["comparisonPeriod"]["endDate"], "2026-04-30")
            self.assertEqual(context["previousPeriod"]["workspaceUsage"][0]["name"], "AprilProject")
            self.assertEqual(context["currentPeriod"]["workspaceUsage"][0]["name"], "MayProject")

    def test_period_summary_prompt_uses_generic_context_and_fixed_sections(self):
        captured = {}

        def fake_sender(**kwargs):
            captured.update(kwargs)
            return '{"markdown":"## 本周期总览\\n\\n- ok","highFrequencyTopics":[{"topic":"Runtime hardening","count":3,"reason":"evidence"}]}'

        with (
            patch(
                "data_foundation.period_summary.resolve_llm_provider",
                return_value={"apiKey": "secret", "endpoint": "https://example.test", "model": "m", "api": "openai-compatible", "timeoutSeconds": 480},
            ),
            patch("data_foundation.period_summary.send_openai_compatible_message", side_effect=fake_sender),
        ):
            result = generate_period_summary_markdown(
                {
                    "currentPeriod": {"kpi": {"totalTokens": 100}},
                    "previousPeriod": {"kpi": {"totalTokens": 80}},
                }
            )

        self.assertEqual(result, "## 本周期总览\n\n- ok")
        self.assertNotIn("Actanara", captured["system"])
        self.assertNotIn("Actanara", captured["prompt"])
        self.assertIn("currentPeriod 是本周期数据", captured["prompt"])
        self.assertIn("previousPeriod 是上周或上月数据", captured["prompt"])
        self.assertIn("highFrequencyTopics", captured["prompt"])
        self.assertIn("只输出 JSON 对象", captured["prompt"])
        self.assertIn("## 工作强度与深夜投入", captured["prompt"])
        self.assertIn("## 关怀与鼓励", captured["prompt"])
        self.assertIn("名言或格言", captured["prompt"])
        self.assertEqual(captured["timeout"], 480)

    def test_period_summary_prompt_uses_english_contract_for_english_profile(self):
        captured = {}

        def fake_sender(**kwargs):
            captured.update(kwargs)
            return '{"markdown":"## Period Overview\\n\\n- ok","highFrequencyTopics":[{"topic":"Runtime hardening","count":3,"reason":"evidence"}]}'

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
            write_settings({"pipeline": {"languageProfile": "en", "englishEnabled": True}}, paths)
            with (
                patch(
                    "data_foundation.period_summary.resolve_llm_provider",
                    return_value={"apiKey": "secret", "endpoint": "https://example.test", "model": "m", "api": "openai-compatible", "timeoutSeconds": 480},
                ),
                patch("data_foundation.period_summary.send_openai_compatible_message", side_effect=fake_sender),
            ):
                result = generate_period_summary_markdown(
                    {
                        "currentPeriod": {"kpi": {"totalTokens": 100}},
                        "previousPeriod": {"kpi": {"totalTokens": 80}},
                    },
                    paths,
                )

        self.assertEqual(result, "## Period Overview\n\n- ok")
        self.assertIn("Output only a JSON object", captured["prompt"])
        self.assertIn("currentPeriod is the current period data", captured["prompt"])
        self.assertIn("## Period Overview", captured["prompt"])
        self.assertIn("## Care and Encouragement", captured["prompt"])
        self.assertNotIn("请基于下面", captured["prompt"])
        self.assertIn("Output only a valid JSON object", captured["system"])


if __name__ == "__main__":
    unittest.main()
