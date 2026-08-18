import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from diary_generator import skill_pass_single_call as single


def _stream(*contents: str, thread: str = "thread-0001") -> str:
    return "\n\n".join(
        f"[record {index:06d} | thread={thread} | role=assistant]\n{content}"
        for index, content in enumerate(contents, start=1)
    )


def _lesson(*, evidence: str = "thread-0001:000001-000002") -> str:
    return f"""{single.LESSON_MARKER}
# 对照路径揭示共享层之外的故障
Evidence: {evidence}
Scores: Evidence Closure 4/5 · Learning Value 3/5 · Transferability 2/5

## Situation
两条路径面对同一目标时产生不同结果。

## Lesson
先独立对照各路径，再修改共享层。

## Observed Result
对照确认只有其中一条路径失败。
"""


def _skill(
    *,
    name: str = "isolate-divergent-paths",
    evidence: str = "thread-0001:000001-000004",
    procedure: str = "1. 在相同条件下分别检查每条路径。",
) -> str:
    return f"""{single.SKILL_MARKER}
Evidence: {evidence}
Scores: Evidence Closure 5/5 · Learning Value 4/5 · Transferability 5/5

---
name: {name}
description: 在相同目标的访问路径出现分歧时，通过独立对照定位路径特有故障。
---

# 隔离不对称访问路径

## Trigger
同一目标经不同路径呈现不同结果。

## Procedure
{procedure}

## Pitfalls
- 不要把一条路径成功当作所有路径都健康。

## Verification
- 确认对照结果能够稳定区分健康路径与失败路径。
"""


