import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import archive as archive_router
from app.services import archive
from data_foundation.paths import runtime_paths_for_home


class DashboardArchiveApiTests(unittest.TestCase):
    @staticmethod
    def _row(
        *,
        business_date: str = "2026-08-22",
        prompt_version: str = "minimal-v22",
        asset_class: str = "lesson",
        original_decision: str | None = None,
        review_id: str = "review-001",
        completion: str | None = None,
        library_action: str | None = None,
        private_path: str = "/Users/operator/Private/evidence.txt",
    ) -> dict:
        original = original_decision or asset_class
        skill_draft = asset_class == "skill"
        return {
            "schema": "actanara.skill-asset-ledger.v1",
            "businessDate": business_date,
            "promptVersion": prompt_version,
            "assetClass": asset_class,
            "disposition": "library-create" if skill_draft else "adjudicated",
            "candidateId": "candidate-001",
            "reviewId": review_id,
            "originalDecision": original,
            "title": f"验证恢复程序 {private_path}",
            "summary": f"执行恢复动作并验证结果；内部位置为 {private_path}；api_key=supersecret",
            "reason": "行动后观察到了可验证结果。",
            "evidence": [f"private-locator:{private_path}"],
            "evidenceRecords": [1, 2],
            "scores": {"evidence": 5, "value": 4, "reuse": 4, "program": 5},
            "completion": completion,
            "completionReason": "Action 后 Result 已被观察。" if completion else None,
            "portfolioReason": "可作为独立程序复用。" if original == "skill" else None,
            "libraryAction": library_action or ("create" if skill_draft else None),
            "existingAssetId": None,
            "existingSkillName": "existing-private-skill" if library_action == "covered" else None,
            "skillName": "recover-and-verify" if skill_draft else None,
            "skillDescription": "恢复状态后执行确定性验证。" if skill_draft else None,
            "skillMarkdown": (
                "---\nname: recover-and-verify\ndescription: 恢复并验证。\n---\n"
                "\n# TOP SECRET EVIDENCE BODY\n"
                if skill_draft
                else None
            ),
        }

    @staticmethod
    def _write_ledger(home: Path, rows: list[dict], *, version: int, business_date: str) -> Path:
        root = home / "artifacts" / "skills"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / f"skill-harness-minimal-v{version}-assets-{business_date}.jsonl"
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def test_v21_v22_projection_is_stable_read_only_and_hides_private_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            v21_lesson = self._row(
                business_date="2026-08-21",
                prompt_version="minimal-v21",
            )
            v22_skill = self._row(
                asset_class="skill",
                original_decision="skill",
                completion="verified",
            )
            v22_reference = self._row(
                asset_class="reference",
                original_decision="reference",
                review_id="review-002",
            )
            v22_discard = self._row(
                asset_class="discard",
                original_decision="discard",
                review_id="review-003",
            )
            for index, row in enumerate((v22_skill, v22_reference, v22_discard), start=1):
                row["candidateId"] = f"candidate-{index:03d}"
            self._write_ledger(home, [v21_lesson], version=21, business_date="2026-08-21")
            self._write_ledger(
                home,
                [v22_skill, v22_reference, v22_discard],
                version=22,
                business_date="2026-08-22",
            )

            first = archive.get_assets(paths=paths)
            second = archive.get_assets(paths=paths)
            runs = archive.get_skill_pass_runs(paths=paths)

        self.assertEqual(first["counts"], {"all": 4, "experience": 2, "practice": 1, "skill": 1})
        self.assertEqual([item["id"] for item in first["items"]], [item["id"] for item in second["items"]])
        self.assertEqual({item["type"] for item in first["items"]}, {"experience", "practice", "skill"})
        self.assertNotIn("reference", {item["type"] for item in first["items"]})
        self.assertNotIn("discard", {item["type"] for item in first["items"]})
        self.assertTrue(all(item["source"]["readOnly"] for item in first["items"]))
        historical = next(item for item in first["items"] if item["promptVersion"] == "minimal-v21")
        self.assertTrue(historical["source"]["historical"])
        current = next(item for item in first["items"] if item["promptVersion"] == "minimal-v22")
        self.assertFalse(current["source"]["historical"])
        public = json.dumps({"assets": first, "runs": runs}, ensure_ascii=False)
        self.assertNotIn("private-locator", public)
        self.assertNotIn("TOP SECRET EVIDENCE BODY", public)
        self.assertNotIn("/Users/operator", public)
        self.assertNotIn("supersecret", public)
        self.assertNotIn(str(home), public)
        v22_run = next(item for item in runs["items"] if item["version"] == 22)
        self.assertEqual(v22_run["counts"]["reference"], 1)
        self.assertEqual(v22_run["counts"]["discard"], 1)

    def test_verified_skill_decision_retains_practice_when_final_disposition_is_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            covered = self._row(
                asset_class="reference",
                original_decision="skill",
                completion="verified",
                library_action="covered",
            )
            self._write_ledger(home, [covered], version=22, business_date="2026-08-22")
            result = archive.get_assets(paths=paths)

        self.assertEqual(result["counts"], {"all": 2, "experience": 1, "practice": 1, "skill": 0})
        self.assertEqual({item["type"] for item in result["items"]}, {"experience", "practice"})
        practice = next(item for item in result["items"] if item["type"] == "practice")
        experience = next(item for item in result["items"] if item["type"] == "experience")
        self.assertEqual(practice["relations"]["derivedFrom"], [experience["id"]])
        self.assertTrue(practice["metrics"]["actionVerified"])
        self.assertTrue(practice["metrics"]["resultObserved"])

    def test_finalized_human_override_skill_still_projects_the_full_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            override = self._row(
                asset_class="skill",
                original_decision="lesson",
                completion=None,
            )
            override["humanOverride"] = True
            override["humanOverrideFrom"] = "lesson"
            self._write_ledger(home, [override], version=22, business_date="2026-08-22")
            result = archive.get_assets(paths=paths)

        self.assertEqual(result["counts"], {"all": 3, "experience": 1, "practice": 1, "skill": 1})
        self.assertEqual({item["type"] for item in result["items"]}, {"experience", "practice", "skill"})

    def test_bootstrap_and_activity_whitelist_foundation_snapshot_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            lesson = self._row()
            self._write_ledger(home, [lesson], version=22, business_date="2026-08-22")
            canonical = home / "assets" / "skills" / "canonical-one"
            canonical.mkdir(parents=True)
            canonical.joinpath("SKILL.md").write_text(
                "---\nname: canonical-one\ndescription: A canonical Actanara Skill.\n---\n\n# Canonical\n",
                encoding="utf-8",
            )
            snapshot = {
                "payload": {
                    "totalTokens": 42,
                    "totalMessages": 7,
                    "totalSessions": 3,
                    "activeDayCount": 2,
                    "diary": {"count": 8, "privatePath": "/Users/operator/diary"},
                    "memory": {"sessionFiles": 9, "privatePath": "/Users/operator/memory"},
                    "skills": {"total": 11, "byTool": {"Codex": [{"path": "/secret"}]}},
                    "infrastructure": {"devices": [{"name": "Mac"}], "secret": "/Users/operator"},
                    "agents": [{"name": "private-agent", "workspace": "/Users/operator/project"}],
                    "agentCount": 1,
                    "tools": [
                        {
                            "name": "Codex",
                            "usageStatus": "available",
                            "sessionCount": 3,
                            "allTimeMessages": 7,
                            "allTimeTokens": 42,
                            "lastActivity": "2026-08-22 12:00",
                            "workspace": "/Users/operator/project",
                        }
                    ],
                    "trend30d": [{"date": "2026-08-22", "slots": {"上午": 42}}],
                    "models": [{"name": "gpt-test", "tokens": 42, "messages": 7, "sessions": 3}],
                },
                "projectionType": "foundation-ai-assets-non-rag-v2",
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "sourceRunId": 1,
                "status": "ready",
            }
            with (
                patch.object(archive, "read_dashboard_snapshot", return_value=snapshot),
                patch.object(archive, "_archive_today", return_value=datetime(2026, 8, 23).date()),
                patch.object(
                    archive,
                    "_foundation_report_inventory",
                    return_value={
                        "status": "ready",
                        "count": 12,
                        "unit": "documents",
                        "sourceErrors": [],
                    },
                ),
            ):
                bootstrap = archive.get_bootstrap(paths=paths)
                bootstrap_again = archive.get_bootstrap(paths=paths)
                activity = archive.get_activity(paths=paths)

        self.assertEqual(
            bootstrap["inventory"]["diary"],
            {"status": "ready", "count": 8, "unit": "days"},
        )
        self.assertEqual(
            bootstrap["inventory"]["memory"],
            {"status": "ready", "count": 9, "unit": "files"},
        )
        self.assertEqual(
            bootstrap["inventory"]["skills"],
            {"status": "ready", "count": 11, "unit": "installedInstances"},
        )
        self.assertEqual(
            bootstrap["inventory"]["reports"],
            {"status": "ready", "count": 12, "unit": "documents"},
        )
        self.assertEqual(bootstrap["summary"]["activeSkillCount"], 1)
        self.assertEqual(bootstrap["summary"]["installedSkillInstanceCount"], 11)
        self.assertEqual(bootstrap["dailyQuote"], bootstrap_again["dailyQuote"])
        self.assertTrue(bootstrap["dailyQuote"]["source"])
        self.assertNotIn("[local path]", bootstrap["dailyQuote"]["text"])
        self.assertEqual(bootstrap["dailyQuote"]["date"], "2026-08-23")
        self.assertEqual(activity["totals"], {"tokens": 42, "messages": 7, "sessions": 3, "activeDays": 2})
        self.assertEqual(activity["trend30d"][0]["slots"], {"上午": 42})
        self.assertEqual(activity["agents"][0]["name"], "Codex")
        public = json.dumps({"bootstrap": bootstrap, "activity": activity}, ensure_ascii=False)
        self.assertNotIn("/Users/operator", public)
        self.assertNotIn("workspace", public)
        self.assertNotIn("privatePath", public)

    def test_foundation_failure_is_degraded_with_unknown_not_fake_zero(self):
        marker = "secret-token /Users/operator/private.sqlite3"
        with tempfile.TemporaryDirectory() as tmp:
            paths = runtime_paths_for_home(Path(tmp) / "runtime")
            with (
                patch.object(archive, "read_dashboard_snapshot", side_effect=OSError(marker)),
                patch.object(archive.logger, "warning"),
            ):
                bootstrap = archive.get_bootstrap(paths=paths)
                activity = archive.get_activity(paths=paths)

        self.assertEqual(bootstrap["status"], "degraded")
        self.assertEqual(activity["status"], "degraded")
        self.assertEqual(activity["totals"], {"tokens": None, "messages": None, "sessions": None, "activeDays": None})
        self.assertTrue(all(item["count"] is None for item in bootstrap["inventory"].values()))
        self.assertNotIn(marker, json.dumps({"bootstrap": bootstrap, "activity": activity}))

    def test_filters_detail_and_router_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            row = self._row(
                asset_class="skill",
                original_decision="skill",
                completion="verified",
            )
            self._write_ledger(home, [row], version=22, business_date="2026-08-22")
            practices = archive.get_assets(asset_type="practice", status="verified", paths=paths)
            detail = archive.get_asset(practices["items"][0]["id"], paths=paths)

        self.assertEqual(practices["counts"]["all"], 1)
        self.assertEqual(detail["item"]["type"], "practice")
        with self.assertRaises(archive.ArchiveQueryError):
            archive.get_assets(asset_type="reference")
        with self.assertRaises(archive.ArchiveAssetNotFound):
            archive.get_asset("not-an-asset")

        self.assertEqual(
            {route.path for route in archive_router.router.routes},
            {
                "/archive/v1/bootstrap",
                "/archive/v1/assets",
                "/archive/v1/assets/{asset_id}",
                "/archive/v1/skill-pass/runs",
                "/archive/v1/activity",
            },
        )
        with patch.object(archive_router.archive, "get_assets", side_effect=archive.ArchiveQueryError("bad")):
            response = asyncio.run(archive_router.api_archive_assets(asset_type="bad", limit=50, offset=0))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("/Users/", response.body.decode("utf-8"))

        marker = "/Users/operator/private.sqlite3 secret-token"
        with (
            patch.object(archive_router.archive, "get_activity", side_effect=RuntimeError(marker)),
            patch.object(archive_router.logger, "exception"),
        ):
            response = asyncio.run(archive_router.api_archive_activity())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(marker, response.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
