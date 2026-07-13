import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from ai_assets_center import unified_source_collector as collector
from data_foundation.settings import write_settings
from data_foundation.paths import initialize_home, update_runtime_manifest_paths


class UnifiedSourceCollectorTests(unittest.TestCase):
    def test_codex_rollout_response_items_are_archived_for_narrative_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".codex" / "sessions" / "2026" / "05" / "21"
            sessions.mkdir(parents=True)
            fixture = sessions / "rollout-2026-05-21T22-18-49-fixture.jsonl"
            _write_codex_rollout_fixture(fixture)

            diary_root = root / "Diary"
            with (
                patch.dict("os.environ", {"TARGET_TIMEZONE": "Asia/Hong_Kong"}, clear=False),
                patch.object(collector, "load_paths", return_value=type("Paths", (), {"diary_dir": diary_root})()),
                patch.object(collector, "external_tool_path", return_value=root / ".codex" / "sessions"),
            ):
                captured = collector.collect_engine("codex", collector.SOURCES["codex"], "2026-05-22")

            self.assertEqual(captured, 2)
            filtered = diary_root / "__diary_daily" / "2026-05-22" / "_filtered" / "codex" / "unified_daily.jsonl"
            raw = diary_root / "__diary_daily" / "2026-05-22" / "codex" / "unified_daily.jsonl"
            self.assertTrue(filtered.exists())
            self.assertTrue(raw.exists())
            rows = [json.loads(line) for line in filtered.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
            self.assertEqual([row["time"] for row in rows], ["18:28", "18:28"])
            joined = "\n".join(row["content"] for row in rows)
            self.assertIn("请修复 Codex 归档", joined)
            self.assertIn("我会先检查 collector", joined)
            self.assertNotIn("[动作: exec_command] rg -n \"codex\"", joined)
            self.assertNotIn("<environment_context>", joined)
            self.assertNotIn("base_instructions", joined)
            self.assertNotIn("token_count", joined)
            self.assertNotIn("huge terminal output", joined)

    def test_collector_writes_to_runtime_diary_root_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".codex" / "sessions" / "2026" / "05" / "21"
            sessions.mkdir(parents=True)
            fixture = sessions / "rollout-2026-05-21T22-18-49-fixture.jsonl"
            _write_codex_rollout_fixture(fixture)
            legacy = root / "LegacyDiary"
            current = root / "GeneratedDiary"
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=legacy)
            paths = update_runtime_manifest_paths(paths.home, generated_diary_root=current, legacy_diary_root=legacy)
            write_settings({"externalTools": {"codex": {"sessionsRoot": str(root / ".codex" / "sessions")}}}, paths)
            with patch.dict("os.environ", {"NOVA_HOME": str(paths.home), "TARGET_TIMEZONE": "Asia/Hong_Kong"}, clear=False), patch.object(
                collector, "load_paths", return_value=paths
            ):
                captured = collector.collect_engine("codex", collector.SOURCES["codex"], "2026-05-22")

            self.assertEqual(captured, 2)
            self.assertTrue((current / "__diary_daily" / "2026-05-22" / "_filtered" / "codex" / "unified_daily.jsonl").exists())
            self.assertFalse((legacy / "__diary_daily").exists())

    def test_codex_collector_uses_configured_external_tool_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured_sessions = root / "configured-codex" / "sessions" / "2026" / "05" / "21"
            configured_sessions.mkdir(parents=True)
            _write_codex_rollout_fixture(configured_sessions / "rollout-2026-05-21T22-18-49-fixture.jsonl")
            legacy = root / "LegacyDiary"
            current = root / "GeneratedDiary"
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=legacy)
            paths = update_runtime_manifest_paths(paths.home, generated_diary_root=current, legacy_diary_root=legacy)
            write_settings({"externalTools": {"codex": {"sessionsRoot": str(configured_sessions.parent.parent.parent)}}}, paths)

            with patch.dict(
                "os.environ",
                {"NOVA_HOME": str(paths.home), "TARGET_TIMEZONE": "Asia/Hong_Kong"},
                clear=False,
            ):
                captured = collector.collect_engine("codex", collector.SOURCES["codex"], "2026-05-22")

            self.assertEqual(captured, 2)
            filtered = current / "__diary_daily" / "2026-05-22" / "_filtered" / "codex" / "unified_daily.jsonl"
            self.assertTrue(filtered.exists())
            rows = [json.loads(line) for line in filtered.read_text(encoding="utf-8").splitlines()]
            self.assertIn("请修复 Codex 归档", "\n".join(row["content"] for row in rows))

    def test_openclaw_agents_are_collected_by_unified_collector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".openclaw" / "agents" / "agent-one" / "sessions"
            sessions.mkdir(parents=True)
            fixture = sessions / "session.jsonl"
            fixture.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in [
                        {
                            "type": "message",
                            "timestamp": "2026-05-22T10:28:18Z",
                            "message": {"role": "user", "content": "plain OpenClaw request"},
                        },
                        {
                            "type": "message",
                            "timestamp": "2026-05-22T10:29:18Z",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    "prefix ",
                                    {"type": "text", "text": "<thinking>skip</thinking>visible"},
                                ],
                            },
                        },
                        {
                            "type": "message",
                            "timestamp": "2026-05-22T10:30:18Z",
                            "message": {"role": "user", "content": "[cron:skip] automated"},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            diary_root = root / "Diary"
            cfg = {
                "tool": "openclaw",
                "key": "agentsRoot",
                "pattern": "*.jsonl*",
                "engine": "openclaw_agents",
            }
            with patch.dict("os.environ", {"TARGET_TIMEZONE": "Asia/Hong_Kong"}, clear=False), patch.object(
                collector, "external_tool_path", return_value=root / ".openclaw" / "agents"
            ), patch.object(
                collector, "load_paths", return_value=type("Paths", (), {"diary_dir": diary_root})()
            ):
                captured = collector.collect_engine("openclaw", cfg, "2026-05-22")

            self.assertEqual(captured, 0)
            self.assertFalse((diary_root / "__diary_daily").exists())

            fixture.write_text(
                "\n".join(
                    json.dumps(row, ensure_ascii=False)
                    for row in [
                        {
                            "type": "message",
                            "timestamp": "2026-05-22T10:28:18Z",
                            "message": {"role": "user", "content": "plain OpenClaw request"},
                        },
                        {
                            "type": "message",
                            "timestamp": "2026-05-22T10:29:18Z",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    "prefix ",
                                    {"type": "text", "text": "<thinking>skip</thinking>visible"},
                                ],
                            },
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"TARGET_TIMEZONE": "Asia/Hong_Kong"}, clear=False), patch.object(
                collector, "external_tool_path", return_value=root / ".openclaw" / "agents"
            ), patch.object(
                collector, "load_paths", return_value=type("Paths", (), {"diary_dir": diary_root})()
            ):
                captured = collector.collect_engine("openclaw", cfg, "2026-05-22")

            self.assertEqual(captured, 2)
            filtered = diary_root / "__diary_daily" / "2026-05-22" / "_filtered" / "agent-one" / "unified_daily.jsonl"
            rows = [json.loads(line) for line in filtered.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
            self.assertEqual(rows[1]["content"], "prefix \nvisible")

    def test_collection_window_follows_configured_timezone(self):
        with patch.dict("os.environ", {"TARGET_TIMEZONE": "UTC"}, clear=False):
            start_ts, duration = collector.get_hkt_window("2026-05-22")

        self.assertEqual(start_ts, datetime(2026, 5, 22, 4, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(duration, 86400)


def _write_codex_rollout_fixture(path: Path) -> None:
    rows = [
        {
            "timestamp": "2026-05-22T10:28:18.675Z",
            "type": "session_meta",
            "payload": {
                "id": "codex-session",
                "cwd": "/workspace/example/open-nova",
                "base_instructions": {"text": "base_instructions should not enter narrative"},
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.680Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>/Users/example</cwd>\n</environment_context>"}],
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.680Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "developer payload should be skipped"}],
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.681Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "请修复 Codex 归档"}],
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.681Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "请修复 Codex 归档"},
        },
        {
            "timestamp": "2026-05-22T10:28:18.682Z",
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 10}}},
        },
        {
            "timestamp": "2026-05-22T10:28:18.683Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "我会先检查 collector"}],
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.683Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "我会先检查 collector"},
        },
        {
            "timestamp": "2026-05-22T10:28:18.684Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": 'rg -n "codex" src tests'}),
            },
        },
        {
            "timestamp": "2026-05-22T10:28:18.685Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": "huge terminal output should be skipped",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
