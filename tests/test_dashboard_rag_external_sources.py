import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))

from app.routers import settings as settings_router
from app.services import settings as settings_service


class DashboardRagExternalSourcesTests(unittest.TestCase):
    def test_service_plans_candidate_external_settings_without_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            document = source / "release.md"
            document.write_text("# Release\n\nExternal source fixture.", encoding="utf-8")
            persisted = {
                "schemaVersion": 1,
                "rag": {
                    "enabled": True,
                    "mode": "v2",
                    "indexing": {
                        "externalSources": {
                            "enabled": False,
                            "mode": "supplement",
                            "paths": [],
                        }
                    },
                },
            }
            payload = {
                "rag": {
                    "indexing": {
                        "externalSources": {
                            "enabled": True,
                            "mode": "replace",
                            "paths": [str(source)],
                            "recursive": True,
                            "include": ["*.md"],
                            "exclude": [],
                            "maxFileBytes": 1024 * 1024,
                            "maxTotalBytes": 2 * 1024 * 1024,
                            "maxFiles": 10,
                            "symlinkPolicy": "reject",
                        }
                    }
                }
            }

            with patch.object(settings_service, "read_settings", return_value=persisted) as read:
                plan = settings_service.plan_rag_external_sources(payload)

            read.assert_called_once_with()
            self.assertTrue(plan["dryRun"])
            self.assertTrue(plan["canExecute"], plan)
            self.assertEqual(plan["mode"], "replace")
            self.assertEqual(plan["summary"]["sourceRecordCount"], 1)
            self.assertEqual(plan["sources"][0]["parserStatus"], "parsed")
            self.assertEqual(document.read_text(encoding="utf-8"), "# Release\n\nExternal source fixture.")

    def test_router_returns_plan_and_maps_validation_failure(self):
        expected = {"dryRun": True, "status": "plan"}
        with patch.object(settings_router.settings, "plan_rag_external_sources", return_value=expected) as plan:
            response = asyncio.run(settings_router.api_rag_external_sources_plan({"rag": {}}))
        self.assertEqual(response, expected)
        plan.assert_called_once_with({"rag": {}})

        with patch.object(
            settings_router.settings,
            "plan_rag_external_sources",
            side_effect=ValueError("invalid fixture"),
        ):
            response = asyncio.run(settings_router.api_rag_external_sources_plan({"rag": {}}))
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
