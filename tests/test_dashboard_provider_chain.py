import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import settings as settings_router
from app.services import settings as dashboard_settings
from data_foundation.paths import initialize_home
from data_foundation.settings import MASKED_SECRET, read_settings
from data_foundation.settings_transaction import SettingsTransactionError


class DashboardProviderChainTests(unittest.TestCase):
    def _runtime(self, root: Path):
        paths = initialize_home(root / "Actanara", legacy_diary_root=root / "Diary")
        read_settings(paths)
        return paths

    def _provider(self, entry_id: str, model: str, api_key: str = "") -> dict:
        return {
            "entryId": entry_id,
            "mode": "preset",
            "provider": "minimax-cn",
            "model": model,
            "apiKey": api_key,
            "apiKeyEnv": f"ACTANARA_TEST_{entry_id.upper().replace('-', '_')}_KEY",
        }

    def test_get_projects_legacy_provider_as_redacted_ordered_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._runtime(Path(tmp))
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False):
                result = dashboard_settings.get_llm_provider_chain()

        self.assertEqual(len(result["providers"]), 1)
        self.assertEqual(result["providers"][0]["entryId"], "legacy-primary")
        self.assertEqual(result["providers"][0]["order"], 0)
        self.assertEqual(result["providers"][0]["role"], "primary")
        self.assertEqual(result["providers"][0]["apiKey"], "")
        self.assertFalse(result["providers"][0]["hasApiKey"])
        self.assertFalse(result["providers"][0]["hasSavedApiKey"])
        self.assertNotIn("secretRef", result["providers"][0])
        self.assertGreater(len(result["catalog"]), 1)
        self.assertFalse(result["readiness"]["ready"])

    def test_put_preserves_order_and_isolates_duplicate_vendor_entries(self):
        first_key = "dashboard-primary-key"
        second_key = "dashboard-fallback-key"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False):
                result = dashboard_settings.update_llm_provider_chain(
                    {
                        "providers": [
                            self._provider("primary-fast", "MiniMax-M3", first_key),
                            self._provider("fallback-steady", "MiniMax-M2.5", second_key),
                        ]
                    }
                )
            raw_text = (paths.config_dir / "settings.json").read_text(encoding="utf-8")
            raw = json.loads(raw_text)

        self.assertEqual(
            [entry["entryId"] for entry in result["providers"]],
            ["primary-fast", "fallback-steady"],
        )
        self.assertEqual([entry["role"] for entry in result["providers"]], ["primary", "fallback"])
        self.assertTrue(all(entry["hasApiKey"] for entry in result["providers"]))
        self.assertTrue(all(entry["hasSavedApiKey"] for entry in result["providers"]))
        self.assertTrue(result["readiness"]["ready"])
        self.assertEqual(result["legacyPrimary"]["model"], "MiniMax-M3")
        self.assertEqual(raw["llmProvider"]["model"], "MiniMax-M3")
        self.assertNotEqual(
            raw["llmProviderChain"][0]["secretRef"],
            raw["llmProviderChain"][1]["secretRef"],
        )
        self.assertNotIn(first_key, raw_text)
        self.assertNotIn(second_key, raw_text)
        self.assertEqual(result["settingsTransaction"]["status"], "committed")

    def test_put_rejects_missing_fallback_key_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            settings_path = paths.config_dir / "settings.json"
            before = settings_path.read_bytes()
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False):
                response = asyncio.run(
                    settings_router.api_update_llm_provider_chain(
                        {
                            "providers": [
                                self._provider("primary", "MiniMax-M3", "primary-key"),
                                self._provider("fallback-missing", "MiniMax-M2.5"),
                            ]
                        }
                    )
                )
            after = settings_path.read_bytes()

        self.assertEqual(response.status_code, 400)
        self.assertIn("fallback-missing", response.body.decode("utf-8"))
        self.assertIn("missing apiKey", response.body.decode("utf-8"))
        self.assertEqual(after, before)

    def test_put_rejects_memory_backend_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "memory"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            settings_path = paths.config_dir / "settings.json"
            before = settings_path.read_bytes()
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False):
                response = asyncio.run(
                    settings_router.api_update_llm_provider_chain(
                        {
                            "providers": [
                                self._provider("primary-memory", "MiniMax-M3", "memory-key"),
                            ]
                        }
                    )
                )
            after = settings_path.read_bytes()

        self.assertEqual(response.status_code, 400)
        self.assertIn("memory backend", response.body.decode("utf-8"))
        self.assertEqual(after, before)

    def test_candidate_probe_never_persists_candidate_secret(self):
        candidate = self._provider("candidate", "MiniMax-M3", "candidate-only-key")
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._runtime(Path(tmp))
            settings_path = paths.config_dir / "settings.json"
            before = settings_path.read_bytes()
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False),
                patch.object(
                    dashboard_settings,
                    "check_llm_provider_availability",
                    return_value={"ok": True, "status": "ok", "hasApiKey": True},
                ) as probe,
            ):
                result = dashboard_settings.test_llm_provider_chain_entry(candidate)
            after = settings_path.read_bytes()

        self.assertTrue(result["ok"])
        self.assertFalse(result["persisted"])
        self.assertEqual(result["entryId"], "candidate")
        self.assertEqual(after, before)
        self.assertEqual(probe.call_args.kwargs["candidate"], candidate)
        self.assertEqual(probe.call_args.args[0].home, paths.home)

    def test_candidate_probe_reuses_the_selected_entry_secret_by_entry_id(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            with patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False):
                saved = dashboard_settings.update_llm_provider_chain(
                    {
                        "providers": [
                            self._provider("primary-fast", "MiniMax-M3", "primary-key"),
                            self._provider("fallback-steady", "MiniMax-M2.5", "fallback-key"),
                        ]
                    }
                )
                fallback = {**saved["providers"][1], "apiKey": ""}
                with patch.object(
                    dashboard_settings,
                    "check_llm_provider_availability",
                    return_value={"ok": True, "status": "ok"},
                ) as probe:
                    result = dashboard_settings.test_llm_provider_chain_entry(fallback)

        self.assertTrue(result["ok"])
        self.assertEqual(probe.call_args.kwargs["candidate"]["apiKey"], "fallback-key")
        self.assertNotEqual(probe.call_args.kwargs["candidate"]["apiKey"], "primary-key")

    def test_router_surfaces_structured_transaction_failure(self):
        failure = SettingsTransactionError(
            {
                "id": "provider-chain-transaction",
                "phase": "verification",
                "status": "failed",
                "conflict": False,
                "compensation": {"status": "compensated"},
            }
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False),
                patch.object(
                    dashboard_settings,
                    "write_operator_settings_bundle",
                    side_effect=failure,
                ),
            ):
                response = asyncio.run(
                    settings_router.api_update_llm_provider_chain(
                        {
                            "providers": [
                                self._provider("primary", "MiniMax-M3", "primary-key"),
                            ]
                        }
                    )
                )
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["settingsTransaction"]["id"], "provider-chain-transaction")
        self.assertEqual(payload["settingsTransaction"]["compensation"]["status"], "compensated")

    def test_readiness_verification_failure_rolls_back_chain_transaction(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"ACTANARA_SECRET_BACKEND": "runtime-file"},
            clear=False,
        ):
            paths = self._runtime(Path(tmp))
            settings_path = paths.config_dir / "settings.json"
            before = settings_path.read_bytes()
            with (
                patch.dict(os.environ, {"ACTANARA_HOME": str(paths.home)}, clear=False),
                patch.object(
                    dashboard_settings,
                    "_raise_if_llm_provider_chain_not_pipeline_ready",
                    side_effect=ValueError("synthetic fallback readiness failure"),
                ),
                self.assertRaises(SettingsTransactionError) as raised,
            ):
                dashboard_settings.update_llm_provider_chain(
                    {
                        "providers": [
                            self._provider("primary", "MiniMax-M3", "primary-key"),
                        ]
                    }
                )
            after = settings_path.read_bytes()

        self.assertEqual(after, before)
        self.assertEqual(raised.exception.summary["compensation"]["status"], "compensated")
        self.assertEqual(
            raised.exception.summary["compensation"]["secretCleanup"],
            "deleted-or-absent",
        )


if __name__ == "__main__":
    unittest.main()
