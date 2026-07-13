import re
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from diary_generator import narrative_pass


RUN_SLOW_TESTS = os.getenv("OPEN_NOVA_RUN_SLOW_TESTS") == "1"


def _entry(hour: int, index: int) -> dict:
    return {"time": f"{hour:02d}:00", "role": "assistant", "content": f"message {index}"}


class NarrativeGatePlanningTests(unittest.TestCase):
    def test_agent_summary_preflights_window_plan_before_llm_calls(self):
        entries = []
        for hour, count in ((0, 3), (8, 3), (14, 3), (18, 3), (20, 3), (22, 20), (23, 20)):
            entries.extend(_entry(hour, len(entries)) for _ in range(count))
        calls = []

        def token_count(prompt: str) -> int:
            label = re.search(r"日志数据（([^，]+)，", prompt).group(1)
            if "晚上(18-24)" in label or "22:00-24:00" in label:
                return narrative_pass.QUALITY_GATE_TOKENS + 1
            return 100

        def call_llm(prompt: str, is_int: bool = False, **kwargs) -> str:
            del is_int, kwargs
            label = re.search(r"日志数据（([^，]+)，", prompt).group(1)
            calls.append(label)
            return "- planned summary"

        with (
            patch.object(narrative_pass, "get_token_count", side_effect=token_count),
            patch.object(narrative_pass, "call_llm", side_effect=call_llm),
            redirect_stdout(io.StringIO()),
        ):
            summary = narrative_pass._generate_agent_summary("codex", entries)

        self.assertTrue(summary)
        self.assertFalse(any("凌晨(00-04)" in label for label in calls))
        self.assertFalse(any("上午(04-12)" in label for label in calls))
        self.assertFalse(any("下午(12-18)" in label for label in calls))
        self.assertFalse(any("00:00-02:00" in label for label in calls))
        self.assertFalse(any("22:00-24:00" in label for label in calls))
        self.assertIn("codex - 22:00-23:00", calls)
        self.assertIn("codex - 23:00-24:00", calls)
        self.assertIn("codex - 全天连续整合", calls)

    @unittest.skipUnless(RUN_SLOW_TESTS, "slow pathological gate guard; set OPEN_NOVA_RUN_SLOW_TESTS=1")
    def test_entry_gate_split_has_chunk_guard_for_pathological_token_counter(self):
        entries = [_entry(9, index) for index in range(10)]
        with (
            patch.object(narrative_pass, "MAX_GATE_SPLIT_CHUNKS", 3),
            patch.object(narrative_pass, "get_token_count", return_value=narrative_pass.QUALITY_GATE_TOKENS + 1),
        ):
            chunks = narrative_pass._split_entries_by_gate(entries, "agent")

        self.assertEqual(len(chunks), 3)
        self.assertEqual(sum(len(chunk) for chunk in chunks), len(entries))
        self.assertEqual(len(chunks[-1]), 8)

    @unittest.skipUnless(RUN_SLOW_TESTS, "slow pathological gate guard; set OPEN_NOVA_RUN_SLOW_TESTS=1")
    def test_final_precompress_split_has_chunk_guard_for_pathological_token_counter(self):
        text = "\n".join(f"line {index}" for index in range(10))
        with (
            patch.object(narrative_pass, "MAX_FINAL_PRECOMPRESS_CHUNKS", 4),
            patch.object(narrative_pass, "get_token_count", return_value=narrative_pass.QUALITY_GATE_TOKENS + 1),
        ):
            chunks = narrative_pass._split_text_for_partial_gate(text, "agent")

        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[-1].splitlines(), [f"line {index}" for index in range(3, 10)])


if __name__ == "__main__":
    unittest.main()
