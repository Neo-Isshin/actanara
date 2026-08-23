import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.filtered_dialogue import (
    FILTERED_DIALOGUE_SCHEMA,
    compact_filtered_entries,
    load_filtered_source_entries,
    replay_filtered_entries,
    write_compact_filtered_jsonl,
)
from diary_generator import narrative_pass, skill_pass_minimal_support as skill_pass, technical_pass


class FilteredDialogueCompactionTests(unittest.TestCase):
    def test_shared_snapshot_prefix_is_stored_once_and_replays_losslessly(self):
        rows = [
            _row("a", "10:00", "user", "goal"),
            _row("a", "10:01", "assistant", "attempt"),
            _row("a", "10:02", "user", "correction"),
            _row("b", "10:00", "user", "goal"),
            _row("b", "10:01", "assistant", "attempt"),
            _row("b", "10:03", "assistant", "verified fix"),
        ]

        compact = compact_filtered_entries(rows, source="codex")

        self.assertEqual(len(compact), 4)
        self.assertTrue(all(row["schemaVersion"] == FILTERED_DIALOGUE_SCHEMA for row in compact))
        self.assertEqual([row["occurrenceCount"] for row in compact[:2]], [2, 2])
        self.assertEqual(replay_filtered_entries(compact), rows)

    def test_same_body_under_different_parent_is_not_merged(self):
        rows = [
            _row("a", "10:00", "user", "first goal"),
            _row("a", "10:01", "assistant", "retry"),
            _row("b", "11:00", "user", "second goal"),
            _row("b", "11:01", "assistant", "retry"),
        ]

        compact = compact_filtered_entries(rows, source="codex")

        self.assertEqual(len(compact), 4)
        self.assertEqual(sum(row["content"] == "retry" for row in compact), 2)
        self.assertEqual(replay_filtered_entries(compact), rows)

    def test_similar_text_and_unattributed_records_are_never_deduped(self):
        rows = [
            {"time": "10:00", "role": "user", "content": "retry request"},
            {"time": "10:01", "role": "user", "content": "retry requests"},
            {"time": "10:02", "role": "user", "content": "retry request"},
        ]

        compact = compact_filtered_entries(rows, source="legacy")

        self.assertEqual(len(compact), 3)
        self.assertEqual([row["occurrenceCount"] for row in compact], [1, 1, 1])

    def test_atomic_writer_and_loader_keep_compact_v2_unchanged(self):
        rows = [
            _row("a", "10:00", "user", "goal"),
            _row("b", "10:00", "user", "goal"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "codex"
            path = source_dir / "unified_daily.jsonl"
            written = write_compact_filtered_jsonl(path, rows, source="codex")
            loaded = load_filtered_source_entries(source_dir)

            disk_rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(written), 1)
        self.assertEqual(disk_rows, written)
        self.assertEqual(loaded, written)
        self.assertEqual(replay_filtered_entries(loaded), rows)

    def test_narrative_technical_and_skill_share_the_same_compacted_legacy_view(self):
        rows = [
            _row("a", "10:00", "user", "goal"),
            _row("a", "10:01", "assistant", "attempt"),
            _row("a", "10:02", "user", "correction"),
            _row("b", "10:00", "user", "goal"),
            _row("b", "10:01", "assistant", "attempt"),
            _row("b", "10:03", "assistant", "verified fix"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filtered_root = root / "__diary_daily" / "2026-08-11" / "_filtered"
            source_dir = filtered_root / "codex"
            source_dir.mkdir(parents=True)
            source_dir.joinpath("unified_daily.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            with patch.object(narrative_pass, "_runtime_diary_root", return_value=root):
                narrative_rows = narrative_pass.load_filtered_entries("2026-08-11")["codex"]
            technical_rows = technical_pass.load_unified_source_entries(filtered_root)[0]["codex"]
            skill_rows = skill_pass.load_filtered_stream(
                SimpleNamespace(diary_dir=root),
                "2026-08-11",
            )
            skill_stream = skill_pass.render_filtered_stream(skill_rows)

        self.assertEqual(len(narrative_rows), 4)
        self.assertEqual(len(technical_rows), 4)
        self.assertEqual(len(skill_rows), 4)
        self.assertEqual(skill_stream.count("\ngoal"), 1)
        self.assertEqual(skill_stream.count("[record "), 4)


def _row(conversation_id: str, time: str, role: str, content: str) -> dict[str, str]:
    return {
        "time": time,
        "role": role,
        "content": content,
        "conversationId": conversation_id,
    }


if __name__ == "__main__":
    unittest.main()
