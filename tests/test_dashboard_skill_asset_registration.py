import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.services import skill_asset_registration as registration
from data_foundation.paths import initialize_home
from data_foundation.settings import write_settings


class DashboardProceduralSkillRegistrationTests(unittest.TestCase):
    @staticmethod
    def _row(
        *,
        action: str = "create",
        description: str = "当增量日志可能被重复扫描时，使用持久游标和原子提交避免重复工作。",
    ) -> dict:
        name = "incremental-jsonl-cursor-ingest"
        existing = action == "extend"
        return {
            "schema": "actanara.skill-asset-ledger.v1",
            "businessDate": "2026-08-12",
            "promptVersion": "minimal-v21",
            "assetClass": "skill",
            "disposition": f"library-{action}",
            "candidateId": "candidate-001",
            "reviewId": "review-001",
            "originalDecision": "skill",
            "title": "使用事务游标进行增量日志摄取",
            "summary": "持久游标避免重复读取已经摄取的日志。",
            "reason": "已执行增量读取并观察到热读零正文。",
            "evidence": ["private-locator"],
            "evidenceRecords": [1, 2, 3],
            "scores": {"evidence": 5, "value": 5, "reuse": 5, "program": 5},
            "completion": "verified",
            "completionReason": "动作后观察到直接结果。",
            "portfolioReason": "它是一条可独立调用的程序。",
            "libraryAction": action,
            "existingAssetId": registration._canonical_asset_id(name) if existing else None,
            "existingSkillName": name if existing else None,
            "skillName": name,
            "skillDescription": description,
            "skillMarkdown": f"""---
name: {name}
description: {description}
---

# 增量摄取 JSONL

## Trigger
日志重复扫描造成持续放大。

## Procedure
读取已提交游标之后的记录，并在同一事务中提交事件与新游标。

## Pitfalls
文件替换或截断时不要沿用旧偏移。

## Verification
相同快照再次读取时不读取正文。
""",
        }

    @staticmethod
    def _write_ledger(home: Path, row: dict) -> Path:
        root = home / "artifacts" / "skills"
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        path = root / "skill-harness-minimal-v21-assets-2026-08-12.jsonl"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_create_registers_canonical_and_detected_agents_without_overwriting_custom_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            paths = initialize_home(base / "Actanara")
            codex_root = base / "codex-skills"
            hermes_root = base / "hermes-skills"
            write_settings(
                {
                    "externalTools": {
                        "codex": {"skillsRoot": str(codex_root)},
                        "hermes": {"skillsRoot": str(hermes_root)},
                    }
                },
                paths,
            )
            row = self._row()
            self._write_ledger(paths.home, row)
            custom = codex_root / row["skillName"] / "SKILL.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("custom user skill\n", encoding="utf-8")

            with patch.object(
                registration,
                "detected_external_tool_ids",
                return_value=("codex", "hermes", "opencode"),
            ):
                result = registration.register_finalized_skill_assets(
                    {
                        "businessDate": "2026-08-12",
                        "promptVersion": "minimal-v21",
                        "reviewIds": ["review-001"],
                    },
                    paths=paths,
                )

            canonical = paths.home / "assets" / "skills" / row["skillName"] / "SKILL.md"
            hermes = hermes_root / row["skillName"] / "SKILL.md"
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["registrationRequested"])
            self.assertEqual(result["eligibleTools"], ["codex", "hermes"])
            self.assertEqual(result["registeredTools"], ["hermes"])
            self.assertEqual(custom.read_text(encoding="utf-8"), "custom user skill\n")
            self.assertEqual(canonical.read_text(encoding="utf-8"), hermes.read_text(encoding="utf-8"))
            self.assertTrue(
                registration._verified_managed(
                    canonical.read_text(encoding="utf-8"), name=row["skillName"]
                )
            )
            self.assertEqual(stat.S_IMODE(canonical.stat().st_mode), 0o600)
            external = result["results"][0]["externalResults"]
            self.assertEqual(
                {item["tool"]: item["result"] for item in external},
                {"codex": "preserved-customized", "hermes": "created"},
            )

    def test_extend_updates_only_verified_managed_copies_and_keeps_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            paths = initialize_home(base / "Actanara")
            codex_root = base / "codex-skills"
            write_settings(
                {"externalTools": {"codex": {"skillsRoot": str(codex_root)}}},
                paths,
            )
            ledger = self._write_ledger(paths.home, self._row())
            request = {
                "businessDate": "2026-08-12",
                "promptVersion": "minimal-v21",
                "reviewIds": ["review-001"],
            }
            with patch.object(
                registration, "detected_external_tool_ids", return_value=("codex",)
            ):
                first = registration.register_finalized_skill_assets(request, paths=paths)
                extended = self._row(
                    action="extend",
                    description="当增量日志存在替换、截断或崩溃边界时，使用身份绑定游标和原子提交安全续读。",
                )
                ledger.write_text(
                    json.dumps(extended, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                ledger.chmod(0o600)
                second = registration.register_finalized_skill_assets(request, paths=paths)

            self.assertEqual(first["results"][0]["canonicalResult"], "created")
            self.assertEqual(second["results"][0]["canonicalResult"], "updated")
            self.assertEqual(
                second["results"][0]["externalResults"][0]["result"], "updated"
            )
            canonical = (
                paths.home
                / "assets"
                / "skills"
                / extended["skillName"]
                / "SKILL.md"
            )
            external = codex_root / extended["skillName"] / "SKILL.md"
            self.assertIn("身份绑定游标", canonical.read_text(encoding="utf-8"))
            self.assertEqual(canonical.read_text(encoding="utf-8"), external.read_text(encoding="utf-8"))
            backups = list(
                (paths.state_dir / "backups" / "procedural-skill-registration").glob(
                    "*/**/SKILL.md"
                )
            )
            self.assertEqual(len(backups), 2)

    def test_external_write_failure_rolls_back_new_canonical_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            paths = initialize_home(base / "Actanara")
            codex_root = base / "codex-skills"
            write_settings(
                {"externalTools": {"codex": {"skillsRoot": str(codex_root)}}},
                paths,
            )
            row = self._row()
            self._write_ledger(paths.home, row)
            request = {
                "businessDate": "2026-08-12",
                "promptVersion": "minimal-v21",
                "reviewIds": ["review-001"],
            }
            original_apply = registration._apply
            calls = 0

            def fail_external(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic external failure")
                return original_apply(*args, **kwargs)

            with (
                patch.object(
                    registration,
                    "detected_external_tool_ids",
                    return_value=("codex",),
                ),
                patch.object(registration, "_apply", side_effect=fail_external),
            ):
                with self.assertRaisesRegex(OSError, "synthetic external failure"):
                    registration.register_finalized_skill_assets(request, paths=paths)

            canonical = (
                paths.home / "assets" / "skills" / row["skillName"] / "SKILL.md"
            )
            external = codex_root / row["skillName"] / "SKILL.md"
            self.assertFalse(canonical.exists())
            self.assertFalse(external.exists())


if __name__ == "__main__":
    unittest.main()
