import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))
os.environ["OPEN_NOVA_SECRET_BACKEND"] = "memory"

from app.services import diary
from data_foundation.paths import initialize_home
from data_foundation.settings import write_llm_provider, write_settings


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"{\\"agent\\":[\\"short\\"]}"}}]}'


class _AnthropicResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"content":[{"type":"text","text":"{\\"agent\\":[\\"short\\"]}"}]}'


class DashboardDiaryLlmProviderTests(unittest.TestCase):
    def test_agent_work_summarizer_uses_resolved_llm_provider(self):
        agent_work = {"agent": [f"entry {idx}" for idx in range(16)]}
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_llm_provider(
                {
                    "provider": "openai-compatible",
                    "mode": "custom",
                    "endpoint": "https://llm.local/api",
                    "model": "model-from-settings",
                    "api": "openai-compatible",
                    "apiKey": "secret-" + "from-settings",
                },
                paths,
            )
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
                patch.object(diary, "_llm_cache_load", return_value=None),
                patch.object(diary, "_llm_cache_save"),
            ):
                summarized = diary._summarize_agent_work(agent_work)

        self.assertEqual(summarized, {"agent": ["short"]})
        self.assertEqual(captured["url"], "https://llm.local/api/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "model-from-settings")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-from-settings")

    def test_minimax_endpoint_uses_anthropic_messages_path(self):
        self.assertEqual(
            diary._dashboard_llm_url({"provider": "minimax", "endpoint": "https://api.minimaxi.com/v1"}),
            "https://api.minimaxi.com/anthropic/v1/messages",
        )

    def test_dashboard_llm_url_requires_endpoint(self):
        with self.assertRaisesRegex(ValueError, "endpoint is required"):
            diary._dashboard_llm_url({"provider": "custom", "endpoint": ""})

    def test_anthropic_messages_api_uses_anthropic_transport_even_for_non_minimax_provider(self):
        self.assertTrue(diary._dashboard_uses_anthropic({"provider": "glm", "api": "anthropic-messages"}))
        self.assertEqual(
            diary._dashboard_llm_url({"provider": "glm", "api": "anthropic-messages", "endpoint": "https://open.bigmodel.cn/api/anthropic"}),
            "https://open.bigmodel.cn/api/anthropic/v1/messages",
        )

    def test_minimax_agent_work_summarizer_uses_anthropic_format(self):
        agent_work = {"agent": [f"entry {idx}" for idx in range(16)]}
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _AnthropicResponse()

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_llm_provider(
                {
                    "provider": "minimax",
                    "mode": "custom",
                    "endpoint": "https://api.minimaxi.com",
                    "model": "MiniMax-M2.7-highspeed",
                    "api": "anthropic-messages",
                    "apiKey": "secret-" + "from-settings",
                },
                paths,
            )
            with (
                patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
                patch.object(diary, "_llm_cache_load", return_value=None),
                patch.object(diary, "_llm_cache_save"),
            ):
                summarized = diary._summarize_agent_work(agent_work)

        self.assertEqual(summarized, {"agent": ["short"]})
        self.assertEqual(captured["url"], "https://api.minimaxi.com/anthropic/v1/messages")
        self.assertEqual(captured["body"]["model"], "MiniMax-M2.7-highspeed")
        self.assertEqual(captured["body"]["messages"][0]["role"], "user")
        self.assertEqual(captured["headers"]["X-api-key"], "secret-from-settings")
        self.assertNotIn("Authorization", captured["headers"])

    def test_missing_llm_key_returns_original_work_without_network(self):
        agent_work = {"agent": [f"entry {idx}" for idx in range(16)]}
        with (
            patch.dict(os.environ, {"LLM_API_KEY": ""}),
            patch.object(diary, "_dashboard_llm_provider", return_value={"apiKey": "", "endpoint": "", "model": "m"}),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = diary._summarize_agent_work(agent_work)

        self.assertEqual(result, agent_work)
        urlopen.assert_not_called()

    def test_missing_endpoint_returns_original_work_without_network(self):
        agent_work = {"agent": [f"entry {idx}" for idx in range(16)]}
        with (
            patch.object(diary, "_dashboard_llm_provider", return_value={"apiKey": "secret", "endpoint": "", "model": "m"}),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = diary._summarize_agent_work(agent_work)

        self.assertEqual(result, agent_work)
        urlopen.assert_not_called()

    def test_hour_from_iso_uses_configured_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_settings({"general": {"timezone": "UTC"}}, paths)

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                hour = diary._hour_from_iso("2026-05-20T03:30:00Z")

        self.assertEqual(hour, 3)

    def test_hermes_hourly_scan_uses_configured_timezone_business_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = initialize_home(root / "NovaDiary", legacy_diary_root=root / "Diary")
            hermes_db = root / "hermes" / "state.db"
            hermes_db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(hermes_db)) as connection:
                connection.execute(
                    "CREATE TABLE sessions(started_at REAL, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER)"
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                    (datetime(2026, 5, 20, 3, 30, tzinfo=timezone.utc).timestamp(), 10, 2, 3),
                )
                connection.commit()
            write_settings(
                {
                    "general": {"timezone": "UTC"},
                    "externalTools": {"hermes": {"stateDbPath": str(hermes_db)}},
                },
                paths,
            )

            with patch.dict(os.environ, {"NOVA_HOME": str(paths.home)}):
                hourly = diary._scan_hermes_for_date("2026-05-19")

        self.assertEqual(hourly[3], 15)
        self.assertEqual(sum(hourly.values()), 15)


if __name__ == "__main__":
    unittest.main()
