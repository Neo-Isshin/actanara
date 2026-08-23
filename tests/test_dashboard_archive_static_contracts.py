import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "src/dashboard/app/static/archive"


class DashboardArchiveStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ARCHIVE / "index.html").read_text(encoding="utf-8")
        cls.script = (ARCHIVE / "app.js").read_text(encoding="utf-8")
        cls.api = (ARCHIVE / "archive-api.js").read_text(encoding="utf-8")
        cls.css = (ARCHIVE / "style.css").read_text(encoding="utf-8")

    def test_archive_uses_independent_local_api_and_same_origin_links(self):
        self.assertIn("const BASE = '/api/archive/v1'", self.api)
        self.assertIn("/api/diary-list?envelope=true", self.api)
        self.assertIn("/api/memory/search", self.api)
        self.assertIn('href="/tasks"', self.html)
        self.assertNotIn("http://127.0.0.1:3036", self.html + self.api)

    def test_core_asset_language_and_activity_boundary_are_explicit(self):
        self.assertIn("经验 Experience", self.html)
        self.assertIn("实践 Practice", self.html)
        self.assertIn("Reference 与 Discard 只属于证据治理", self.html)
        self.assertIn("HISTORICAL LEDGER · NOT AN ASSET SCORE", self.html)
        self.assertIn("loadArchiveData();", self.script)

    def test_visible_markup_does_not_ship_the_old_demo_snapshot(self):
        for stale_demo_value in (
            "24,801",
            "2,019",
            "1,516",
            "Job #694",
            "10.9 GB",
            "09 AUGUST 2026",
        ):
            self.assertNotIn(stale_demo_value, self.html, stale_demo_value)

    def test_preview_mutations_disclose_their_boundary(self):
        self.assertIn("Preview 不会创建文件", self.html)
        self.assertIn("不会修改本机索引", self.html)
        self.assertIn("未修改本机数据", self.script)

    def test_rich_visual_and_mobile_contract_remains_present(self):
        for marker in (
            "share-preview",
            "skill-pass-chart",
            "formation-landscape",
            "model-bars",
            "archiveCalendar",
            "mobile-bottom-nav",
            "@media (max-width: 380px)",
        ):
            self.assertIn(marker, self.html + self.css + self.script)

    def test_macos_and_linux_payload_gates_include_archive_entrypoint(self):
        mac = (ROOT / "install/install.sh").read_text(encoding="utf-8")
        linux = (ROOT / "install/install_linux.py").read_text(encoding="utf-8")
        for asset in ("archive/index.html", "archive/style.css", "archive/archive-api.js", "archive/app.js"):
            self.assertIn(asset, mac)
            self.assertIn(asset, linux)


if __name__ == "__main__":
    unittest.main()