class SingleCallSkillPassTests(unittest.TestCase):
    def test_prompt_assigns_semantics_to_one_model_call_without_quantity_target(self):
        prompt = single.build_skill_prompt("filtered stream")

        self.assertIn("Agent 程序性记忆架构师", single.SYSTEM_SKILL_DISTILLATION)
        self.assertIn("结果逆向追溯因果链", single.SYSTEM_SKILL_DISTILLATION)
        self.assertIn("Lesson 与 Skill", single.SYSTEM_SKILL_DISTILLATION)
        self.assertIn("从结果逆向追溯", prompt)
        self.assertIn("全轨迹结果盘点", prompt)
        self.assertIn("目标 → 非平凡阻碍或错误路线 → 有效转折", prompt)
        self.assertIn("反事实检查", prompt)
        self.assertIn("候选族比较", prompt)
        self.assertIn("投影到问题类别", prompt)
        self.assertIn("行动定理", prompt)
        self.assertIn("站在新 Agent 角度复核", prompt)
        self.assertIn("最后执行硬否决", prompt)
        self.assertIn("Promotion", prompt)
        self.assertIn("允许最终没有任何输出", prompt)
        self.assertIn("Evidence Closure", prompt)
        self.assertIn("Learning Value", prompt)
        self.assertIn("Transferability", prompt)
        self.assertIn("不存在数量目标", prompt)
        self.assertIn("步骤数量由证据决定", prompt)
        self.assertIn("工作可能跨 thread", prompt)
        self.assertNotIn("Source-Workflow", prompt)
        self.assertNotIn("Option 1", prompt)
        self.assertNotIn("P/S/E", prompt)
        self.assertLess(prompt.index("filtered stream"), prompt.index("从结果逆向追溯"))

    def test_render_keeps_global_chronology_instead_of_grouping_by_thread(self):
        entries = [
            single.FilteredEntry("codex", "10:00", "user", "first", "a", 1),
            single.FilteredEntry("codex", "10:01", "user", "second", "b", 2),
            single.FilteredEntry("codex", "10:02", "assistant", "third", "a", 3),
        ]

        rendered = single.render_filtered_stream(entries)

        self.assertLess(rendered.index("first"), rendered.index("second"))
        self.assertLess(rendered.index("second"), rendered.index("third"))
        self.assertIn("record 000001 | thread=thread-0001", rendered)
        self.assertIn("record 000002 | thread=thread-0002", rendered)
        self.assertIn("record 000003 | thread=thread-0001", rendered)

    def test_thin_reader_accepts_scores_without_using_them_as_admission_gate(self):
        stream = _stream("goal", "observed boundary")
        low_scored = _lesson().replace(
            "Evidence Closure 4/5 · Learning Value 3/5 · Transferability 2/5",
            "Evidence Closure 0/5 · Learning Value 0/5 · Transferability 0/5",
        )

        lessons, skills, rejected = single.parse_distillation_output(low_scored, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual(len(lessons), 1)
        self.assertEqual(skills, [])
        self.assertEqual(lessons[0].scores.evidence_closure, 0)

    def test_thin_reader_does_not_enforce_failure_vocabulary_or_step_count(self):
        stream = _stream("goal", "method", "result", "confirmation")
        block = _skill(procedure="执行记录中实际使用的单一原子操作。")

        lessons, skills, rejected = single.parse_distillation_output(block, stream=stream)

        self.assertEqual((lessons, rejected), ([], 0))
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].procedure, "执行记录中实际使用的单一原子操作。")

    def test_reader_rejects_forged_evidence_but_salvages_valid_sibling(self):
        stream = _stream("goal", "boundary", "method", "result")
        raw = _lesson(evidence="thread-0002:000001-000002") + "\n\n" + _skill()

        lessons, skills, rejected = single.parse_distillation_output(raw, stream=stream)

        self.assertEqual(lessons, [])
        self.assertEqual([item.name for item in skills], ["isolate-divergent-paths"])
        self.assertEqual(rejected, 1)

    def test_reader_rejects_private_output_without_semantic_reclassification(self):
        stream = _stream("goal", "boundary", "method", "result")
        unsafe = _skill().replace("同一目标", "/Users/alice/private 同一目标", 1)

        lessons, skills, rejected = single.parse_distillation_output(unsafe, stream=stream)

        self.assertEqual(lessons, [])
        self.assertEqual(skills, [])
        self.assertEqual(rejected, 1)

    def test_reader_does_not_semantically_dedupe_model_output(self):
        stream = _stream("goal", "boundary", "method", "result")
        raw = _skill() + "\n\n" + _skill()

        _lessons, skills, rejected = single.parse_distillation_output(raw, stream=stream)

        self.assertEqual(rejected, 0)
        self.assertEqual(len(skills), 2)

    def test_call_uses_skill_specific_medium_thinking_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            with patch.object(
                single,
                "execute_llm_message",
                return_value=SimpleNamespace(text="<think>private</think>\n" + _lesson()),
            ) as execute:
                output = single.call_skill_llm("prompt", stream="stream", paths=paths)

        self.assertNotIn("private", output)
        self.assertEqual(execute.call_args.kwargs["thinking_mode"], "medium")
        self.assertEqual(execute.call_args.kwargs["pass_id"], "skill-single-call")

    def test_run_uses_exactly_one_llm_call_and_writes_separate_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"time": "10:00", "role": "user", "content": "goal", "conversationId": "a"}),
                        json.dumps({"time": "10:01", "role": "assistant", "content": "boundary", "conversationId": "a"}),
                        json.dumps({"time": "10:02", "role": "assistant", "content": "method", "conversationId": "a"}),
                        json.dumps({"time": "10:03", "role": "assistant", "content": "result", "conversationId": "a"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            calls = []

            def fake_llm(prompt, **kwargs):
                calls.append((prompt, kwargs))
                return _lesson(evidence="thread-0001:000001-000002") + "\n\n" + _skill()

            with patch.object(single, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = single.run_skill_pass("2026-08-12", paths=paths, llm_call=fake_llm)

            report = result.report_path.read_text(encoding="utf-8")

            self.assertEqual(len(calls), 1)
            self.assertEqual(result.llm_calls, 1)
            self.assertEqual(len(result.lessons), 1)
            self.assertEqual(len(result.candidates), 1)
            self.assertIn("one holistic LLM call", report)
            self.assertIn("Total LLM calls: 1", report)
            self.assertIn(single.LESSON_MARKER, report)
            self.assertIn(single.SKILL_MARKER, report)
            self.assertEqual(result.report_path.name, "skill-single-call-v3-2026-08-12.md")
            self.assertEqual(result.report_path.stat().st_mode & 0o777, 0o600)
            self.assertIsNotNone(result.raw_output_path)
            self.assertEqual(result.raw_output_path.name, "skill-single-call-v3-raw-2026-08-12.md")
            self.assertEqual(result.raw_output_path.stat().st_mode & 0o777, 0o600)
            self.assertIn(single.LESSON_MARKER, result.raw_output_path.read_text(encoding="utf-8"))

    def test_raw_output_is_withheld_when_it_matches_private_output_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps(
                    {"time": "10:00", "role": "user", "content": "goal", "conversationId": "a"}
                )
                + "\n",
                encoding="utf-8",
            )

            def unsafe_llm(*args, **kwargs):
                return "/Users/alice/private/output"

            with patch.object(single, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                result = single.run_skill_pass("2026-08-12", paths=paths, llm_call=unsafe_llm)

            raw = result.raw_output_path.read_text(encoding="utf-8")
            self.assertIn("withheld", raw)
            self.assertNotIn("/Users/alice", raw)

    def test_empty_day_uses_no_llm_and_oversize_day_fails_before_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            calls = []

            def forbidden(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("must not call")

            with patch.object(single, "resolve_llm_provider", return_value={"pipelineGateTokens": 80000}):
                empty = single.run_skill_pass("2026-08-12", paths=paths, llm_call=forbidden)

            self.assertEqual(empty.llm_calls, 0)
            self.assertEqual(calls, [])

            filtered = paths.diary_dir / "__diary_daily" / "2026-08-12" / "_filtered" / "codex"
            filtered.mkdir(parents=True)
            filtered.joinpath("unified_daily.jsonl").write_text(
                json.dumps({"time": "10:00", "role": "user", "content": "large " * 5000, "conversationId": "a"}) + "\n",
                encoding="utf-8",
            )
            with patch.object(single, "resolve_llm_provider", return_value={"pipelineGateTokens": 1000}):
                with self.assertRaises(single.SkillPassError):
                    single.run_skill_pass("2026-08-12", paths=paths, llm_call=forbidden)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
