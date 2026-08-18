import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from diary_generator import skill_pass_single_call as single
from diary_generator import skill_pass_two_call as two


def _stream(*rows: tuple[str, str]) -> str:
    return "\n\n".join(
        f"[record {index:06d} | thread={thread} | role=assistant]\n{text}"
        for index, (thread, text) in enumerate(rows, start=1)
    )


def _discovery(records: str = "000001, 000002, 000003, 000004") -> str:
    return f"""{two.DISCOVERY_MARKER}
# 不对称路径需要双端证据
Records: {records}

## Goal
定位一条路径为何超时。

## Non-trivial Obstacle
单端成功误导了故障归因。

## Effective Turn
同时对照两条路径并抓取双端数据包。

## Observed Result
确认请求到达而返回路径丢失。

## Reuse Hypothesis
用于诊断方向不对称的网络故障。
"""


def _skill(evidence: str = "thread-0001:000001-000004") -> str:
    return f"""{single.SKILL_MARKER}
Evidence: {evidence}
Scores: Evidence Closure 5/5 · Learning Value 4/5 · Transferability 5/5

---
name: diagnose-asymmetric-network-paths
description: 在连接方向表现不一致时，用路径对照与双端捕获隔离故障方向。
---

# 诊断不对称网络路径

## Trigger
同一目标经不同路径或方向出现相反结果。

## Procedure
1. 固定目标与协议，分别测试每条路径。
2. 在两端同时捕获请求与响应，确定丢失方向。

## Pitfalls
不要把代理路径成功解释为直连路径健康。

## Verification
重复测试稳定显示请求到达且响应只在特定方向丢失。
"""


def _compact_discovery(records: str = "000001, 000002, 000003, 000004") -> str:
    return f"""{two.DISCOVERY_MARKER}
# 用双端证据诊断不对称路径
Records: {records}
Why: 单端成功误导了归因，改用双路径和双端抓包后确认返回方向丢包。
"""


def _legacy_discovery(evidence: str) -> str:
    return _compact_discovery().replace(
        "Records: 000001, 000002, 000003, 000004",
        f"Evidence: {evidence}",
    )


