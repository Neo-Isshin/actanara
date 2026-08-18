import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from diary_generator import skill_pass_minimal_harness as harness
from diary_generator import skill_pass_minimal_support as support


def _stream() -> str:
    return "\n\n".join(
        f"[record {index:06d} | thread=thread-0001 | role=assistant]\n{text}"
        for index, text in enumerate(("goal", "obstacle", "turn", "result"), start=1)
    )


def _discovery() -> str:
    return f"""{support.DISCOVERY_MARKER}
# 用双端证据诊断不对称路径
Records: 000001, 000002, 000003, 000004
Why: 单端成功误导归因，改用对照路径和双端观察后确认返回方向丢失。
"""


def _decision(decision: str = "skill", *, evidence: str = "000001, 000002, 000003, 000004") -> str:
    return f"""{harness.REVIEW_MARKER}
# 对照路径并验证双向流量
Candidate-ID: candidate-001
Decision: {decision}
Evidence-Records: {evidence}
Action-Records: {"000003" if decision == "skill" else "none"}
Verification-Records: {"000004" if decision == "skill" else "none"}
Scores: Evidence 5/5 · Value 4/5 · Reuse 5/5 · Program {"5" if decision == "skill" else "2"}/5
Reason: 记录显示同一目标下的困难、决定性转折和观察结果。
"""


def _completion(
    completion: str = "verified",
    *,
    review_id: str = "review-001",
) -> str:
    action_state = "incomplete" if completion == "incomplete" else "completed"
    result_state = "missing" if completion == "incomplete" else "observed"
    program_count = "multiple" if completion == "bundled" else "one"
    return f"""{harness.COMPLETION_MARKER}
Review-ID: {review_id}
Action-State: {action_state}
Result-State: {result_state}
Program-Count: {program_count}
Completion: {completion}
Reason: 已完成的动作之后出现了直接结果，形成单一因果程序。
"""


def _portfolio(*, review_id: str = "review-001") -> str:
    return f"""{harness.PORTFOLIO_MARKER}
Review-ID: {review_id}
Why-Standalone: 这是一条有独立触发条件、决定性机制和完成标准的程序。
"""


def _skill() -> str:
    return f"""{harness.PROPOSAL_MARKER}
Review-ID: review-001
Library-Action: create
Existing-Skill: none
Library-Reason: 现有库没有覆盖这套诊断程序。
---
name: diagnose-asymmetric-paths
description: 在相同目标经不同方向产生相反结果时，用受控对照与双端观察隔离故障方向。
---

# 诊断不对称路径

## Trigger
相同目标经不同路径或方向产生相反结果。

## Procedure
固定目标和协议，逐条改变一条路径变量，并在请求端与响应端同时观察。

## Pitfalls
不要把替代路径成功解释为原路径健康。

## Verification
重复对照稳定显示请求到达，而响应只在特定方向丢失。
"""


def _asset(*, modifiable: bool = True) -> harness.ExistingSkillAsset:
    return harness.ExistingSkillAsset(
        asset_id="asset-111111111111",
        name="diagnose-asymmetric-paths",
        description="Use controlled comparisons to diagnose asymmetric network paths.",
        source="test",
        body="""---
name: diagnose-asymmetric-paths
description: Use controlled comparisons to diagnose asymmetric network paths.
---

# Diagnose asymmetric paths

## Trigger
Traffic succeeds in one direction but not the other.

## Procedure
Compare both directions while holding the target stable.

## Pitfalls
Do not treat one successful route as proof that both directions work.

## Verification
Observe the request and response at both ends.
""",
        modifiable=modifiable,
    )


