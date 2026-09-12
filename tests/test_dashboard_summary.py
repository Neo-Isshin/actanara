import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import dashboard_summary as summary_router
from app.services import dashboard_summary as summary
from data_foundation.db import connect, migrate
from data_foundation.paths import initialize_home, runtime_paths_for_home


class DashboardSummaryTests(unittest.TestCase):
    @staticmethod
    def runtime(home):
        paths = initialize_home(home)
        migrate(paths)
        return paths

    @staticmethod
    def ledger(home, *, day="2026-09-07", version=22, classes=("lesson",), title="Reusable recovery"):
        root = home / "artifacts" / "skills"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        rows = []
        for number, kind in enumerate(classes, 1):
            row = {
                "schema": "actanara.skill-asset-ledger.v1", "businessDate": day,
                "promptVersion": f"minimal-v{version}", "assetClass": kind,
                "originalDecision": kind, "candidateId": f"candidate-{number:03}",
                "reviewId": f"review-{number:03}", "disposition": "adjudicated",
                "title": title, "summary": "Useful summary /Users/operator/private token=hidden",
                "evidence": ["private-locator"], "evidenceRecords": [1],
                "scores": {"evidence": 5, "value": 4, "reuse": 4, "program": 5},
            }
            if kind == "skill":
                row.update(libraryAction="create", skillName="recovery", skillDescription="Recovery",
                           skillMarkdown="---\nname: recovery\ndescription: Recovery\n---\nPrivate procedure")
            rows.append(row)
        target = root / f"skill-harness-minimal-v{version}-assets-{day}.jsonl"
        target.write_text("".join(json.dumps(row) + "\n" for row in rows))
        target.chmod(0o600)
        return target

    @staticmethod
    def seed_sql(paths):
        with connect(paths) as connection:
            for number, (kind, status) in enumerate((
                ("task", "completed"), ("task", "done"), ("task", "settled"),
                ("task", "active"), ("task", "archived"), ("subtask", "done"),
                ("workstream", "settled"),
            )):
                connection.execute(
                    "INSERT INTO nova_task_nodes(node_id, node_type, title, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"node-{number}", kind, f"Restore /Users/operator/private-{number} token=hidden",
                     status, "2026-09-01", "2026-09-07"),
                )
            connection.execute(
                "INSERT INTO nova_task_candidates(candidate_id, candidate_type, proposed_title, status, "
                "confidence, reason, source_fingerprint, created_at, updated_at) "
                "VALUES ('candidate', 'parent_task', 'Review this', 'pending_review', 'high', '', 'unique', '2026-09-07', '2026-09-07')"
            )
            for number, (kind, status) in enumerate((
                ("narrative", "ready"), ("technical", "ready"), ("learning", "ready"),
                ("narrative", "stale"), ("token-metric", "ready"),
            )):
                connection.execute(
                    "INSERT INTO diary_markdown_documents(document_key, business_date, report_type, "
                    "relative_path, title, content_sha256, byte_size, parsed_at, status) "
                    "VALUES (?, '2026-09-07', ?, ?, ?, 'sha', 10, '2026-09-07', ?)",
                    (f"/Users/operator/private-key-{number}", kind, f"file-{number}.md",
                     f"Report /Users/operator/private-{number} api_key=hidden", status),
                )

    def test_counts_use_distinct_authorities_and_hide_private_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.seed_sql(paths)
            self.ledger(paths.home, version=21, classes=("lesson", "lesson", "lesson"))
            self.ledger(paths.home, classes=("lesson", "skill", "reference", "discard"),
                        title="Learning /Users/operator/secret token=hidden")
            self.ledger(paths.home, day="2026-09-06", classes=("lesson",))
            canonical = paths.home / "assets" / "skills" / "recovery"
            canonical.mkdir(parents=True)
            canonical.joinpath("SKILL.md").write_text("---\nname: recovery\ndescription: Safe recovery\n---\nRecovery procedure")
            result = summary.get_summary(paths=paths)

        self.assertEqual(result["status"], "ready")
        self.assertEqual({key: value["count"] for key, value in result["assets"].items()},
                         {"completedTasks": 3, "lessons": 2, "learningNotes": 0, "skills": 1, "reports": 3})
        self.assertEqual(result["review"]["taskCandidates"]["count"], 1)
        self.assertEqual(result["review"]["skillDrafts"]["count"], 1)
        self.assertNotIn("assetCount", result)
        self.assertEqual(result["assets"]["lessons"]["scope"]["includedDays"], 2)
        self.assertEqual(result["assets"]["reports"]["byType"], {"narrative": 1, "technical": 1, "learning": 1})
        public = json.dumps(result)
        for forbidden in ("/Users/operator", "hidden", "private-locator", "Private procedure", "private body", str(paths.home)):
            self.assertNotIn(forbidden, public)
        self.assertLessEqual(len(result["recent"]), summary.MAX_RECENT_ITEMS)
        self.assertTrue(all(item["href"] in {"/tasks", "#page-static", "/dashboard"} for item in result["recent"]))
        self.assertTrue(all(source["readOnly"] for source in result["sources"]))

    def test_missing_runtime_is_unknown_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "not-initialized"
            result = summary.get_summary(paths=runtime_paths_for_home(home))
            self.assertFalse(home.exists())
        self.assertEqual(result["status"], "degraded")
        self.assertIsNone(result["assets"]["completedTasks"]["count"])
        self.assertIsNone(result["assets"]["reports"]["count"])
        self.assertEqual(result["assets"]["lessons"]["count"], 0)
        self.assertEqual(result["assets"]["skills"]["count"], 0)

    def test_empty_initialized_runtime_is_zero_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            result = summary.get_summary(paths=paths)
        self.assertEqual(result["status"], "empty")
        self.assertTrue(all(value["count"] == 0 for value in result["assets"].values()))
        self.assertEqual(result["sourceErrors"], [])

    def test_bounded_ledger_counts_identify_their_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.ledger(paths.home, day="2026-09-05")
            self.ledger(paths.home, day="2026-09-06")
            self.ledger(paths.home, day="2026-09-07")
            with patch.object(summary, "MAX_LEDGER_DAYS", 2):
                result = summary.get_summary(paths=paths)
        metric = result["assets"]["lessons"]
        self.assertEqual(metric["count"], 2)
        self.assertFalse(metric["scope"]["complete"])
        self.assertEqual(metric["scope"]["availableDays"], 3)
        self.assertEqual(metric["scope"]["from"], "2026-09-06")
        self.assertEqual(metric["scope"]["through"], "2026-09-07")

    def test_corrupt_latest_ledger_is_not_replaced_with_old_success_or_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.ledger(paths.home, version=21)
            invalid = self.ledger(paths.home, version=22)
            invalid.write_text("{corrupt}\n")
            self.ledger(paths.home, day="2026-09-06")
            result = summary.get_summary(paths=paths)
        lessons = result["assets"]["lessons"]
        self.assertIsNone(lessons["count"])
        self.assertEqual(lessons["availableCount"], 1)
        self.assertEqual(lessons["status"], "degraded")
        self.assertEqual(result["assets"]["reports"]["count"], 0)
        self.assertFalse(any(item["businessDate"] == "2026-09-07" for item in result["recent"]))

    def test_all_reads_keep_persisted_runtime_byte_identical_and_open_database_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.seed_sql(paths)
            self.ledger(paths.home)
            # SQLite may create transient WAL coordination files even for a
            # mode=ro handle; persisted database/ledger/artifact bytes stay put.
            before = {str(path.relative_to(paths.home)): path.read_bytes()
                      for path in paths.home.rglob("*")
                      if path.is_file() and not path.name.endswith(("-shm", "-wal"))}
            with patch.object(summary, "connect", wraps=connect) as observed:
                summary.get_summary(paths=paths)
            after = {str(path.relative_to(paths.home)): path.read_bytes()
                     for path in paths.home.rglob("*")
                     if path.is_file() and not path.name.endswith(("-shm", "-wal"))}
        self.assertEqual(before, after)
        self.assertTrue(observed.call_args_list)
        self.assertTrue(all(call.kwargs == {"read_only": True} for call in observed.call_args_list))

    def test_canonical_inventory_limit_is_not_reported_as_an_exact_total(self):
        with patch.object(summary.archive, "_canonical_skill_inventory", return_value={
            "count": 400, "status": "ready", "sourceErrors": [],
        }):
            metric, errors = summary._skills(runtime_paths_for_home(Path("/unused")))
        self.assertEqual(metric["count"], 400)
        self.assertFalse(metric["scope"]["complete"])
        self.assertEqual(errors, [])

    def test_canonical_learning_is_exposed_separately_with_its_own_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.ledger(paths.home, title="Same experience")
            target = paths.home / "artifacts" / "learning" / "lessons.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(json.dumps(row) for row in (
                {"id": "same-id", "text": "Same experience", "date": "2026-09-07"},
                {"id": "same-id", "text": "Updated duplicate", "date": "2026-09-07"},
                {"id": "another-id", "text": "Preserve /Users/operator/private api_key=hidden"},
                {"problem": "Not a retrievable Lesson without text"},
            )) + "\n")
            result = summary.get_summary(paths=paths)
        self.assertEqual(result["assets"]["lessons"]["count"], 1)
        self.assertEqual(result["assets"]["learningNotes"]["count"], 2)
        learning = [row for row in result["recent"] if row["source"] == "canonical-learning-lessons"]
        self.assertEqual(len(learning), 2)
        self.assertNotIn("hidden", json.dumps(learning))
        self.assertNotIn("/Users/operator", json.dumps(learning))

    def test_corrupt_canonical_learning_keeps_known_rows_but_not_a_false_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            target = paths.home / "artifacts" / "learning" / "lessons.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"id":"valid","text":"A learning record"}\n{corrupt}\n')
            result = summary.get_summary(paths=paths)
        self.assertIsNone(result["assets"]["learningNotes"]["count"])
        self.assertEqual(result["assets"]["learningNotes"]["availableCount"], 1)
        self.assertEqual(result["assets"]["learningNotes"]["status"], "degraded")

    def test_canonical_learning_does_not_follow_symlinks_or_oversized_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            target = paths.home / "artifacts" / "learning" / "lessons.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            actual = Path(tmp) / "external-lessons.jsonl"
            actual.write_text('{"text":"Do not expose through a symlink"}\n')
            target.symlink_to(actual)
            linked = summary.get_summary(paths=paths)
            self.assertIsNone(linked["assets"]["learningNotes"]["count"])
            self.assertFalse(any(item["source"] == "canonical-learning-lessons" for item in linked["recent"]))
            target.unlink()
            target.write_text('{"text":"This exceeds the fixture byte limit"}\n')
            with patch.object(summary, "MAX_LEDGER_BYTES", 1):
                oversized = summary.get_summary(paths=paths)
        self.assertIsNone(oversized["assets"]["learningNotes"]["count"])

    def test_disabled_task_management_preserves_completed_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.seed_sql(paths)
            with patch.object(summary, "is_nova_task_enabled", return_value=False):
                result = summary.get_summary(paths=paths)
        self.assertEqual(result["assets"]["completedTasks"]["count"], 3)
        self.assertFalse(result["assets"]["completedTasks"]["enabled"])

    def test_router_is_read_only_no_store_and_does_not_return_raw_errors(self):
        with patch.object(summary, "get_summary", return_value={"status": "ready"}):
            response = asyncio.run(summary_router.api_dashboard_summary())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        with patch.object(summary, "get_summary", side_effect=RuntimeError("/Users/private token=hidden")), self.assertLogs(summary_router.logger, level="ERROR"):
            error = asyncio.run(summary_router.api_dashboard_summary())
        self.assertEqual(error.status_code, 500)
        self.assertNotIn(b"hidden", error.body)
        self.assertNotIn(b"/Users/private", error.body)
        self.assertTrue(all(route.methods == {"GET"} for route in summary_router.router.routes))

    def test_summary_uses_shared_session_authentication(self):
        from app.services.dashboard_security import is_protected_path, is_session_exempt_path
        self.assertTrue(is_protected_path("/api/dashboard/summary"))
        self.assertFalse(is_session_exempt_path("/api/dashboard/summary"))
        self.assertTrue(is_protected_path("/api/dashboard/document"))
        self.assertFalse(is_session_exempt_path("/api/dashboard/document"))

    def test_recent_lessons_include_sanitized_readable_summary_and_review_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.runtime(Path(tmp) / "runtime")
            self.ledger(paths.home)
            result = summary.get_summary(paths=paths)
        lesson = next(item for item in result["recent"] if item["type"] == "lesson")
        self.assertEqual(lesson["reviewId"], "review-001")
        self.assertIn("Useful summary", lesson["summary"])
        self.assertNotIn("hidden", lesson["summary"])
        self.assertNotIn("/Users/operator", lesson["summary"])

    def test_document_uses_requested_report_type_and_removes_private_paths(self):
        from datetime import date
        paths = runtime_paths_for_home(Path("/tmp/dashboard-documents"))
        documents = [{
            "report_type": kind,
            "title": f"{kind} /Users/operator/private api_key=hidden",
            "document_key": "private-database-key",
            "relative_path": "private-directory/document.md",
            "sections": [{"headingLevel": 2, "heading": "Saved evidence", "bodyMarkdown": "Keep **formatting**.\n\n/Users/operator/private token=hidden"}],
        } for kind in ("narrative", "technical", "learning")]
        for kind in ("narrative", "technical", "learning"):
            with patch.object(summary, "read_diary_markdown_documents", return_value=documents) as reader:
                result = summary.get_document("2026-09-07", kind, paths=paths)
            reader.assert_called_once_with(paths, date(2026, 9, 7), date(2026, 9, 7))
            self.assertEqual(result["type"], kind)
            self.assertTrue(result["title"].startswith(kind))
            self.assertIn("\n\n", result["content"])
            self.assertIn("**formatting**", result["content"])
            for private in ("hidden", "/Users/operator", "private-database-key", "private-directory"):
                self.assertNotIn(private, json.dumps(result))

    def test_document_validates_queries_and_reports_missing_or_ambiguous_authority(self):
        for stamp, kind in (("20260907", "narrative"), ("2026-02-31", "learning"), ("2026-09-07", "tokens")):
            with self.assertRaises(summary.DashboardDocumentQueryError):
                summary.get_document(stamp, kind)
        with patch.object(summary, "read_diary_markdown_documents", return_value=[]):
            with self.assertRaises(summary.DashboardDocumentNotFound):
                summary.get_document("2026-09-07", "technical")
        with patch.object(summary, "read_diary_markdown_documents", return_value=[{"report_type": "learning"}] * 2):
            with self.assertRaises(summary.DashboardDocumentAmbiguous):
                summary.get_document("2026-09-07", "learning")

    def test_document_bound_is_explicit(self):
        documents = [{"report_type": "technical", "title": "Saved report", "sections": []}]
        with patch.object(summary, "read_diary_markdown_documents", return_value=documents), patch.object(summary, "MAX_DOCUMENT_CHARS", 5):
            result = summary.get_document("2026-09-07", "technical")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["content"]), 5)

    def test_document_router_preserves_typed_error_status_and_no_store(self):
        cases = ((summary.DashboardDocumentQueryError("Invalid date"), 400),
                 (summary.DashboardDocumentNotFound("Missing"), 404),
                 (summary.DashboardDocumentAmbiguous("Ambiguous"), 409))
        for error, code in cases:
            with patch.object(summary, "get_document", side_effect=error):
                response = asyncio.run(summary_router.api_dashboard_document("2026-09-07", "learning"))
            self.assertEqual(response.status_code, code)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_canonical_skills_are_listed_and_read_without_a_usage_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = runtime_paths_for_home(Path(tmp) / "runtime")
            canonical = paths.home / "assets" / "skills" / "recover-files"
            canonical.mkdir(parents=True)
            saved = canonical / "SKILL.md"
            saved.write_text("---\nname: recover-files\ndescription: Verify recovered files.\n---\n\n# Recovery\n\n1. Read the restored file.\n2. Verify its checksum.\n")
            original = saved.read_bytes()
            listing = summary.get_skills(paths=paths)
            detail = summary.get_skill(listing["items"][0]["id"], paths=paths)
            self.assertEqual(saved.read_bytes(), original)
            self.assertFalse(paths.db_path.exists())
        self.assertEqual(listing["status"], "ready")
        self.assertEqual(listing["count"], 1)
        self.assertEqual(set(listing["items"][0]), {"id", "name", "description"})
        self.assertRegex(listing["items"][0]["id"], r"^asset-[0-9a-f]{12}$")
        self.assertEqual(detail["id"], listing["items"][0]["id"])
        self.assertEqual(detail["name"], "recover-files")
        self.assertIn("Verify its checksum", detail["content"])
        self.assertNotIn(str(paths.home), json.dumps({"list": listing, "detail": detail}))

    def test_canonical_skill_missing_and_unsafe_roots_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = runtime_paths_for_home(Path(tmp) / "runtime")
            self.assertEqual(summary.get_skills(paths=paths)["status"], "empty")
            self.assertFalse(paths.home.exists())
            with self.assertRaises(summary.DashboardSkillNotFound):
                summary.get_skill("asset-000000000000", paths=paths)
            actual = Path(tmp) / "other-library"
            actual.mkdir()
            canonical = paths.home / "assets" / "skills"
            canonical.parent.mkdir(parents=True)
            canonical.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(summary.DashboardSkillsUnavailable):
                summary.get_skills(paths=paths)

    def test_canonical_skill_detail_rejects_paths_before_reading_library(self):
        with patch.object(summary, "_read_canonical_library") as reader:
            for invalid in ("../../private", "/Users/operator/SKILL.md", "recover-files", "asset-xyz", ""):
                with self.assertRaises(summary.DashboardSkillNotFound):
                    summary.get_skill(invalid)
        reader.assert_not_called()

    def test_canonical_skill_list_limit_is_disclosed(self):
        from diary_generator.skill_pass_minimal_harness import ExistingSkillAsset
        asset = ExistingSkillAsset("asset-000000000000", "skill", "Description", "actanara", "# Body", True)
        with patch.object(summary, "_read_canonical_library", return_value=(asset,) * 400):
            listing = summary.get_skills()
        self.assertFalse(listing["scope"]["complete"])
        self.assertEqual(listing["scope"]["limit"], 400)

    def test_canonical_skill_routers_keep_unknown_distinct_from_empty(self):
        with patch.object(summary, "get_skills", return_value={"status": "empty", "count": 0, "items": []}):
            empty = asyncio.run(summary_router.api_dashboard_skills())
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.headers["cache-control"], "no-store")
        with patch.object(summary, "get_skills", side_effect=RuntimeError("private location")), self.assertLogs(summary_router.logger, level="WARNING"):
            unavailable = asyncio.run(summary_router.api_dashboard_skills())
        self.assertEqual(unavailable.status_code, 503)
        self.assertIsNone(json.loads(unavailable.body)["count"])
        self.assertNotIn(b"private location", unavailable.body)
        with patch.object(summary, "get_skill", side_effect=summary.DashboardSkillNotFound("Missing Skill")):
            missing = asyncio.run(summary_router.api_dashboard_skill("asset-000000000000"))
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["cache-control"], "no-store")

    def test_canonical_skill_routes_are_authenticated(self):
        from app.services.dashboard_security import is_protected_path, is_session_exempt_path
        for route in ("/api/dashboard/skills", "/api/dashboard/skills/asset-000000000000"):
            self.assertTrue(is_protected_path(route))
            self.assertFalse(is_session_exempt_path(route))


if __name__ == "__main__":
    unittest.main()