class TwoCallSkillPassTests(unittest.TestCase):
    def test_discovery_and_crystallization_have_separate_responsibilities(self):
        discovery = two.build_discovery_prompt("raw-record")
        final = two.build_crystallization_prompt(
            [two.parse_discovery_output(_discovery(), stream=_stream(*[("thread-0001", str(i)) for i in range(4)]))[0][0]],
            evidence_stream="cited-record",
        )

        self.assertIn("完整扫描", discovery)
        self.assertIn("逆向追溯", discovery)
        self.assertIn("目标—结果遍", discovery)
        self.assertIn("因果单元遍", discovery)
        self.assertIn("困难—转折遍", discovery)
        self.assertIn("长对话与短对话权重相同", discovery)
        self.assertIn("不是 conversation、项目名或用户目标本身", discovery)
        self.assertIn("同一目标可能跨多个 thread", discovery)
        self.assertIn("元系统禁令按候选因果单元判断", discovery)
        self.assertIn("不变量", discovery)
        self.assertIn("事务回滚", discovery)
        self.assertIn("每个候选都必须重复输出", discovery)
        self.assertIn("全局唯一的六位 record 编号", discovery)
        self.assertIn("不要输出 thread 名", discovery)
        self.assertIn("Goal、Obstacle、Turn、Result、评分和最终 Procedure", discovery)
        self.assertLess(discovery.index("raw-record"), discovery.index("发现方法"))
        self.assertIn("候选卡不是事实", final)
        self.assertIn("反事实价值判断", final)
        self.assertIn("通用触发", final)
        self.assertIn("最后硬否决", final)
        self.assertIn("Evidence Closure", final)
        self.assertIn("诊断工作流不要求最终修复", final)
        self.assertIn("普通配置字段解释", final)
        self.assertIn("输出前最后再检查一次", final)
        self.assertIn("最高优先级禁令", two.CRYSTALLIZATION_SYSTEM)
        self.assertIn("最高优先级禁令", two.DISCOVERY_SYSTEM)

    def test_hybrid_crystallization_borrows_reverse_trace_without_forcing_snippets(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(4)])
        candidate = two.parse_discovery_output(_discovery(), stream=stream)[0][0]

        prompt = two.build_hybrid_crystallization_prompt(
            [candidate],
            evidence_stream=stream,
        )

        self.assertIn("逆向追溯决定性转折", prompt)
        self.assertIn("只有证据充分时才陈述根因", prompt)
        self.assertIn("筛选反模式", prompt)
        self.assertIn("移除这些专有信息后", prompt)
        self.assertIn("陌生 Agent", prompt)
        self.assertIn("才给出命令、代码、字面输出或模板", prompt)
        self.assertIn("Lesson 不是更严格的 Skill", prompt)
        self.assertIn("每个候选因果链最多形成一个最终产物", prompt)
        self.assertIn("同一证据链不得重复", prompt)
        self.assertIn("最高且仍可执行的稳定抽象", prompt)
        self.assertIn("每一句做删除测试", prompt)
        self.assertIn("事故中的尺寸、日期、倍率", prompt)
        self.assertIn("三项评分都必须至少为 4/5", prompt)
        self.assertIn("Learning Value 不足 4/5", prompt)
        self.assertIn("若一条拟写 Lesson 已包含可重复的有序动作", prompt)
        self.assertIn("严格区分“已执行的修复”和“已执行的诊断”", prompt)
        self.assertIn("产品功能咨询属于知识问答", prompt)
        self.assertIn("绝不能输出 `# Evidence:`", prompt)
        self.assertIn("允许最终没有输出", prompt)
        self.assertIn("Evidence Closure", prompt)
        self.assertIn("最高优先级禁令", two.HYBRID_CRYSTALLIZATION_SYSTEM)

    def test_hybrid_crystallization_reuses_discovery_and_writes_isolated_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "time": f"10:0{index}",
                            "role": "assistant",
                            "content": value,
                            "conversationId": "a",
                        }
                    )
                    for index, value in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                return _skill()

            with patch.object(two, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = two.run_skill_pass(
                    "2026-08-12",
                    paths=paths,
                    llm_call=fake_llm,
                    discovery_raw_override=_discovery(),
                    crystallization_variant="hybrid",
                )

            self.assertEqual([call[1]["stage"] for call in calls], ["crystallization-hybrid"])
            self.assertIs(calls[0][1]["system"], two.HYBRID_CRYSTALLIZATION_SYSTEM)
            self.assertEqual(result.prompt_version, two.HYBRID_PROMPT_VERSION)
            self.assertEqual(len(result.candidates), 1)
            self.assertIn("v3-hybrid", result.report_path.name)
            self.assertIn("v3-hybrid", result.crystallization_raw_path.name)
            self.assertFalse(two.report_path(paths, "2026-08-12").exists())

    def test_discovery_reader_only_checks_structure_evidence_and_privacy(self):
        stream = _stream(
            ("thread-0001", "goal"),
            ("thread-0001", "obstacle"),
            ("thread-0001", "turn"),
            ("thread-0001", "result"),
        )

        candidates, rejected = two.parse_discovery_output(_discovery(), stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].effective_turn, "同时对照两条路径并抓取双端数据包。")
        forged, rejected = two.parse_discovery_output(
            _discovery("000001, 000002, 000003, 999999"), stream=stream
        )
        self.assertEqual(forged, [])
        self.assertEqual(rejected, 1)

    def test_compact_discovery_defers_full_causal_writing_to_second_stage(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(4)])

        candidates, rejected = two.parse_discovery_output(
            _compact_discovery(),
            stream=stream,
        )

        self.assertEqual(rejected, 0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].goal, "")
        self.assertIn("返回方向丢包", candidates[0].summary)
        self.assertIn("Why:", candidates[0].markdown())
        final = two.build_hybrid_crystallization_prompt(candidates, evidence_stream=stream)
        self.assertIn("候选可能只有一句 Why", final)

    def test_discovery_normalizes_interleaved_thread_interval_without_cross_thread_evidence(self):
        stream = _stream(
            ("thread-0001", "goal"),
            ("thread-0002", "unrelated"),
            ("thread-0001", "turn"),
            ("thread-0002", "unrelated result"),
            ("thread-0001", "result"),
        )

        candidates, rejected = two.parse_discovery_output(
            _legacy_discovery("thread-0001:000001-000005"),
            stream=stream,
        )

        self.assertEqual(rejected, 0)
        self.assertEqual(
            candidates[0].evidence,
            (
                "thread-0001:000001-000001",
                "thread-0001:000003-000003",
                "thread-0001:000005-000005",
            ),
        )
        selected = two.select_cited_records(stream, candidates)
        self.assertIn("goal", selected)
        self.assertIn("turn", selected)
        self.assertIn("result", selected)
        self.assertNotIn("unrelated", selected)

    def test_global_record_ids_restore_threads_without_model_thread_copying(self):
        stream = _stream(
            ("thread-0007", "goal"),
            ("thread-0002", "unrelated"),
            ("thread-0007", "action"),
            ("thread-0007", "verified"),
        )

        candidates, rejected = two.parse_discovery_output(
            _compact_discovery("000001, 000003, 000004"),
            stream=stream,
        )

        self.assertEqual((len(candidates), rejected), (1, 0))
        self.assertEqual(candidates[0].record_ids, (1, 3, 4))
        self.assertEqual(
            candidates[0].evidence,
            ("thread-0007:000001-000001", "thread-0007:000003-000004"),
        )
        self.assertNotIn("unrelated", two.select_cited_records(stream, candidates))

    def test_legacy_wrong_thread_range_cannot_survive_by_accidental_inner_match(self):
        stream = _stream(
            ("thread-0023", "real goal"),
            ("thread-0001", "unrelated inner record"),
            ("thread-0023", "real result"),
        )

        candidates, rejected = two.parse_discovery_output(
            _legacy_discovery("thread-0001:000001-000003"),
            stream=stream,
        )

        self.assertEqual(candidates, [])
        self.assertEqual(rejected, 1)

    def test_discovery_privacy_redaction_preserves_structure_not_private_literals(self):
        raw = _discovery().replace("定位一条路径", "定位 /Users/alice/work 到 198.51.100.8 的路径")

        redacted = two.redact_discovery_output(raw)

        self.assertIn("<private-home>/", redacted)
        self.assertIn("<network-address>", redacted)
        self.assertNotIn("/Users/alice", redacted)
        self.assertNotIn("198.51.100.8", redacted)
        parsed, rejected = two.parse_discovery_output(
            redacted,
            stream=_stream(*[("thread-0001", str(index)) for index in range(4)]),
        )
        self.assertEqual(rejected, 0)
        self.assertEqual(len(parsed), 1)

    def test_discovery_reader_accepts_repeated_generic_headings_in_one_xml_envelope(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(4)])
        first = _discovery().replace(two.DISCOVERY_MARKER + "\n", "").replace(
            "# 不对称路径需要双端证据", "# 一句话候选身份\n不对称路径需要双端证据"
        )
        second = first.replace("不对称路径需要双端证据", "像素编辑需要保持不变量", 1)
        raw = f"<actanara-discovery>\n{first}\n{second}\n</actanara-discovery>"

        parsed, rejected = two.parse_discovery_output(raw, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual([item.title for item in parsed], ["不对称路径需要双端证据", "像素编辑需要保持不变量"])

    def test_discovery_keeps_exact_locator_and_drops_ambiguous_sibling_reference(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(4)])
        raw = _legacy_discovery(
            "thread-0001:000001-000004;thread-0001/0007 系列;thread-9999:000001"
        ).replace("## Non-trivial Obstacle", "## Non-trivial Obst障")

        parsed, rejected = two.parse_discovery_output(raw, stream=stream)

        self.assertEqual(parsed, [])
        self.assertEqual(rejected, 1)

    def test_discovery_expands_record_range_shorthand_under_last_explicit_thread(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(4)])
        raw = _legacy_discovery("thread-0001:000001-000002,000003,000004")

        parsed, rejected = two.parse_discovery_output(raw, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual(
            parsed[0].evidence,
            (
                "thread-0001:000001-000002",
                "thread-0001:000003-000003",
                "thread-0001:000004-000004",
            ),
        )

    def test_discovery_has_no_fixed_evidence_reference_count_limit(self):
        stream = _stream(*[("thread-0001", str(index)) for index in range(30)])
        evidence = ", ".join(f"{index:06d}" for index in range(1, 31))

        parsed, rejected = two.parse_discovery_output(
            _compact_discovery(evidence),
            stream=stream,
        )

        self.assertEqual((len(parsed), rejected), (1, 0))
        self.assertEqual(len(parsed[0].record_ids), 30)
        self.assertEqual(parsed[0].evidence, ("thread-0001:000001-000030",))

    def test_cited_record_packet_keeps_only_referenced_thread_records_in_chronology(self):
        stream = _stream(
            ("thread-0001", "goal"),
            ("thread-0002", "unrelated"),
            ("thread-0001", "obstacle"),
            ("thread-0001", "result"),
        )
        candidate = two.parse_discovery_output(
            _discovery("000001, 000003, 000004"), stream=stream
        )[0][0]

        selected = two.select_cited_records(stream, [candidate])

        self.assertIn("goal", selected)
        self.assertIn("obstacle", selected)
        self.assertIn("result", selected)
        self.assertNotIn("unrelated", selected)
        self.assertLess(selected.index("goal"), selected.index("obstacle"))

    def test_run_uses_two_calls_and_writes_private_stage_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "time": f"10:0{index}",
                            "role": "assistant",
                            "content": value,
                            "conversationId": "a",
                        }
                    )
                    for index, value in enumerate(("goal", "obstacle", "turn", "result"))
                )
                + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                return _discovery() if kwargs["stage"] == "discovery" else _skill()

            with patch.object(two, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = two.run_skill_pass("2026-08-12", paths=paths, llm_call=fake_llm)

            self.assertEqual([call[1]["stage"] for call in calls], ["discovery", "crystallization"])
            self.assertEqual(result.llm_calls, 2)
            self.assertEqual(len(result.discoveries), 1)
            self.assertEqual(len(result.candidates), 1)
            self.assertLess(result.evidence_packet_tokens, result.full_input_tokens + 100)
            self.assertEqual(result.report_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result.discovery_raw_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result.crystallization_raw_path.stat().st_mode & 0o777, 0o600)
            self.assertIn("Total LLM calls: 2", result.report_path.read_text(encoding="utf-8"))

    def test_empty_or_no_discovery_avoids_unnecessary_second_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append(kwargs["stage"])
                return "Nothing to preserve."

            with patch.object(two, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                empty = two.run_skill_pass("2026-08-12", paths=paths, llm_call=fake_llm)
            self.assertEqual(empty.llm_calls, 0)

            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps(
                    {"time": "10:00", "role": "assistant", "content": "ordinary", "conversationId": "a"}
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.object(two, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                no_result = two.run_skill_pass("2026-08-12", paths=paths, llm_call=fake_llm)
            self.assertEqual(calls, ["discovery"])
            self.assertEqual(no_result.llm_calls, 1)
            self.assertEqual(no_result.candidates, ())


if __name__ == "__main__":
    unittest.main()