class MinimalHarnessTests(unittest.TestCase):
    def test_adjudication_completion_and_crystallization_use_high_thinking(self):
        response = type("Response", (), {"text": "ok"})()
        with patch.object(harness, "execute_llm_message", return_value=response) as call:
            for stage in (
                "adjudication",
                "completion-audit",
                "portfolio",
                "crystallization",
            ):
                harness.call_harness_llm(
                    "prompt",
                    system="system",
                    stage=stage,
                    source="source",
                    paths=object(),
                )
                self.assertEqual(call.call_args.kwargs["thinking_mode"], "high")

    def test_prompt_contract_retains_core_judgment(self):
        for term in ("Evidence", "Value", "Reuse", "Program"):
            self.assertIn(term, harness.ADJUDICATION_PROMPT)
        for decision in ("skill", "lesson", "reference", "discard"):
            self.assertIn(f"`{decision}`", harness.ADJUDICATION_PROMPT)
        self.assertNotIn("Scope=", harness.ADJUDICATION_PROMPT)
        self.assertNotIn("Novelty=", harness.ADJUDICATION_PROMPT)
        self.assertNotIn("事务 owner", harness.ADJUDICATION_PROMPT)
        self.assertNotIn("traceroute", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("作用域", harness.ADJUDICATION_PROMPT)
        self.assertIn("完整性复核", harness.ADJUDICATION_PROMPT)
        self.assertIn("VPS 或单一项目不是自动排除条件", harness.DISCOVERY_PROMPT)
        self.assertIn(harness.DISCOVERY_NONE_MARKER, harness.DISCOVERY_PROMPT)
        self.assertIn("基线测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("执行测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("完成态测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("Skill 完成硬门", harness.ADJUDICATION_PROMPT)
        self.assertIn("不得一边承认证据缺口，一边仍判 Skill", harness.ADJUDICATION_PROMPT)
        self.assertIn("仅仅定位根因、提出修法、开始实施或声明稍后验证", harness.ADJUDICATION_PROMPT)
        self.assertIn("诊断步骤实际执行", harness.ADJUDICATION_PROMPT)
        self.assertIn("Verification-Records 必须包含该动作之后的完成态观察", harness.ADJUDICATION_PROMPT)
        self.assertIn("程序测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("独立资产测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("压缩测试", harness.ADJUDICATION_PROMPT)
        self.assertIn("ASCII 小写 kebab-case", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("调用触发契约", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("当【可观察触发状态】时", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("仅凭 `description` 判断是否调用", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("用它反向裁剪正文", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("只保留证据最强且可独立闭合的一条程序", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("Procedure 用 2–6 个有序步骤", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("闭卷证据测试", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("{authority_boundary}", harness.CRYSTALLIZATION_PROMPT)
        self.assertIn("完成态与批内资产组合已经独立核验", harness.CRYSTALLIZATION_SYSTEM)
        self.assertIn("人工覆盖不代表完成态", harness.HUMAN_OVERRIDE_CRYSTALLIZATION_SYSTEM)
        self.assertIn("单独调用", harness.PORTFOLIO_PROMPT)
        self.assertIn("不要追求数量", harness.PORTFOLIO_PROMPT)
        self.assertIn("Skill Promotion", harness.PORTFOLIO_PROMPT)
        self.assertIn("后续结果", harness.COMPLETION_PROMPT)
        self.assertIn("Action-State", harness.COMPLETION_PROMPT)
        self.assertIn("Result-State", harness.COMPLETION_PROMPT)
        self.assertIn("Program-Count", harness.COMPLETION_PROMPT)
        self.assertIn("`incomplete`", harness.COMPLETION_PROMPT)
        self.assertIn("`bundled`", harness.COMPLETION_PROMPT)
        for action in ("create", "extend", "covered", "conflict", "reject"):
            self.assertIn(f"`{action}`", harness.CRYSTALLIZATION_PROMPT)

    def test_adjudication_preserves_four_model_authored_asset_classes(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        prompt = harness.build_adjudication_prompt([source], evidence_stream=stream)
        self.assertEqual(prompt.count("[record 000002"), 1)
        self.assertNotIn("<allowed_work_records>", prompt)
        for decision in ("skill", "lesson", "reference", "discard"):
            rows, rejected = harness.parse_adjudication_output(
                _decision(decision),
                stream=stream,
                candidates=[source],
            )
            self.assertEqual((len(rows), rejected), (1, 0), decision)
            self.assertEqual(rows[0].decision, decision)

    def test_one_marker_can_prefix_multiple_complete_review_cards(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        second = _decision("lesson").replace(
            harness.REVIEW_MARKER + "\n", "", 1
        ).replace("candidate-001", "candidate-002")
        rows, rejected = harness.parse_adjudication_output(
            _decision() + "\n\n" + second,
            stream=stream,
            candidates=[source, source],
        )
        self.assertEqual((len(rows), rejected), (2, 0))
        self.assertEqual([row.decision for row in rows], ["skill", "lesson"])
        self.assertEqual(rows[0].title, source.title)

    def test_one_discovery_marker_can_prefix_multiple_records_cards(self):
        stream = _stream()
        second = """# 第二个独立候选
Records: 000002, 000003, 000004
Why: 第二条完整因果线索具有独立记录集合。"""
        rows, rejected = harness.parse_discovery_output(
            _discovery() + "\n\n" + second,
            stream=stream,
        )
        self.assertEqual((len(rows), rejected), (2, 0))
        self.assertEqual(rows[1].record_ids, (2, 3, 4))
        self.assertEqual(rows[1].title, "第二个独立候选")

        none_rows, none_rejected = harness.parse_discovery_output(
            f"{harness.DISCOVERY_NONE_MARKER}\nReason: 完整扫描后没有符合门槛的经历。",
            stream=stream,
        )
        self.assertEqual((none_rows, none_rejected), ([], 0))

        mixed_rows, mixed_rejected = harness.parse_discovery_output(
            f"{harness.DISCOVERY_NONE_MARKER}\nReason: 无候选。\n\n{_discovery()}",
            stream=stream,
        )
        self.assertEqual((mixed_rows, mixed_rejected), ([], 1))

    def test_completion_audit_keeps_verified_and_blocks_incomplete(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        decisions, rejected = harness.parse_adjudication_output(
            _decision()
            + "\n\n"
            + _decision().replace("candidate-001", "candidate-002"),
            stream=stream,
            candidates=[source, source],
        )
        self.assertEqual((len(decisions), rejected), (2, 0))
        raw = _completion() + "\n\n" + _completion(
            "incomplete", review_id="review-002"
        ).replace(harness.COMPLETION_MARKER + "\n", "", 1)
        audits, rejected = harness.parse_completion_output(raw, decisions=decisions)
        self.assertEqual((len(audits), rejected), (2, 0))
        self.assertEqual(
            [(row.review_id, row.completion) for row in audits],
            [("review-001", "verified"), ("review-002", "incomplete")],
        )

        markerless = raw.replace(harness.COMPLETION_MARKER + "\n", "")
        markerless_audits, markerless_rejected = harness.parse_completion_output(
            markerless,
            decisions=decisions,
        )
        self.assertEqual((len(markerless_audits), markerless_rejected), (2, 0))

        inconsistent = _completion().replace(
            "Program-Count: one", "Program-Count: multiple"
        )
        inconsistent_audits, inconsistent_rejected = harness.parse_completion_output(
            inconsistent,
            decisions=decisions[:1],
        )
        self.assertEqual((inconsistent_audits, inconsistent_rejected), ([], 1))

        portfolio = _portfolio() + "\n\n" + _portfolio(
            review_id="review-002"
        ).replace(harness.PORTFOLIO_MARKER + "\n", "", 1)
        keeps, invalid = harness.parse_portfolio_output(
            portfolio,
            decisions=decisions,
        )
        self.assertEqual((len(keeps), invalid), (2, 0))
        self.assertEqual([row.review_id for row in keeps], ["review-001", "review-002"])

    def test_selected_dossier_prompt_carries_only_its_evidence_records(self):
        stream = _stream()
        first_raw = _discovery().replace(
            "Records: 000001, 000002, 000003, 000004",
            "Records: 000001, 000002",
        )
        second_raw = (
            _discovery()
            .replace("# 用双端证据诊断不对称路径", "# 第二个独立候选")
            .replace(
                "Records: 000001, 000002, 000003, 000004",
                "Records: 000003, 000004",
            )
        )
        candidates, rejected = support.parse_discovery_output(
            first_raw + "\n\n" + second_raw,
            stream=stream,
        )
        self.assertEqual((len(candidates), rejected), (2, 0))
        selected_stream = support.select_cited_records(stream, [candidates[1]])
        prompt = harness.build_adjudication_prompt(
            candidates,
            evidence_stream=selected_stream,
            candidate_numbers=(2,),
        )
        self.assertNotIn("[record 000001", prompt)
        self.assertIn("[record 000003", prompt)

    def test_adjudication_rejects_cross_dossier_evidence_but_not_low_scores(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        invalid, rejected = harness.parse_adjudication_output(
            _decision(evidence="000001, 000099"),
            stream=stream,
            candidates=[source],
        )
        self.assertEqual((invalid, rejected), ([], 1))

        low_score = _decision().replace("Value 4/5", "Value 2/5")
        rows, rejected = harness.parse_adjudication_output(
            low_score,
            stream=stream,
            candidates=[source],
        )
        self.assertEqual((len(rows), rejected), (1, 0))
        self.assertEqual(rows[0].scores.value, 2)
        self.assertFalse(rows[0].crystallizable)

    def test_crystallization_model_does_not_repeat_evidence_or_scores(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        decisions, rejected = harness.parse_adjudication_output(
            _decision(), stream=stream, candidates=[source]
        )
        self.assertEqual(rejected, 0)
        prompt = harness.build_crystallization_prompt(decisions, evidence_stream=stream)
        self.assertIn("已通过独立完成态和批内资产组合核验", prompt)
        self.assertNotIn("用户只覆盖了“允许尝试结晶”", prompt)
        self.assertIn("模型只需复制 Review-ID、选择库动作", prompt)
        self.assertEqual(prompt.count("[record 000002"), 1)
        self.assertIn("Action-Records: 000003", prompt)
        self.assertNotIn("<action_records>", prompt)
        self.assertNotIn("Evidence:", harness.CRYSTALLIZATION_PROMPT)
        self.assertNotIn("Scores:", harness.CRYSTALLIZATION_PROMPT)

        skills, rejected = harness.parse_crystallization_output(
            _skill(), stream=stream, decisions=decisions
        )
        self.assertEqual((len(skills), rejected), (1, 0))
        self.assertEqual(skills[0].evidence, ("thread-0001:000001-000004",))
        self.assertEqual(skills[0].scores.learning_value, 4)

        without_marker = _skill().replace(harness.PROPOSAL_MARKER + "\n", "", 1)
        skills, rejected = harness.parse_crystallization_output(
            without_marker, stream=stream, decisions=decisions
        )
        self.assertEqual((len(skills), rejected), (1, 0))

    def test_only_skill_decisions_reach_crystallization(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        decisions, rejected = harness.parse_adjudication_output(
            _decision("reference"), stream=stream, candidates=[source]
        )
        self.assertEqual(rejected, 0)
        prompt = harness.build_crystallization_prompt(decisions, evidence_stream=stream)
        self.assertIn("<approved_skill_dossiers>\n\n</approved_skill_dossiers>", prompt)
        skills, rejected = harness.parse_crystallization_output(
            "", stream=stream, decisions=decisions
        )
        self.assertEqual((skills, rejected), ([], 0))

    def test_user_forced_lesson_reaches_only_targeted_crystallization(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        lesson_raw = _decision().replace("Decision: skill", "Decision: lesson")
        lesson_raw = lesson_raw.replace("Value 4/5", "Value 2/5")
        decisions, rejected = harness.parse_adjudication_output(
            lesson_raw,
            stream=stream,
            candidates=[source],
        )
        self.assertEqual(rejected, 0)
        self.assertEqual(decisions[0].decision, "lesson")
        self.assertFalse(decisions[0].crystallizable)

        normal_prompt = harness.build_crystallization_prompt(
            decisions,
            evidence_stream=stream,
        )
        self.assertNotIn(
            '<approved_skill_dossier id="review-001">',
            normal_prompt,
        )
        normal_skills, normal_rejected = harness.parse_crystallization_output(
            _skill(),
            stream=stream,
            decisions=decisions,
        )
        self.assertEqual((normal_skills, normal_rejected), ([], 1))

        forced_prompt = harness.build_crystallization_prompt(
            decisions,
            evidence_stream=stream,
            forced_review_ids=("review-001",),
        )
        self.assertIn("Review-ID: review-001", forced_prompt)
        self.assertIn(
            '<approved_skill_dossier id="review-001">',
            forced_prompt,
        )
        self.assertIn("Authority: user-forced-lesson", forced_prompt)
        self.assertIn("用户只覆盖了“允许尝试结晶”", forced_prompt)
        self.assertIn("没有因此获得完成态、独立资产价值、批内组合或注册权威", forced_prompt)
        self.assertNotIn(
            "进入本阶段的 dossier 已通过独立完成态和批内资产组合核验",
            forced_prompt,
        )
        forced_skills, forced_rejected = harness.parse_crystallization_output(
            _skill(),
            stream=stream,
            decisions=decisions,
            forced_review_ids=("review-001",),
        )
        self.assertEqual((len(forced_skills), forced_rejected), (1, 0))
        self.assertEqual(forced_skills[0].scores.learning_value, 2)

    def test_replayed_pipeline_attaches_host_owned_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                result = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    discovery_raw_override=_discovery(),
                    adjudication_raw_override=_decision(),
                    completion_raw_override=_completion(),
                    portfolio_raw_override=_portfolio(),
                    crystallization_raw_override=_skill(),
                    library_assets_override=(),
                )

            self.assertEqual(result.llm_calls, 0)
            self.assertEqual(len(result.skills), 1)
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("Decision: skill", report)
            self.assertIn("Evidence: thread-0001:000001-000004", report)
            self.assertIn("Skill Library Proposals", report)
            self.assertEqual(result.proposals[0].action, "create")
            self.assertEqual(
                [(item.asset_class, item.disposition) for item in result.asset_ledger],
                [("skill", "library-create")],
            )
            ledger_rows = [
                json.loads(line)
                for line in result.asset_ledger_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(ledger_rows), 1)
            self.assertEqual(ledger_rows[0]["schema"], harness.ASSET_LEDGER_SCHEMA)
            self.assertEqual(ledger_rows[0]["assetClass"], "skill")
            self.assertEqual(
                ledger_rows[0]["skillName"], "diagnose-asymmetric-paths"
            )
            for heading in (
                "### Skill Proposals",
                "### Lessons",
                "### References",
                "### Discarded Retrieval Records",
            ):
                self.assertIn(heading, report)

    def test_asset_ledger_preserves_model_classes_and_deterministic_demotions(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        base = harness.parse_adjudication_output(
            _decision(), stream=stream, candidates=[source]
        )[0][0]
        explicit = [
            base,
            replace(
                base,
                candidate_id="candidate-002",
                review_id="review-002",
                decision="lesson",
            ),
            replace(
                base,
                candidate_id="candidate-003",
                review_id="review-003",
                decision="reference",
            ),
            replace(
                base,
                candidate_id="candidate-004",
                review_id="review-004",
                decision="discard",
            ),
        ]
        proposal = harness.SkillProposal(
            review_id="review-001",
            action="create",
            existing_asset_id=None,
            existing_skill_name=None,
            reason="库中没有覆盖该程序。",
            skill=support.parse_skill_candidate(
                "\n".join(
                    [
                        support.SKILL_MARKER,
                        f"Evidence: {', '.join(base.evidence)}",
                        base.scores.skill_scores().markdown(),
                        "",
                        _skill().split("Library-Reason:", 1)[1].split("\n", 1)[1],
                    ]
                ),
                stream=stream,
            ),
        )
        ledger = harness.build_asset_ledger(
            (source, source, source, source),
            explicit,
            (harness.parse_completion_output(_completion(), decisions=[base])[0][0],),
            (harness.PortfolioKeep("review-001", "独立程序。"),),
            (proposal,),
        )
        self.assertEqual(
            [item.asset_class for item in ledger],
            ["skill", "lesson", "reference", "discard"],
        )
        self.assertTrue(all(item.original_decision == expected for item, expected in zip(
            ledger, ("skill", "lesson", "reference", "discard")
        )))

        incomplete = replace(
            harness.parse_completion_output(_completion(), decisions=[base])[0][0],
            completion="incomplete",
            action_state="incomplete",
            result_state="missing",
        )
        demoted = harness.build_asset_ledger(
            (source,), (base,), (incomplete,), (), ()
        )[0]
        self.assertEqual(
            (demoted.asset_class, demoted.disposition),
            ("lesson", "completion-incomplete"),
        )

    def test_incomplete_completion_audit_never_reaches_crystallization(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                result = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    discovery_raw_override=_discovery(),
                    adjudication_raw_override=_decision(),
                    completion_raw_override=_completion("incomplete"),
                    crystallization_raw_override=_skill(),
                    library_assets_override=(),
                )

            self.assertEqual(result.llm_calls, 0)
            self.assertEqual(len(result.completion_audits), 1)
            self.assertEqual(result.completion_audits[0].completion, "incomplete")
            self.assertEqual(result.proposals, ())
            self.assertEqual(result.skills, ())
            self.assertIsNone(result.crystallization_raw_path)

            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                portfolio_none = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    discovery_raw_override=_discovery(),
                    adjudication_raw_override=_decision(),
                    completion_raw_override=_completion(),
                    portfolio_raw_override="",
                    crystallization_raw_override=_skill(),
                    library_assets_override=(),
                )
            self.assertEqual(portfolio_none.llm_calls, 0)
            self.assertEqual(portfolio_none.portfolio_keeps, ())
            self.assertEqual(portfolio_none.proposals, ())
            self.assertEqual(portfolio_none.skills, ())
            self.assertIsNone(portfolio_none.crystallization_raw_path)

    def test_live_run_keeps_partial_adjudication_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            discovery = _discovery() + "\n\n" + _discovery().replace(
                "# 用双端证据诊断不对称路径",
                "# 第二个独立候选",
            )
            calls: list[str] = []

            def fake_llm(prompt, **kwargs):
                calls.append(kwargs["stage"])
                if kwargs["stage"] == "adjudication":
                    return _decision()
                if kwargs["stage"] == "completion-audit":
                    return _completion()
                if kwargs["stage"] == "portfolio":
                    return _portfolio()
                return _skill()

            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                result = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=discovery,
                    library_assets_override=(),
                )

            self.assertEqual(
                calls,
                ["adjudication", "completion-audit", "portfolio", "crystallization"],
            )
            self.assertEqual(result.llm_calls, 4)
            self.assertEqual(result.rejected_decisions, 1)
            self.assertEqual([row.decision for row in result.decisions], ["skill"])
            self.assertIsNone(result.adjudication_repair_raw_path)
            self.assertIn(
                "Adjudication stage recovery attempted: no",
                result.report_path.read_text(encoding="utf-8"),
            )

    def test_live_run_keeps_partial_crystallization_without_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            discovery = _discovery() + "\n\n" + _discovery().replace(
                "# 用双端证据诊断不对称路径",
                "# 第二个独立候选",
            )
            reviews = _decision() + "\n\n" + _decision().replace(
                "candidate-001", "candidate-002"
            )
            calls: list[str] = []

            def fake_llm(prompt, **kwargs):
                calls.append(kwargs["stage"])
                self.assertEqual(kwargs["stage"], "crystallization")
                return _skill()

            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                result = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=discovery,
                    adjudication_raw_override=reviews,
                    completion_raw_override=(
                        _completion()
                        + "\n\n"
                        + _completion(review_id="review-002")
                    ),
                    portfolio_raw_override=(
                        _portfolio() + "\n\n" + _portfolio(review_id="review-002")
                    ),
                    library_assets_override=(),
                )

            self.assertEqual(calls, ["crystallization"])
            self.assertEqual(len(result.skills), 1)
            self.assertEqual(result.rejected_skills, 1)
            self.assertIsNone(result.crystallization_repair_raw_path)
            self.assertIn(
                "Crystallization stage recovery attempted: no",
                result.report_path.read_text(encoding="utf-8"),
            )

    def test_live_run_recovers_once_when_each_stage_has_zero_valid_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            discovery = _discovery() + "\n\n" + _discovery().replace(
                "# 用双端证据诊断不对称路径",
                "# 第二个独立候选",
            )
            repaired_decisions = _decision() + "\n\n" + _decision().replace(
                "candidate-001", "candidate-002"
            )
            repaired_skills = _skill() + "\n\n" + (
                _skill()
                .replace("review-001", "review-002")
                .replace("diagnose-asymmetric-paths", "verify-asymmetric-paths")
            )
            calls: list[str] = []

            def fake_llm(prompt, **kwargs):
                stage = kwargs["stage"]
                calls.append(stage)
                if stage == "adjudication":
                    return "malformed"
                if stage == "adjudication-repair":
                    self.assertIn("candidate-001, candidate-002", prompt)
                    return repaired_decisions
                if stage == "crystallization":
                    return "malformed"
                self.assertEqual(stage, "crystallization-repair")
                self.assertIn("review-001, review-002", prompt)
                return repaired_skills

            with patch.object(
                harness,
                "resolve_llm_provider",
                return_value={"pipelineGateTokens": 80000},
            ):
                result = harness.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=discovery,
                    completion_raw_override=(
                        _completion()
                        + "\n\n"
                        + _completion(review_id="review-002")
                    ),
                    portfolio_raw_override=(
                        _portfolio() + "\n\n" + _portfolio(review_id="review-002")
                    ),
                    library_assets_override=(),
                )

            self.assertEqual(
                calls,
                [
                    "adjudication",
                    "adjudication-repair",
                    "crystallization",
                    "crystallization-repair",
                ],
            )
            self.assertEqual(result.llm_calls, 4)
            self.assertEqual(len(result.decisions), 2)
            self.assertEqual(len(result.skills), 2)
            self.assertEqual((result.rejected_decisions, result.rejected_skills), (0, 0))
            self.assertIsNotNone(result.adjudication_repair_raw_path)
            self.assertIsNotNone(result.crystallization_repair_raw_path)

    def test_live_run_recovers_discovery_only_after_structural_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
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
                            "conversationId": "a",
                        }
                    )
                    for index, text in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            result = harness.run_skill_pass(
                "2026-08-12",
                paths=paths,
                discovery_raw_override="malformed",
                discovery_repair_raw_override=_discovery(),
                adjudication_raw_override=_decision("lesson"),
                library_assets_override=(),
            )
            self.assertEqual(len(result.discoveries), 1)
            self.assertEqual([row.decision for row in result.decisions], ["lesson"])
            self.assertIsNotNone(result.discovery_repair_raw_path)
            self.assertIn(
                "Discovery stage recovery attempted: yes",
                result.report_path.read_text(encoding="utf-8"),
            )

    def test_library_actions_are_model_authored_but_structurally_bounded(self):
        stream = _stream()
        source = support.parse_discovery_output(_discovery(), stream=stream)[0][0]
        decisions, _ = harness.parse_adjudication_output(
            _decision(), stream=stream, candidates=[source]
        )
        asset = _asset()
        matches = {
            "review-001": (harness.SkillLibraryMatch(asset, body_included=True),)
        }
        library_text, effective = harness.prepare_library_prompt(matches)
        prompt = harness.build_crystallization_prompt(
            decisions,
            evidence_stream=stream,
            library_text=library_text,
            library_matches=effective,
        )
        self.assertIn(asset.asset_id, prompt)
        self.assertIn("modifiable=\"yes\"", prompt)
        self.assertIn("<existing_skill_body>", prompt)

        covered = "\n".join(
            (
                harness.PROPOSAL_MARKER,
                "Review-ID: review-001",
                "Library-Action: covered",
                f"Existing-Skill: {asset.asset_id}",
                "Library-Reason: 现有资产已经覆盖相同触发、步骤和验证。",
            )
        )
        proposals, rejected = harness.parse_crystallization_proposals(
            covered,
            stream=stream,
            decisions=decisions,
            library_matches=effective,
        )
        self.assertEqual((len(proposals), rejected), (1, 0))
        self.assertEqual(proposals[0].action, "covered")
        self.assertIsNone(proposals[0].skill)

        conflict = "\n".join(
            (
                harness.PROPOSAL_MARKER,
                "Review-ID: review-001",
                "Library-Action: conflict",
                f"Existing-Skill: {asset.asset_id}",
                "Library-Reason: 新证据与现有完成判断冲突，需要人工处理。",
            )
        )
        proposals, rejected = harness.parse_crystallization_proposals(
            conflict,
            stream=stream,
            decisions=decisions,
            library_matches=effective,
        )
        self.assertEqual((len(proposals), rejected), (1, 0))
        self.assertEqual(proposals[0].action, "conflict")
        self.assertIsNone(proposals[0].skill)

        rejected_proposal = "\n".join(
            (
                harness.PROPOSAL_MARKER,
                "Review-ID: review-001",
                "Library-Action: reject",
                "Existing-Skill: none",
                "Library-Reason: 原始记录只有未来验证计划，不能支持 Skill 正文。",
            )
        )
        proposals, rejected = harness.parse_crystallization_proposals(
            rejected_proposal,
            stream=stream,
            decisions=decisions,
            library_matches=effective,
        )
        self.assertEqual((len(proposals), rejected), (1, 0))
        self.assertEqual(proposals[0].action, "reject")
        self.assertIsNone(proposals[0].skill)

        extended = (
            _skill()
            .replace("Library-Action: create", "Library-Action: extend")
            .replace("Existing-Skill: none", f"Existing-Skill: {asset.asset_id}")
        )
        proposals, rejected = harness.parse_crystallization_proposals(
            extended,
            stream=stream,
            decisions=decisions,
            library_matches=effective,
        )
        self.assertEqual((len(proposals), rejected), (1, 0))
        self.assertEqual(proposals[0].action, "extend")
        self.assertIsNotNone(proposals[0].skill)

        unrelated = replace(
            asset,
            asset_id="asset-222222222222",
            name="audit-unrelated-storage",
            description="Audit unrelated storage behavior.",
            body=asset.body.replace(
                "diagnose-asymmetric-paths", "audit-unrelated-storage"
            ),
        )
        unrelated_matches = {
            "review-001": (
                harness.SkillLibraryMatch(unrelated, body_included=True),
            )
        }
        proposals, rejected = harness.parse_crystallization_proposals(
            _skill(),
            stream=stream,
            decisions=decisions,
            library_matches=unrelated_matches,
        )
        self.assertEqual((len(proposals), rejected), (1, 0))
        self.assertEqual(proposals[0].action, "create")
        self.assertEqual(proposals[0].skill.name, "diagnose-asymmetric-paths")

        read_only = {
            "review-001": (
                harness.SkillLibraryMatch(_asset(modifiable=False), body_included=True),
            )
        }
        proposals, rejected = harness.parse_crystallization_proposals(
            extended,
            stream=stream,
            decisions=decisions,
            library_matches=read_only,
        )
        self.assertEqual((proposals, rejected), ([], 1))

    def test_library_loader_rejects_unsafe_files_and_keeps_safe_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            root = Path(tmp) / "skills"
            valid = root / "valid" / "SKILL.md"
            valid.parent.mkdir(parents=True)
            valid.write_text(_asset().body, encoding="utf-8")
            linked = root / "linked" / "SKILL.md"
            linked.parent.mkdir(parents=True)
            linked.symlink_to(valid)
            hardlinked = root / "hardlinked" / "SKILL.md"
            hardlinked.parent.mkdir(parents=True)
            hardlinked.hardlink_to(valid)
            private = root / "private" / "SKILL.md"
            private.parent.mkdir(parents=True)
            private.write_text(
                _asset().body.replace(
                    "diagnose-asymmetric-paths",
                    "private-runtime-path",
                )
                + "\nUse /Users/private-name/secret/file.\n",
                encoding="utf-8",
            )

            assets = harness.load_existing_skill_library(
                paths,
                roots=(harness.SkillLibraryRoot("test", root, True),),
            )
            # The hard link makes both aliases unsafe; the private file is withheld.
            self.assertEqual(assets, ())

            hardlinked.unlink()
            assets = harness.load_existing_skill_library(
                paths,
                roots=(harness.SkillLibraryRoot("test", root, True),),
            )
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0].name, "diagnose-asymmetric-paths")
            self.assertNotIn(
                str(valid), assets[0].manifest(include_body=False, review_ids=[])
            )


if __name__ == "__main__":
    unittest.main()
