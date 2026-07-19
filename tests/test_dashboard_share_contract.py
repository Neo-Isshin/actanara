import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dashboard" / "app" / "static"


class DashboardShareContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.script = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "css" / "style.css").read_text(encoding="utf-8")

    def test_weekly_monthly_and_ai_assets_expose_share_buttons(self):
        self.assertIn('id="mrShareBtn"', self.html)
        self.assertIn("openReportSharePreview('monthly', 'mr')", self.html)
        self.assertIn('id="aiAssetsShareBtn"', self.html)
        self.assertIn("openAiAssetsSharePreview()", self.html)
        weekly = self.script.split("async function loadReport(reportId, navEl)", 1)[1].split(
            "// ─── 月报加载", 1
        )[0]
        self.assertIn("_shareBtn", weekly)
        self.assertIn("openReportSharePreview('weekly'", weekly)
        self.assertIn("registerReportSharePayload(prefix, 'weekly'", weekly)
        self.assertIn("registerReportSharePayload(MR_PREFIX, 'monthly'", self.script)
        self.assertIn("registerAiAssetsSharePayload(d)", self.script)
        self.assertGreaterEqual(self.script.count("clearSharePayload("), 3)

    def test_icons_are_local_lucide_compatible_svg(self):
        icon_body = self.script.split("function shareIconSvg(name)", 1)[1].split(
            "function hydrateShareIcons", 1
        )[0]
        for icon in ("share", "image", "copy", "download"):
            self.assertIn(f"{icon}:", icon_body)
        self.assertIn('viewBox="0 0 24 24"', icon_body)
        self.assertIn('stroke="currentColor"', icon_body)
        self.assertIn('stroke-linecap="round"', icon_body)
        self.assertNotIn("https://", icon_body)
        self.assertIn("hydrateShareIcons(document)", self.script)

    def test_share_payload_is_a_new_numeric_allowlist(self):
        report_builder = self.script.split("function buildReportSharePayload", 1)[1].split(
            "function buildAiAssetsSharePayload", 1
        )[0]
        assets_builder = self.script.split("function buildAiAssetsSharePayload", 1)[1].split(
            "function setSharePayload", 1
        )[0]
        for builder in (report_builder, assets_builder):
            self.assertIn("schemaVersion: 1", builder)
            self.assertIn("metrics:", builder)
            self.assertIn("trend", builder)
            self.assertIn("outcomes:", builder)
            self.assertNotIn("...data", builder)
            self.assertNotIn("Object.assign", builder)

        for forbidden in (
            "periodSummary",
            "summaryTopics",
            "highFrequencyTopics",
            "topTopics",
            "lessons",
            "agentWork",
            "workspaceUsage",
            "modelUsage",
            "models",
            "agents",
            "agentTree",
            "infrastructure",
            "toolConfigs",
            "workspaceAttributionQa",
            "usageCache",
            "dataFreshness",
            "dashboardState",
            "sourceErrors",
            "rawContent",
            "secretRef",
        ):
            self.assertNotIn(forbidden, report_builder)
            self.assertNotIn(forbidden, assets_builder)

        self.assertIn("ACTANARA_SHARE_TOOL_NAMES.has", assets_builder)
        self.assertIn("['上午', '下午', '晚上', '凌晨']", assets_builder)
        self.assertNotIn("innerText", report_builder + assets_builder)
        self.assertNotIn("textContent", report_builder + assets_builder)
        self.assertNotIn("querySelector", report_builder + assets_builder)

    def test_renderer_has_bounded_canvas_and_text_layout(self):
        self.assertIn("const ACTANARA_SHARE_CANVAS_WIDTH = 1200", self.script)
        self.assertIn("const ACTANARA_SHARE_CANVAS_HEIGHT = 1500", self.script)
        self.assertIn("const ACTANARA_SHARE_MAX_EDGE = 4096", self.script)
        self.assertIn("const ACTANARA_SHARE_MAX_PIXELS = 4000000", self.script)
        renderer = self.script.split("function renderActanaraShareCanvas(payload)", 1)[1].split(
            "function shareCanvasBlob", 1
        )[0]
        self.assertIn("width * height > ACTANARA_SHARE_MAX_PIXELS", renderer)
        self.assertIn("canvas.width = width", renderer)
        self.assertIn("canvas.height = height", renderer)
        self.assertIn("getContext('2d', { alpha: false })", renderer)
        self.assertIn("ACTANARA_SHARE_PALETTES", renderer)
        self.assertIn("payload.metrics.slice(0, 4)", renderer)
        self.assertIn("payload.outcomes.slice(0, 3)", renderer)
        self.assertNotIn("drawImage", renderer)
        self.assertNotIn("html2canvas", self.script.lower())
        self.assertNotIn("dom-to-image", self.script.lower())

        wrap = self.script.split("function shareTextTokens", 1)[1].split(
            "function shareSetFont", 1
        )[0]
        self.assertIn("Intl.Segmenter", wrap)
        self.assertIn("Array.from(token)", wrap)
        self.assertIn("shareEllipsize", wrap)
        self.assertIn("maximumLines", wrap)
        self.assertIn("normalize('NFC')", self.script)
        for font in ("PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC"):
            self.assertIn(font, self.script)

    def test_preview_copy_download_and_cleanup_states_are_explicit(self):
        self.assertIn("data-share-state=\"preparing\"", self.script)
        self.assertIn("data-share-state=\"ready\"", self.script)
        self.assertIn("data-share-state=\"error\"", self.script)
        self.assertIn("aria-live=\"polite\"", self.script)
        self.assertIn("new ClipboardItem({ 'image/png': ACTANARA_SHARE_PREVIEW.blob })", self.script)
        self.assertIn("navigator.clipboard.write", self.script)
        self.assertIn("downloadButton.focus()", self.script)
        self.assertIn("anchor.download = `actanara-${payload.kind}-${stamp}.png`", self.script)
        self.assertIn("anchor.remove()", self.script)
        self.assertIn("canvas.toBlob", self.script)
        self.assertIn("URL.createObjectURL(blob)", self.script)
        self.assertIn("URL.revokeObjectURL", self.script)
        close_body = self.script.split("function closeModal()", 1)[1].split(
            "function modalBack()", 1
        )[0]
        self.assertIn("releaseActanaraSharePreview()", close_body)
        self.assertIn("dashboardModalGenerationIsCurrent(generation)", self.script)

    def test_share_text_theme_and_mobile_contracts_cover_both_languages(self):
        for text in (
            "sharePng: '分享图片'",
            "sharePng: 'Share PNG'",
            "sharePreparing: '正在本地生成 PNG…'",
            "sharePreparing: 'Generating PNG locally...'",
            "shareClipboardUnavailable:",
            "sharePrivacyNote:",
            "shareThemeLight:",
            "shareThemeDark:",
        ):
            self.assertIn(text, self.script)
        self.assertIn("light: Object.freeze", self.script)
        self.assertIn("dark: Object.freeze", self.script)
        self.assertIn("prefers-color-scheme: dark", self.script)
        self.assertIn(".share-preview-image-wrap", self.css)
        self.assertIn(".share-preview-actions", self.css)
        final_mobile = self.css.rsplit("@media (max-width: 720px)", 1)[1]
        self.assertIn(".share-preview-toolbar", final_mobile)
        self.assertIn("flex-direction: column", final_mobile)
        self.assertIn(".share-preview-actions", final_mobile)
        self.assertIn("grid-template-columns: 1fr 1fr", final_mobile)
        self.assertIn("flex-wrap: wrap !important", final_mobile)


if __name__ == "__main__":
    unittest.main()
