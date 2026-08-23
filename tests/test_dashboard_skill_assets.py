import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import ai_assets as ai_assets_router
from app.services import skill_assets
from data_foundation.paths import initialize_home, runtime_paths_for_home
from diary_generator import skill_pass_minimal_harness as harness


class DashboardSkillAssetProjectionTests(unittest.TestCase):
    @staticmethod
    def _row(
        *,
        business_date: str = "2026-08-12",
        prompt_version: str = "minimal-v21",
        asset_class: str = "skill",
        review_id: str = "review-001",
    ) -> dict:
        skill = asset_class == "skill"
        return {
            "schema": "actanara.skill-asset-ledger.v1",
            "businessDate": business_date,
            "promptVersion": prompt_version,
            "assetClass": asset_class,
            "disposition": "library-create" if skill else "adjudicated",
            "candidateId": "candidate-001",
            "reviewId": review_id,
            "originalDecision": asset_class,
            "title": "在可观察失败后执行已验证的恢复程序",
            "summary": "遇到非平凡阻碍后，执行最小恢复并观察成功结果。",
            "reason": "行动与验证记录形成一条因果程序。",
            "evidence": ["private-locator-one"],
            "evidenceRecords": [1],
            "scores": {"evidence": 5, "value": 4, "reuse": 4, "program": 5},
            "completion": "verified" if skill else None,
            "completionReason": "结果已观察。" if skill else None,
            "portfolioReason": "具有独立复用价值。" if skill else None,
            "libraryAction": "create" if skill else None,
            "existingAssetId": None,
            "existingSkillName": None,
            "skillName": "recover-verified-state" if skill else None,
            "skillDescription": "当恢复动作可能留下不一致状态时，使用事务内验证程序确认目标服务真正可用。" if skill else None,
            "skillMarkdown": "---\nname: recover-verified-state\ndescription: 当恢复动作可能留下不一致状态时，使用事务内验证程序确认目标服务真正可用。\n---\n\n# 恢复并验证\n" if skill else None,
        }

    @staticmethod
    def _write_ledger(home: Path, rows: list[dict], *, version: int = 21, business_date: str = "2026-08-12") -> Path:
        root = home / "artifacts" / "skills"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / f"skill-harness-minimal-v{version}-assets-{business_date}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_missing_ledger_is_explicit_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = runtime_paths_for_home(Path(tmp) / "runtime")
            result = skill_assets.get_skill_asset_review(paths=paths)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["counts"], {"skill": 0, "lesson": 0, "reference": 0, "discard": 0})

    def test_projection_defaults_only_complete_skill_proposals_and_hides_private_locators(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            rows = [
                self._row(),
                self._row(asset_class="lesson", review_id="review-002"),
                self._row(asset_class="reference", review_id="review-003"),
                self._row(asset_class="discard", review_id="review-004"),
            ]
            for index, row in enumerate(rows, start=1):
                row["candidateId"] = f"candidate-{index:03d}"
            self._write_ledger(home, rows)
            result = skill_assets.get_skill_asset_review(paths=paths)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["businessDate"], "2026-08-12")
        self.assertEqual(result["promptVersion"], "minimal-v21")
        self.assertEqual(result["counts"], {"skill": 1, "lesson": 1, "reference": 1, "discard": 1})
        self.assertTrue(result["items"][0]["selectedByDefault"])
        self.assertTrue(all(not item["selectedByDefault"] for item in result["items"][1:]))
        self.assertEqual(result["items"][0]["evidenceCount"], 1)
        public = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private-locator-one", public)
        self.assertNotIn(str(home), public)

    def test_latest_numeric_prompt_version_wins_for_same_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            old = self._row(prompt_version="minimal-v9")
            new = self._row(prompt_version="minimal-v21")
            self._write_ledger(home, [old], version=9)
            self._write_ledger(home, [new], version=21)
            result = skill_assets.get_skill_asset_review("2026-08-12", paths=paths)
        self.assertEqual(result["promptVersion"], "minimal-v21")

    def test_compact_evidence_locator_may_cover_multiple_source_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            row = self._row()
            row["evidence"] = ["compact-private-locator"]
            row["evidenceRecords"] = [4, 5, 6]
            self._write_ledger(home, [row])
            result = skill_assets.get_skill_asset_review(paths=paths)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["items"][0]["evidenceCount"], 3)
        self.assertNotIn("compact-private-locator", json.dumps(result))

    def test_empty_authoritative_ledger_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            self._write_ledger(home, [])
            result = skill_assets.get_skill_asset_review(paths=paths)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["items"], [])

    def test_unsafe_permissions_duplicate_keys_and_non_skill_draft_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            path = self._write_ledger(home, [self._row()])
            path.chmod(0o644)
            with self.assertRaises(skill_assets.SkillAssetLedgerError):
                skill_assets.get_skill_asset_review(paths=paths)

            path.chmod(0o600)
            duplicate = json.dumps(self._row(), ensure_ascii=False)[:-1] + ',"title":"forged"}\n'
            path.write_text(duplicate, encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(skill_assets.SkillAssetLedgerError):
                skill_assets.get_skill_asset_review(paths=paths)

            lesson = self._row(asset_class="lesson")
            lesson["skillName"] = "should-not-exist"
            path.write_text(json.dumps(lesson, ensure_ascii=False) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaises(skill_assets.SkillAssetLedgerError):
                skill_assets.get_skill_asset_review(paths=paths)

    def test_symlinked_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            root = home / "artifacts" / "skills"
            root.mkdir(parents=True)
            outside = Path(tmp) / "outside.jsonl"
            outside.write_text(json.dumps(self._row(), ensure_ascii=False) + "\n", encoding="utf-8")
            outside.chmod(0o600)
            (root / "skill-harness-minimal-v21-assets-2026-08-12.jsonl").symlink_to(outside)
            with self.assertRaises((skill_assets.SkillAssetLedgerError, OSError)):
                skill_assets.get_skill_asset_review(paths=paths)

    def test_router_maps_contract_errors_without_private_details(self):
        with patch.object(
            ai_assets_router.skill_assets,
            "get_skill_asset_review",
            side_effect=skill_assets.SkillAssetLedgerError("malformed Skill asset ledger"),
        ):
            response = asyncio.run(ai_assets_router.api_ai_assets_skill_assets("2026-08-12"))
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("/Users/", response.body.decode("utf-8"))

    def test_force_crystallization_request_rejects_invalid_and_non_lesson_rows(self):
        with self.assertRaises(skill_assets.SkillAssetActionError) as invalid:
            skill_assets.force_crystallize_lessons({"reviewIds": []})
        self.assertEqual(invalid.exception.code, "skill-crystallization-invalid")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = runtime_paths_for_home(home)
            self._write_ledger(home, [self._row()])
            with self.assertRaises(skill_assets.SkillAssetActionError) as not_lesson:
                skill_assets.force_crystallize_lessons(
                    {
                        "businessDate": "2026-08-12",
                        "promptVersion": "minimal-v21",
                        "reviewIds": ["review-001"],
                    },
                    paths=paths,
                )
        self.assertEqual(
            not_lesson.exception.code,
            "skill-crystallization-not-lesson",
        )

    def test_crystallization_router_preserves_no_registration_boundary(self):
        result = {
            "status": "ready",
            "crystallizedReviewIds": ["review-002"],
            "notCrystallized": [],
            "registrationRequested": False,
            "review": {"status": "ready", "items": []},
        }
        with patch.object(
            ai_assets_router.skill_assets,
            "force_crystallize_lessons",
            return_value=result,
        ) as action:
            response = asyncio.run(
                ai_assets_router.api_ai_assets_skill_assets_crystallize(
                    {
                        "businessDate": "2026-08-12",
                        "promptVersion": "minimal-v21",
                        "reviewIds": ["review-002"],
                    }
                )
            )
        self.assertEqual(response, result)
        self.assertFalse(response["registrationRequested"])
        action.assert_called_once()

    def test_registration_router_calls_separate_registration_service(self):
        result = {
            "status": "completed",
            "registrationRequested": True,
            "registeredTools": ["codex"],
            "eligibleTools": ["codex"],
            "results": [],
        }
        with patch.object(
            ai_assets_router.skill_asset_registration,
            "register_finalized_skill_assets",
            return_value=result,
        ) as action:
            response = asyncio.run(
                ai_assets_router.api_ai_assets_skill_assets_register(
                    {
                        "businessDate": "2026-08-12",
                        "promptVersion": "minimal-v21",
                        "reviewIds": ["review-001"],
                    }
                )
            )
        self.assertEqual(response, result)
        self.assertTrue(response["registrationRequested"])
        action.assert_called_once()

    def test_historical_lesson_is_read_only_under_current_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "runtime"
            paths = initialize_home(home)
            filtered = (
                paths.diary_dir
                / "__diary_daily"
                / "2026-08-12"
                / "_filtered"
                / "codex"
            )
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "time": f"10:0{index}",
                            "role": "assistant",
                            "content": text,
                            "conversationId": "thread-1",
                        }
                    )
                    for index, text in enumerate(
                        ("goal", "obstacle", "decisive action", "verified result")
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            lesson = self._row(asset_class="lesson")
            lesson["scores"] = {"evidence": 5, "value": 2, "reuse": 4, "program": 4}
            ledger = self._write_ledger(home, [lesson])
            root = ledger.parent
            discovery = """<!-- actanara-discovery -->
# 用已验证结果收口恢复动作
Records: 000001, 000002, 000003, 000004
Why: 困难发生后执行决定性动作，并观察到恢复结果。
"""
            adjudication = """<!-- actanara-harness-review -->
# 用已验证结果收口恢复动作
Candidate-ID: candidate-001
Decision: lesson
Evidence-Records: 000001, 000002, 000003, 000004
Action-Records: 000003
Verification-Records: 000004
Scores: Evidence 5/5 · Value 2/5 · Reuse 4/5 · Program 4/5
Reason: 该做法有程序性，但未达到自动 Skill 裁决门槛。
"""
            for filename, content in (
                (
                    "skill-harness-minimal-v21-raw-discovery-2026-08-12.md",
                    discovery,
                ),
                (
                    "skill-harness-minimal-v21-raw-adjudication-2026-08-12.md",
                    adjudication,
                ),
            ):
                path = root / filename
                path.write_text(content, encoding="utf-8")
                path.chmod(0o600)
            calls: list[dict] = []

            def llm_call(prompt: str, **kwargs) -> str:
                calls.append({"prompt": prompt, **kwargs})
                return """<!-- actanara-skill-proposal -->
Review-ID: review-001
Library-Action: create
Existing-Skill: none
Library-Reason: 用户要求对这条有完整动作与验证的 Lesson 做例外结晶。
---
name: close-recovery-with-verification
description: 当恢复动作已执行但完成状态仍不明确时，使用直接行为验证来确认目标状态。
---

# 用行为验证收口恢复动作

## Trigger
恢复动作已执行，但完成状态仍不明确。

## Procedure
执行记录中已经验证有效的最小恢复动作。

## Pitfalls
不要把动作已提交误当成目标状态已恢复。

## Verification
观察目标行为是否恢复并满足完成条件。
"""

            before = ledger.read_bytes()
            with self.assertRaises(skill_assets.SkillAssetActionError) as raised:
                skill_assets.force_crystallize_lessons(
                    {
                        "businessDate": "2026-08-12",
                        "promptVersion": "minimal-v21",
                        "reviewIds": ["review-001"],
                    },
                    paths=paths,
                    llm_call=llm_call,
                )

            self.assertEqual(raised.exception.code, "skill-crystallization-version-mismatch")
            self.assertEqual(calls, [])
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)


class DashboardSkillAssetStaticContractTests(unittest.TestCase):
    def test_review_ui_defaults_only_skill_and_registers_on_generate(self):
        html = (ROOT / "src" / "dashboard" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "src" / "dashboard" / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "src" / "dashboard" / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn('id="aaSkillAssetReview"', html)
        self.assertIn("fetch('/api/ai-assets/skill-assets')", script)
        self.assertIn("item.selectedByDefault === true && item.assetClass === 'skill'", script)
        self.assertIn("['skill', 'lesson'].includes(item.assetClass)", script)
        self.assertIn("fetch('/api/ai-assets/skill-assets/crystallize'", script)
        self.assertIn("fetch('/api/ai-assets/skill-assets/register'", script)
        self.assertIn("lessons.map(item => item.reviewId)", script)
        self.assertIn("async function generateAndRegisterSelectedSkillAssets()", script)
        self.assertNotIn("actanara.skill-draft-review-bundle.v1", script)
        self.assertNotIn("downloadSelectedSkillAssetDrafts", script)
        self.assertIn("finalized.map(item => item.reviewId)", script)
        self.assertIn(".aa-skill-assets-actions", css)


if __name__ == "__main__":
    unittest.main()
