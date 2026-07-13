import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.llm_provider_test import check_llm_provider_availability
from data_foundation.paths import initialize_home
from data_foundation.settings import read_settings, write_llm_provider


class LlmProviderAvailabilityTests(unittest.TestCase):
    def test_probe_reports_missing_config_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")

            result = check_llm_provider_availability(paths)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing_config")
        self.assertIn("apiKey", result["missing"])
        self.assertFalse(result["hasApiKey"])

    def test_probe_uses_secret_ref_without_exposing_or_persisting_candidate_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_llm_provider(
                {
                    "provider": "custom",
                    "endpoint": "https://llm.example/v1",
                    "model": "saved-model",
                    "api": "openai-compatible",
                    "apiKey": "saved-secret",
                },
                paths,
            )
            calls = []

            def fake_sender(**kwargs):
                calls.append(kwargs)
                return "OK"

            result = check_llm_provider_availability(
                paths,
                candidate={"model": "candidate-model", "apiKey": "candidate-secret"},
                openai_sender=fake_sender,
            )
            raw = read_settings(paths, redact_secrets=False)["llmProvider"]

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "candidate-model")
        self.assertTrue(result["hasApiKey"])
        self.assertNotIn("apiKey", result)
        self.assertEqual(calls[0]["api_key"], "candidate-secret")
        self.assertEqual(raw["apiKey"], "")
        self.assertNotEqual(raw.get("apiKey"), "candidate-secret")

    def test_anthropic_api_uses_anthropic_sender(self):
        calls = []

        def fake_anthropic(**kwargs):
            calls.append(kwargs)
            return "OK"

        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            result = check_llm_provider_availability(
                paths,
                candidate={
                    "provider": "custom",
                    "endpoint": "https://llm.example/anthropic",
                    "model": "m",
                    "api": "anthropic-messages",
                    "apiKey": "secret",
                },
                anthropic_sender=fake_anthropic,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["api"], "anthropic-messages")
        self.assertEqual(calls[0]["model"], "m")

    def test_candidate_preset_provider_rehydrates_catalog_endpoint_api_and_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "NovaDiary", legacy_diary_root=Path(tmp) / "Diary")
            write_llm_provider(
                {
                    "mode": "preset",
                    "provider": "minimax-cn",
                    "model": "MiniMax-M3",
                    "apiKey": "saved-secret",
                },
                paths,
            )
            calls = []

            def fake_anthropic(**kwargs):
                calls.append(kwargs)
                return "OK"

            result = check_llm_provider_availability(
                paths,
                candidate={"provider": "glm", "model": "glm-5.1", "apiKey": "candidate-secret"},
                anthropic_sender=fake_anthropic,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "glm")
        self.assertEqual(result["model"], "glm-5.1")
        self.assertEqual(result["api"], "anthropic-messages")
        self.assertEqual(result["endpoint"], "https://open.bigmodel.cn/api/anthropic")
        self.assertEqual(calls[0]["api_key"], "candidate-secret")
        self.assertEqual(calls[0]["endpoint"], "https://open.bigmodel.cn/api/anthropic")
        self.assertEqual(calls[0]["model"], "glm-5.1")


if __name__ == "__main__":
    unittest.main()
