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
from diary_generator import skill_pass_single_call as single
from diary_generator import skill_pass_three_call as three
from diary_generator import skill_pass_two_call as two


def _stream() -> str:
    return "\n\n".join(
        f"[record {index:06d} | thread=thread-0001 | role=assistant]\n{text}"
        for index, text in enumerate(("goal", "obstacle", "turn", "result"), start=1)
    )


def _discovery() -> str:
    return f"""{two.DISCOVERY_MARKER}
# 用双端证据诊断不对称路径
Records: 000001, 000002, 000003, 000004
Why: 单端成功误导归因，改用对照路径和双端观察后确认返回方向丢失。
"""


def _review(
    decision: str = "skill",
    *,
    scope: str = "domain-work",
    action_state: str | None = None,
    verification_state: str | None = None,
    novelty: str = "nontrivial",
    evidence_records: str = "000001, 000002, 000003, 000004",
    action_records: str = "000003",
    verification_records: str = "000004",
) -> str:
    resolved_action_state = action_state or (
        "executed" if action_records != "none" else "none"
    )
    resolved_verification_state = verification_state or (
        "behavior-observed" if verification_records != "none" else "none"
    )
    return f"""{three.REVIEW_MARKER}
# 对照路径并验证双向流量
Candidate-ID: candidate-001
Scope: {scope}
Action-State: {resolved_action_state}
Verification-State: {resolved_verification_state}
Novelty: {novelty}
Decision: {decision}
Evidence-Records: {evidence_records}
Action-Records: {action_records}
Verification-Records: {verification_records}
Scores: Evidence Closure 5/5 · Learning Value 4/5 · Transferability 5/5
Procedure Readiness: 5/5
Reason: 记录形成同一目标下的困难、转折和行为结果；单端成功的早期解释被双端观察否决。
"""


def _skill(
    *,
    review_id: str = "review-001",
    scope: str = "domain-work",
    action_state: str = "executed",
    verification_state: str = "behavior-observed",
    novelty: str = "nontrivial",
    scores: str | None = None,
    evidence: str = "thread-0001:000001-000004",
    evidence_records: str = "000001, 000002, 000003, 000004",
    action_records: str = "000003",
    verification_records: str = "000004",
) -> str:
    score_line = scores or "Scores: Evidence Closure 5/5 · Learning Value 4/5 · Transferability 5/5"
    return f"""{single.SKILL_MARKER}
Review-ID: {review_id}
Evidence: {evidence}
Scope: {scope}
Action-State: {action_state}
Verification-State: {verification_state}
Novelty: {novelty}
Evidence-Records: {evidence_records}
Action-Records: {action_records}
Verification-Records: {verification_records}
{score_line}

---
name: diagnose-asymmetric-paths
description: 在相同目标经不同方向或路径产生相反结果时，用受控对照和双端观察定位故障方向。
---

# 诊断不对称路径

## Trigger
同一目标经不同方向或路径呈现相反结果。

## Procedure
固定目标和协议，逐条对照路径，并在两端观察请求与响应以隔离丢失方向。

## Pitfalls
不要把一条替代路径成功解释为另一条路径健康。

## Verification
重复对照稳定显示请求到达，而响应只在特定方向丢失。
"""


