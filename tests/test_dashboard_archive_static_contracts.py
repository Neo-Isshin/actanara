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
        cls.editorial = (ARCHIVE / "editorial.css").read_text(encoding="utf-8")
        cls.extended = (ARCHIVE / "archive-extended.js").read_text(encoding="utf-8")
        cls.extended_css = (ARCHIVE / "extended.css").read_text(encoding="utf-8")
        cls.daily_console = (ARCHIVE / "daily-console.js").read_text(encoding="utf-8")
        cls.daily_console_css = (ARCHIVE / "daily-console.css").read_text(encoding="utf-8")

    def test_archive_uses_independent_local_api_and_same_origin_links(self):
        self.assertIn("const BASE = '/api/archive/v1'", self.api)
        self.assertIn("/api/diary-list?envelope=true", self.api)
        self.assertIn("/api/memory/search", self.api)
        self.assertIn('href="/tasks"', self.html)
        self.assertNotIn("http://127.0.0.1:3036", self.html + self.api)

    def test_core_asset_language_and_activity_boundary_are_explicit(self):
        self.assertIn("经验 Experience", self.html)
        self.assertIn("实践 Practice", self.html)
        self.assertIn("Reference 与 Discard 只用于内部审核", self.html)
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
        self.assertIn("同步会调用现有资产刷新服务", self.html)
        self.assertIn("当前服务仍未提供覆盖恢复接口", self.html)
        self.assertIn("未修改本机数据", self.script)

    def test_functional_copy_uses_clear_chinese_instead_of_archive_metaphors(self):
        combined = self.html + self.script
        for clear_copy in (
            "今日概览",
            "最近新增与更新",
            "资产形成流程",
            "最近生成的资产",
            "Agent 数据更新时间",
            "跨 Agent 记忆搜索",
            "日记与报告",
            "数据更新时间",
        ):
            self.assertIn(clear_copy, combined)
        for ambiguous_copy in (
            "馆藏正在发生",
            "资产装订链",
            "最近入馆",
            "来源新鲜度",
            "投影新鲜度",
            "记忆索引室",
            "检索全部馆藏",
            "正在打开馆藏",
            "档案柜",
        ):
            self.assertNotIn(ambiguous_copy, combined)

    def test_visible_status_labels_are_chinese(self):
        for marker in (
            "ready: '正常'",
            "empty: '暂无数据'",
            "stale: '需要更新'",
            "unsupported: '暂不支持'",
            "error: '读取失败'",
        ):
            self.assertIn(marker, self.script)

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

    def test_selected_editorial_design_is_the_single_canonical_preview(self):
        self.assertIn('data-theme="editorial"', self.html)
        self.assertIn('href="editorial.css"', self.html)
        self.assertIn('data-theme="editorial"', self.editorial)
        self.assertNotIn("data-theme-choice", self.html)
        self.assertNotIn("theme-switcher.js", self.html)

    def test_archive_adopts_classic_runtime_and_skill_pass_capabilities(self):
        for marker in (
            "data-route=\"operations\"",
            "data-route=\"rag\"",
            "data-route=\"settings\"",
            "skillPassReviewPanel",
            "liveTokenKpis",
            "extendedRagSearchForm",
            "extendedLlmChain",
            "archiveMessageList",
            "archiveTaskList",
        ):
            self.assertIn(marker, self.html)
        for marker in (
            "/api/token-clock",
            "/api/msgbox?limit=20",
            "/api/background-tasks?limit=20",
            "/api/ai-assets/skill-assets",
            "/api/ai-assets/skill-assets/crystallize",
            "/api/ai-assets/skill-assets/register",
            "/api/rag/status?probe=false",
            "/api/rag/coverage",
            "/api/llm-provider-chain",
            "/api/ai-assets/backups/run",
            "/api/foundation/history-backfill",
        ):
            self.assertIn(marker, self.extended)
        for backup_scope in ("database", "diaryMarkdown", "periodReports", "ragV2", "settings", "runtimeManifests"):
            self.assertIn(f'data-backup-scope="{backup_scope}"', self.html)
        self.assertIn("extended.css", self.html)

    def test_daily_console_has_three_review_blocks_and_safe_actions(self):
        for marker in (
            'id="page-console"',
            'data-page="console"',
            'data-route="console"',
            'dailyConsoleDate',
            'dailyConsoleAssets',
            'dailyConsoleTasks',
            'dailyConsoleLessons',
            'dailyConsoleAttention',
            'daily-console.css',
            'daily-console.js',
        ):
            self.assertIn(marker, self.html)
        self.assertIn("console: '每日决策台'", self.script)
        for marker in (
            "ASSET ADDITIONS",
            "NOVA-TASK PROPOSALS",
            "LESSON PROPOSALS",
            "/api/archive/v1/assets",
            "/api/tasks/l1-review",
            "/api/ai-assets/skill-assets",
            "/api/msgbox",
            "taskConfirm",
            "lessonCrystallize",
            "skillRegister",
            "actanara_dashboard_csrf",
            "[redacted]",
        ):
            self.assertIn(marker, self.daily_console)
        self.assertIn("daily-console-grid", self.daily_console_css)
        self.assertIn("@media (max-width: 720px)", self.daily_console_css)

    def test_macos_and_linux_payload_gates_include_archive_entrypoint(self):
        mac = (ROOT / "install/install.sh").read_text(encoding="utf-8")
        linux = (ROOT / "install/install_linux.py").read_text(encoding="utf-8")
        for asset in (
            "archive/index.html",
            "archive/style.css",
            "archive/editorial.css",
            "archive/extended.css",
            "archive/archive-api.js",
            "archive/app.js",
            "archive/archive-extended.js",
            "archive/daily-console.css",
            "archive/daily-console.js",
        ):
            self.assertIn(asset, mac)
            self.assertIn(asset, linux)


if __name__ == "__main__":
    unittest.main()
