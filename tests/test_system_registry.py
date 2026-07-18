import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from data_foundation.paths import initialize_home
from data_foundation.system_registry import register_default_system_components, system_registry_status

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


class SystemRegistryTests(unittest.TestCase):
    def test_default_system_registry_registers_core_surfaces_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            status = register_default_system_components(paths)
            second = register_default_system_components(paths)

        self.assertEqual(status["status"], "ok")
        self.assertEqual(second["counts"]["components"], status["counts"]["components"])
        components = {item["componentKey"]: item for item in status["components"]}
        for key in {
            "dashboard.server",
            "rag.v2",
            "foundation.pipeline",
            "foundation.sqlite",
            "skills.inventory",
            "agents.inventory",
            "llm.provider.catalog",
        }:
            self.assertIn(key, components)
        self.assertFalse(status["authority"]["writesAllowed"])
        self.assertFalse(status["authority"]["taskWriteAllowed"])
        self.assertFalse(status["authority"]["promptMutationAllowed"])
        self.assertIn("read-only-search", components["rag.v2"]["capabilities"])
        self.assertIn("settings-api", components["dashboard.server"]["capabilities"])

    def test_system_registry_status_is_readonly_after_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = initialize_home(Path(tmp) / "Actanara")
            register_default_system_components(paths)
            status = system_registry_status(paths)

        self.assertEqual(status["parseStatus"], "ok")
        self.assertGreaterEqual(status["counts"]["byType"]["rag"], 1)
        self.assertGreaterEqual(status["counts"]["byType"]["dashboard"], 1)

    @unittest.skipUnless(FASTAPI_AVAILABLE, "Dashboard runtime dependency fastapi is not installed in this interpreter")
    def test_foundation_ops_system_registry_router_uses_service_facade(self):
        from app.routers import foundation_ops as ops_router

        with patch.object(ops_router.foundation_ops, "get_system_registry_status", return_value={"ok": True}) as status:
            response = asyncio.run(ops_router.api_foundation_system_registry_status())

        self.assertEqual(response, {"ok": True})
        status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