class ThreeCallSkillPassTests(unittest.TestCase):
    def test_adjudication_and_crystallization_have_separate_authority(self):
        stream = _stream()
        discovery = two.parse_discovery_output(_discovery(), stream=stream)[0][0]
        review_prompt = three.build_review_prompt([discovery], evidence_stream=stream)
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        final_prompt = three.build_crystallization_prompt(reviews, evidence_stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual(reviews[0].asset_id, "review-001")
        self.assertEqual(reviews[0].candidate_id, "candidate-001")
        self.assertIn("不写最终 Skill", three.REVIEW_SYSTEM)
        self.assertIn("不写 Procedure", review_prompt)
        self.assertIn("Procedure Readiness", review_prompt)
        self.assertIn("本轮共有 1 张候选卡", review_prompt)
        self.assertIn("只裁决这些 ID：candidate-001", review_prompt)
        self.assertIn("Allowed-Records: 000001, 000002, 000003, 000004", review_prompt)
        self.assertIn("Action-Records", review_prompt)
        self.assertIn("Verification-Records", review_prompt)
        self.assertIn("Novelty=`nontrivial`", review_prompt)
        self.assertIn("仍必须是 distillation-meta", three.REVIEW_SYSTEM)
        self.assertIn("不得写 thread 或范围", review_prompt)
        self.assertIn("基线测试要评估完整程序", review_prompt)
        self.assertIn("诊断程序不因", review_prompt)
        self.assertIn("固定容量、超时、文件大小或保留槽位", review_prompt)
        self.assertIn("两个可独立触发、独立验证的机制", review_prompt)
        self.assertIn("只能 Decision=drop", three.REVIEW_SYSTEM)
        self.assertIn("Review-ID: review-001", final_prompt)
        self.assertIn("本轮共有 1 张获批 Skill 卡", final_prompt)
        self.assertIn("不得新增候选、改变决策", three.CRYSTALLIZATION_SYSTEM)
        self.assertIn("逐字复制", final_prompt)
        self.assertIn("Novelty: nontrivial", final_prompt)
        self.assertIn("Procedure 只能取自 Action-Records", final_prompt)
        self.assertIn("traceroute 不是客户端抓包", final_prompt)
        self.assertIn("严格按 `1, 2, 3...`", final_prompt)

        lesson_raw = _review("lesson").replace(
            "Procedure Readiness: 5/5",
            "Procedure Readiness: 3/5",
        )
        lessons, rejected = three.parse_review_output(lesson_raw, stream=stream)
        lesson_prompt = three.build_crystallization_prompt(lessons, evidence_stream=stream)
        self.assertIn("本轮共有 0 张获批 Skill 卡", lesson_prompt)
        self.assertIn(
            "<reviewed_asset_dossiers>\n\n</reviewed_asset_dossiers>",
            lesson_prompt,
        )

    def test_each_crystallization_dossier_contains_only_its_review_records(self):
        stream = _stream()
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        self.assertEqual(rejected, 0)
        first = replace(
            reviews[0],
            asset_id="review-001",
            evidence=("thread-0001:000001-000002",),
            evidence_records=(1, 2),
            action_records=(1,),
            verification_records=(2,),
        )
        second = replace(
            reviews[0],
            asset_id="review-002",
            candidate_id="candidate-002",
            evidence=("thread-0001:000003-000004",),
            evidence_records=(3, 4),
            action_records=(3,),
            verification_records=(4,),
        )

        prompt = three.build_crystallization_prompt(
            [first, second],
            evidence_stream=stream,
        )
        first_dossier, second_dossier = prompt.split(
            '<reviewed_asset_dossier id="review-002">'
        )

        self.assertIn("[record 000001", first_dossier)
        self.assertNotIn("[record 000003", first_dossier)
        self.assertNotIn("[record 000001", second_dossier)
        self.assertIn("[record 000003", second_dossier)
        self.assertIn("不得使用其它 dossier", prompt)
        self.assertIn("禁止相邻或同义词重复", prompt)
        self.assertIn("`Action-State=diagnostic`", prompt)
        self.assertIn("现在做真实验收", prompt)
        self.assertIn("保持测量对象精确", prompt)
        self.assertIn("<context_records>\n(none)", prompt)
        self.assertIn("<action_records>\n[record 000001", prompt)
        self.assertIn("<verification_records>\n[record 000002", prompt)
        self.assertIn("Procedure 只能使用 dossier 的 action_records", three.CRYSTALLIZATION_SYSTEM)
        self.assertIn("同一记录中与当前卡机制独立", three.CRYSTALLIZATION_SYSTEM)

    def test_recursive_meta_candidate_is_left_for_llm_adjudication(self):
        stream = _stream()
        ordinary = two.parse_discovery_output(_discovery(), stream=stream)[0][0]
        recursive = two.DiscoveryCandidate(
            title="Skill 证据权威升级",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=ordinary.evidence,
            summary="调整 Skill Promotion 与证据快照。",
        )

        prompt = three.build_review_prompt(
            [ordinary, recursive],
            evidence_stream=stream,
        )

        self.assertIn("本轮共有 2 张候选卡", prompt)
        self.assertIn("candidate-001, candidate-002", prompt)
        self.assertIn("Skill 证据权威升级", prompt)
        self.assertIn("只能 Decision=drop", three.REVIEW_SYSTEM)

    def test_each_review_dossier_contains_only_its_allowed_records(self):
        stream = _stream()
        first = two.DiscoveryCandidate(
            title="第一个机制",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=("thread-0001:000001-000002",),
            record_ids=(1, 2),
            summary="第一组证据。",
        )
        second = two.DiscoveryCandidate(
            title="第二个机制",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=("thread-0001:000003-000004",),
            record_ids=(3, 4),
            summary="第二组证据。",
        )

        prompt = three.build_review_prompt([first, second], evidence_stream=stream)
        first_dossier, second_dossier = prompt.split('<candidate_dossier id="candidate-002">')

        self.assertIn("[record 000001", first_dossier)
        self.assertIn("[record 000002", first_dossier)
        self.assertNotIn("[record 000003", first_dossier)
        self.assertNotIn("[record 000001", second_dossier)
        self.assertIn("[record 000003", second_dossier)
        self.assertIn("[record 000004", second_dossier)
        self.assertIn("唯一事实边界", prompt)
        self.assertIn("`修复会`", prompt)

    def test_crystallization_must_preserve_review_type_evidence_scores_and_card(self):
        stream = _stream()
        reviews, _ = three.parse_review_output(_review(), stream=stream)

        lessons, skills, rejected = three.parse_crystallization_output(
            _skill(), stream=stream, decisions=reviews
        )
        self.assertEqual((len(lessons), len(skills), rejected), (0, 1, 0))

        _, changed, rejected = three.parse_crystallization_output(
            _skill(scores="Scores: Evidence Closure 5/5 · Learning Value 5/5 · Transferability 5/5"),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual(changed, [])
        self.assertEqual(rejected, 1)

        _, missing, rejected = three.parse_crystallization_output(
            _skill(review_id="review-999"), stream=stream, decisions=reviews
        )
        self.assertEqual(missing, [])
        self.assertEqual(rejected, 2)

    def test_skill_decision_must_satisfy_the_llm_authored_score_contract(self):
        raw = _review().replace(
            "Transferability 5/5",
            "Transferability 3/5",
        )

        reviews, rejected = three.parse_review_output(raw, stream=_stream())

        self.assertEqual(reviews, [])
        self.assertEqual(rejected, 1)

    def test_review_decision_matrix_is_structurally_closed(self):
        stream = _stream()

        meta_skill, rejected = three.parse_review_output(
            _review(scope="distillation-meta"),
            stream=stream,
        )
        self.assertEqual(meta_skill, [])
        self.assertEqual(rejected, 1)

        planned_skill, rejected = three.parse_review_output(
            _review(action_state="planned", action_records="none"),
            stream=stream,
        )
        self.assertEqual(planned_skill, [])
        self.assertEqual(rejected, 1)

        unverified_skill, rejected = three.parse_review_output(
            _review(verification_state="none", verification_records="none"),
            stream=stream,
        )
        self.assertEqual(unverified_skill, [])
        self.assertEqual(rejected, 1)

        routine_skill, rejected = three.parse_review_output(
            _review(novelty="routine"),
            stream=stream,
        )
        self.assertEqual(routine_skill, [])
        self.assertEqual(rejected, 1)

        routine_drop, rejected = three.parse_review_output(
            _review(
                "drop",
                novelty="routine",
                action_state="none",
                verification_state="none",
                action_records="none",
                verification_records="none",
            ).replace("Learning Value 4/5", "Learning Value 3/5"),
            stream=stream,
        )
        self.assertEqual((len(routine_drop), rejected), (1, 0))

        meta_drop, rejected = three.parse_review_output(
            _review(
                "drop",
                scope="distillation-meta",
                action_state="none",
                verification_state="none",
                action_records="none",
                verification_records="none",
            ),
            stream=stream,
        )
        self.assertEqual((len(meta_drop), rejected), (1, 0))

    def test_exact_instructional_reason_residue_is_ignored(self):
        raw = _review() + "\nReason: 一句简洁裁决理由。\n"

        reviews, rejected = three.parse_review_output(raw, stream=_stream())

        self.assertEqual((len(reviews), rejected), (1, 0))

    def test_crystallization_must_preserve_decision_matrix(self):
        stream = _stream()
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        self.assertEqual(rejected, 0)

        _, changed, rejected = three.parse_crystallization_output(
            _skill(action_state="diagnostic"),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual(changed, [])
        self.assertEqual(rejected, 1)

        _, changed, rejected = three.parse_crystallization_output(
            _skill(novelty="routine"),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual(changed, [])
        self.assertEqual(rejected, 1)

    def test_review_requires_one_exact_card_per_discovery_candidate(self):
        stream = _stream()
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]

        reviews, rejected = three.parse_review_output(
            _review(),
            stream=stream,
            candidates=[candidate],
        )
        self.assertEqual((len(reviews), rejected), (1, 0))

        missing, rejected = three.parse_review_output(
            "",
            stream=stream,
            candidates=[candidate],
        )
        self.assertEqual(missing, [])
        self.assertEqual(rejected, 1)

    def test_skill_decision_requires_executed_action_and_verification_records(self):
        stream = _stream()
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]

        missing_action, rejected = three.parse_review_output(
            _review(action_records="none"),
            stream=stream,
            candidates=[candidate],
        )
        self.assertEqual(missing_action, [])
        self.assertEqual(rejected, 1)

        missing_verification, rejected = three.parse_review_output(
            _review(verification_records="none"),
            stream=stream,
            candidates=[candidate],
        )
        self.assertEqual(missing_verification, [])
        self.assertEqual(rejected, 1)

        lesson, rejected = three.parse_review_output(
            _review("lesson", action_records="none", verification_records="000004"),
            stream=stream,
            candidates=[candidate],
        )
        self.assertEqual((len(lesson), rejected), (1, 0))

    def test_review_record_roles_must_be_exact_subsets_of_candidate_evidence(self):
        stream = _stream()
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]

        reviews, rejected = three.parse_review_output(
            _review(
                evidence_records="000001, 000002, 000003",
                action_records="000003",
                verification_records="000004",
            ),
            stream=stream,
            candidates=[candidate],
        )

        self.assertEqual(reviews, [])
        self.assertEqual(rejected, 1)

    def test_non_skill_authority_may_only_be_normalized_downward(self):
        stream = _stream()
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]

        reviews, rejected = three.parse_review_output(
            _review(
                "lesson",
                action_state="diagnostic",
                action_records="none",
            ),
            stream=stream,
            candidates=[candidate],
        )

        self.assertEqual((len(reviews), rejected), (1, 0))
        self.assertEqual(reviews[0].decision, "lesson")
        self.assertEqual(reviews[0].action_state, "none")

    def test_review_accepts_one_outer_marker_with_multiple_exact_cards(self):
        stream = _stream()
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]
        second = two.DiscoveryCandidate(
            title="第二个独立程序",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=candidate.evidence,
            summary="同一小型 fixture 中的第二张结构卡。",
        )
        raw = _review() + "\n\n" + _review("drop").replace(
            three.REVIEW_MARKER + "\n",
            "",
        ).replace("candidate-001", "candidate-002")

        reviews, rejected = three.parse_review_output(
            raw,
            stream=stream,
            candidates=[candidate, second],
        )

        self.assertEqual((len(reviews), rejected), (2, 0))
        self.assertEqual([item.candidate_id for item in reviews], ["candidate-001", "candidate-002"])

    def test_compact_review_protocol_keeps_every_candidate_card(self):
        stream = _stream()
        source = two.parse_discovery_output(_discovery(), stream=stream)[0][0]
        candidates = [
            two.DiscoveryCandidate(
                title=f"候选 {index}",
                goal="",
                obstacle="",
                effective_turn="",
                observed_result="",
                reuse_hypothesis="",
                evidence=source.evidence,
                record_ids=source.record_ids,
                summary="同一 fixture 的独立结构卡。",
            )
            for index in range(1, 31)
        ]
        cards = []
        for index in range(1, 31):
            cards.append(
                _review("drop", action_records="none", verification_records="none")
                .replace("# 对照路径并验证双向流量", f"# 候选 {index}")
                .replace("candidate-001", f"candidate-{index:03d}")
            )
        raw = "\n\n".join(cards)

        reviews, rejected = three.parse_review_output(
            raw,
            stream=stream,
            candidates=candidates,
        )

        self.assertEqual((len(reviews), rejected), (30, 0))
        prompt = three.build_review_prompt(candidates, evidence_stream=stream)
        self.assertIn("本轮共有 30 张候选卡", prompt)
        self.assertIn("candidate-030", prompt)

    def test_review_may_narrow_evidence_only_within_its_candidate(self):
        stream = _stream()
        candidate = two.DiscoveryCandidate(
            title="对照路径",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=("thread-0001:000001-000002", "thread-0001:000003-000004"),
            summary="用对照定位故障方向。",
        )
        narrowed = _review(
            evidence_records="000001, 000002",
            action_records="000001",
            verification_records="000002",
        )

        reviews, rejected = three.parse_review_output(
            narrowed,
            stream=stream,
            candidates=[candidate],
        )

        self.assertEqual((len(reviews), rejected), (1, 0))

    def test_review_accepts_all_normalized_evidence_from_large_discovery_card(self):
        stream = "\n\n".join(
            f"[record {index:06d} | thread=thread-0001 | role=assistant]\nitem {index}"
            for index in range(1, 26)
        )
        evidence = tuple(f"thread-0001:{index:06d}-{index:06d}" for index in range(1, 26))
        candidate = two.DiscoveryCandidate(
            title="用多组对照定位不对称路径",
            goal="",
            obstacle="",
            effective_turn="",
            observed_result="",
            reuse_hypothesis="",
            evidence=evidence,
            summary="多个独立观察共同闭合诊断方向。",
        )
        record_ids = ", ".join(f"{index:06d}" for index in range(1, 26))
        raw = _review(
            evidence_records=record_ids,
            action_records="000024",
            verification_records="000025",
        )

        reviews, rejected = three.parse_review_output(
            raw,
            stream=stream,
            candidates=[candidate],
        )

        self.assertEqual((len(reviews), rejected), (1, 0))
        self.assertEqual(reviews[0].evidence, ("thread-0001:000001-000025",))

        _, skills, rejected = three.parse_crystallization_output(
            _skill(
                evidence="thread-0001:000001-000025",
                evidence_records=record_ids,
                action_records="000024",
                verification_records="000025",
            ),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual((len(skills), rejected), (1, 0))
        self.assertEqual(skills[0].evidence, ("thread-0001:000001-000025",))

    def test_crystallization_must_preserve_action_and_verification_records(self):
        stream = _stream()
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        self.assertEqual(rejected, 0)

        _, changed, rejected = three.parse_crystallization_output(
            _skill(action_records="000002"),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual(changed, [])
        self.assertEqual(rejected, 1)

        _, missing, rejected = three.parse_crystallization_output(
            _skill(verification_records="none"),
            stream=stream,
            decisions=reviews,
        )
        self.assertEqual(missing, [])
        self.assertEqual(rejected, 1)

    def test_crystallization_repairs_only_missing_yaml_close_delimiter(self):
        stream = _stream()
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        self.assertEqual(rejected, 0)
        missing_close = _skill().replace(
            "description: 在相同目标经不同方向或路径产生相反结果时，用受控对照和双端观察定位故障方向。\n---\n\n# 诊断不对称路径",
            "description: 在相同目标经不同方向或路径产生相反结果时，用受控对照和双端观察定位故障方向。\n\n# 诊断不对称路径",
        ) + "\n\n---"

        _, skills, rejected = three.parse_crystallization_output(
            missing_close,
            stream=stream,
            decisions=reviews,
        )

        self.assertEqual((len(skills), rejected), (1, 0))
        self.assertNotIn("---", skills[0].verification)

    def test_crystallization_zero_pads_existing_short_record_ids(self):
        stream = _stream()
        reviews, rejected = three.parse_review_output(_review(), stream=stream)
        self.assertEqual(rejected, 0)

        _, skills, rejected = three.parse_crystallization_output(
            _skill(action_records="3", verification_records="4"),
            stream=stream,
            decisions=reviews,
        )

        self.assertEqual((len(skills), rejected), (1, 0))

    def test_cached_discovery_runs_only_adjudication_and_crystallization(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
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
            calls: list[str] = []

            def fake_llm(prompt, **kwargs):
                calls.append(kwargs["stage"])
                return _review() if kwargs["stage"] == "adjudication" else _skill()

            with patch.object(three, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = three.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=_discovery(),
                )

            self.assertEqual(calls, ["adjudication", "crystallization"])
            self.assertEqual(result.llm_calls, 2)
            self.assertEqual(len(result.reviews), 1)
            self.assertEqual(len(result.candidates), 1)
            self.assertEqual(result.rejected_final_blocks, 0)
            self.assertTrue(result.report_path.is_file())

            calls.clear()
            with patch.object(three, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                resumed = three.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=_discovery(),
                    review_raw_override=_review(),
                )

            self.assertEqual(calls, ["crystallization"])
            self.assertEqual(resumed.llm_calls, 1)
            self.assertEqual(len(resumed.candidates), 1)

            calls.clear()
            with patch.object(three, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                fully_replayed = three.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=_discovery(),
                    review_raw_override=_review(),
                    crystallization_raw_override=_skill(),
                )

            self.assertEqual(calls, [])
            self.assertEqual(fully_replayed.llm_calls, 0)
            self.assertEqual(len(fully_replayed.candidates), 1)

    def test_invalid_review_is_withheld_without_suppressing_closed_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-10" / "_filtered" / "codex"
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
                "# 第二个候选",
            )
            calls: list[str] = []

            def fake_llm(prompt, **kwargs):
                calls.append(kwargs["stage"])
                return _review() if kwargs["stage"] == "adjudication" else _skill()

            with patch.object(three, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = three.run_skill_pass(
                    "2026-08-10",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=discovery,
                )

            self.assertEqual(calls, ["adjudication", "crystallization"])
            self.assertEqual(result.llm_calls, 2)
            self.assertEqual(result.rejected_reviews, 1)
            self.assertEqual(len(result.candidates), 1)
            self.assertIn("> PARTIAL:", result.report_path.read_text(encoding="utf-8"))

    def test_invalid_final_block_does_not_suppress_closed_skill_sibling(self):
        stream = _stream()
        source = two.parse_discovery_output(_discovery(), stream=stream)[0][0]
        second = replace(source, title="第二个候选")
        reviews, rejected = three.parse_review_output(
            _review()
            + "\n\n"
            + _review().replace("candidate-001", "candidate-002"),
            stream=stream,
            candidates=[source, second],
        )
        self.assertEqual(rejected, 0)
        raw = _skill() + "\n\n" + _skill(
            review_id="review-002",
            action_records="000002",
        )

        _, skills, rejected = three.parse_crystallization_output(
            raw,
            stream=stream,
            decisions=reviews,
        )

        self.assertEqual(len(skills), 1)
        self.assertEqual(rejected, 1)


if __name__ == "__main__":
    unittest.main()
