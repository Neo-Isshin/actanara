import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.paths import initialize_home
from data_foundation.settings import (
    MASKED_SECRET,
    SETTINGS_SCHEMA_VERSION,
    llm_provider_chain_readiness_error,
    read_settings,
    resolve_llm_provider_chain,
    write_llm_provider,
    write_operator_settings_bundle,
)


class LlmProviderChainTests(unittest.TestCase):
    def _runtime(self, root: Path):
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        read_settings(paths)
        return paths

    def _provider(self, entry_id: str, model: str, *, api_key: str = "") -> dict:
        return {
            "entryId": entry_id,
            "mode": "preset",
            "provider": "minimax-cn",
            "model": model,
            "apiKey": api_key,
            "apiKeyEnv": f"ACTANARA_TEST_{entry_id.upper().replace('-', '_')}_KEY",
        }

    def test_old_single_provider_settings_project_to_one_primary_entry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
        ):
            paths = self._runtime(Path(tmp))
            write_llm_provider(
                self._provider("ignored", "MiniMax-M3", api_key="legacy-key"),
                paths,
            )
            settings_path = paths.config_dir / "settings.json"
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            raw.pop("llmProviderChain", None)
            settings_path.write_text(json.dumps(raw), encoding="utf-8")

            chain = resolve_llm_provider_chain(paths)

        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0]["entryId"], "legacy-primary")
        self.assertEqual(chain[0]["role"], "primary")
        self.assertEqual(chain[0]["model"], "MiniMax-M3")
        self.assertEqual(chain[0]["apiKey"], "legacy-key")

    def test_order_same_provider_secret_isolation_and_primary_mirror(self):
        first_key = "first-provider-key"
        second_key = "second-provider-key"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
        ):
            paths = self._runtime(Path(tmp))
            saved = write_operator_settings_bundle(
                {
                    "llmProviderChain": [
                        self._provider("primary-fast", "MiniMax-M3", api_key=first_key),
                        self._provider("fallback-steady", "MiniMax-M2.5", api_key=second_key),
                    ]
                },
                paths,
            )
            raw_text = (paths.config_dir / "settings.json").read_text(encoding="utf-8")
            raw = json.loads(raw_text)
            resolved = resolve_llm_provider_chain(paths)

        self.assertEqual(raw["schemaVersion"], SETTINGS_SCHEMA_VERSION)
        self.assertNotIn(first_key, raw_text)
        self.assertNotIn(second_key, raw_text)
        self.assertEqual(
            [entry["entryId"] for entry in raw["llmProviderChain"]],
            ["primary-fast", "fallback-steady"],
        )
        self.assertEqual(raw["llmProvider"]["model"], "MiniMax-M3")
        self.assertEqual(raw["llmProvider"]["secretRef"], raw["llmProviderChain"][0]["secretRef"])
        self.assertNotEqual(
            raw["llmProviderChain"][0]["secretRef"],
            raw["llmProviderChain"][1]["secretRef"],
        )
        self.assertEqual([entry["apiKey"] for entry in resolved], [first_key, second_key])
        self.assertEqual([entry["role"] for entry in resolved], ["primary", "fallback"])
        self.assertTrue(all(entry["readiness"]["ready"] for entry in resolved))
        self.assertEqual(saved["llmProvider"]["model"], "MiniMax-M3")

    def test_missing_fallback_key_is_unavailable_not_zero_or_silent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
        ), patch("data_foundation.settings.config.LLM_API_KEY", ""):
            paths = self._runtime(Path(tmp))
            write_operator_settings_bundle(
                {
                    "llmProviderChain": [
                        self._provider("primary", "MiniMax-M3", api_key="primary-key"),
                        self._provider("fallback-missing", "MiniMax-M2.5"),
                    ]
                },
                paths,
            )
            chain = resolve_llm_provider_chain(paths, redact_secrets=True)
            error = llm_provider_chain_readiness_error(paths)

        self.assertTrue(chain[0]["readiness"]["ready"])
        self.assertFalse(chain[1]["readiness"]["ready"])
        self.assertEqual(chain[1]["readiness"]["status"], "missing-configuration")
        self.assertEqual(chain[0]["apiKey"], MASKED_SECRET)
        self.assertIn("fallback-missing", error or "")

    def test_memory_fallback_fails_cross_process_readiness(self):
        with tempfile.TemporaryDirectory() as tmp, patch("data_foundation.settings.config.LLM_API_KEY", ""):
            paths = self._runtime(Path(tmp))
            with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "runtime-file"}):
                write_operator_settings_bundle(
                    {
                        "llmProviderChain": [
                            self._provider("primary", "MiniMax-M3", api_key="primary-key"),
                        ]
                    },
                    paths,
                )
            with patch.dict(os.environ, {"ACTANARA_SECRET_BACKEND": "memory"}):
                write_operator_settings_bundle(
                    {
                        "llmProviderChain": [
                            self._provider("primary", "MiniMax-M3", api_key=MASKED_SECRET),
                            self._provider("fallback-memory", "MiniMax-M2.5", api_key="memory-key"),
                        ]
                    },
                    paths,
                )
                chain = resolve_llm_provider_chain(
                    paths,
                    False,
                    True,
                )
                error = llm_provider_chain_readiness_error(
                    paths,
                    True,
                )

        self.assertTrue(chain[0]["readiness"]["ready"])
        self.assertEqual(
            chain[1]["readiness"]["status"],
            "cross-process-secret-unavailable",
        )
        self.assertIn("fallback-memory", error or "")
        self.assertIn("memory", error or "")


if __name__ == "__main__":
    unittest.main()
