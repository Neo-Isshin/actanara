import asyncio
import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "dashboard"))


CLASSIC_ASSET_SHA256 = {
    "src/dashboard/app/static/index.html": "7c53d367b2764320db3e0aed07af3d34766acdbb6cb59ed82d31860abf9dd6ae",
    "src/dashboard/app/static/css/style.css": "e664cbc2c7b28283ea0b3daea4b359ab3ffdceed8c9ca932a4c74e15e4975c07",
    "src/dashboard/app/static/js/app.js": "a144874d193ff22e0dddda41fae109184169627f96ba2b190c539750384e20f6",
    "src/dashboard/app/static/tasks.html": "71407fd1521a798e2a3ef8654eed0fc524aea065352ec9d424e2f82fbcc5e986",
}


class DashboardArchiveRouteTests(unittest.TestCase):
    def test_classic_dashboard_assets_remain_byte_identical_to_archive_baseline(self):
        for relative, expected in CLASSIC_ASSET_SHA256.items():
            payload = (ROOT / relative).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), expected, relative)

    def test_archive_static_assets_are_packaged_separately(self):
        archive = ROOT / "src/dashboard/app/static/archive"
        for name in ("index.html", "style.css", "editorial.css", "extended.css", "app.js", "archive-extended.js", "daily-console.css", "daily-console.js"):
            self.assertTrue((archive / name).is_file(), name)
        package_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"static/archive/*.html"', package_config)
        self.assertIn('"static/archive/*.css"', package_config)
        self.assertIn('"static/archive/*.js"', package_config)

    def test_classic_and_archive_routes_coexist(self):
        from app import main as dashboard_main

        classic = asyncio.run(dashboard_main.dashboard())
        recovery = asyncio.run(dashboard_main.dashboard_classic())
        archive = asyncio.run(dashboard_main.dashboard_preview())

        self.assertEqual(classic.headers["location"], "/static/index.html")
        self.assertEqual(recovery.headers["location"], "/static/index.html")
        self.assertEqual(archive.headers["location"], "/static/archive/index.html")
        main_source = (ROOT / "src/dashboard/app/main.py").read_text(encoding="utf-8")
        self.assertIn('app.include_router(archive.router, prefix="/api"', main_source)

    def test_archive_route_bootstraps_the_dashboard_session(self):
        from app.services.dashboard_security import should_bootstrap_session

        self.assertTrue(should_bootstrap_session("/dashboard", "GET"))
        self.assertTrue(should_bootstrap_session("/dashboard-classic", "GET"))
        self.assertTrue(should_bootstrap_session("/dashboard-preview", "GET"))
        self.assertTrue(should_bootstrap_session("/static/archive/index.html", "GET"))


if __name__ == "__main__":
    unittest.main()
