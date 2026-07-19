import { expect, test } from "@playwright/test";
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { withBrowserContext } from "./dashboard-live-context.js";

const repoRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const evidenceDir = path.join(repoRoot, "output", "playwright");
const forbiddenMarker = "ACTANARA-SHARE-FORBIDDEN-MARKER";
const forbiddenPayloadKeys = new Set([
  "absolutePath",
  "agentTree",
  "agents",
  "apiKey",
  "dashboardState",
  "dataFreshness",
  "headers",
  "highFrequencyTopics",
  "infrastructure",
  "lessons",
  "models",
  "path",
  "periodSummary",
  "rawContent",
  "secret",
  "secretRef",
  "sourceErrors",
  "summaryTopics",
  "toolConfigs",
  "topTopics",
  "workspace",
  "workspaceUsage",
]);

let dashboardProcess = null;
let dashboardOrigin = "";
let isolatedRoot = "";
let serverOutput = "";

test.describe.configure({ mode: "serial" });

function listenPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(error => error ? reject(error) : resolve(address.port));
    });
  });
}

async function waitForDashboard(process, origin) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (process.exitCode !== null) {
      throw new Error(`isolated Dashboard exited before readiness (code ${process.exitCode})\n${serverOutput.slice(-4000)}`);
    }
    try {
      const response = await fetch(`${origin}/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok && (await response.json()).status === "ok") return;
    } catch (_) {
      // The loopback listener may not have bound yet.
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`isolated Dashboard did not become ready\n${serverOutput.slice(-4000)}`);
}

async function stopDashboard() {
  if (!dashboardProcess || dashboardProcess.exitCode !== null) return;
  const exited = new Promise(resolve => dashboardProcess.once("exit", resolve));
  dashboardProcess.kill("SIGTERM");
  const graceful = await Promise.race([
    exited.then(() => true),
    new Promise(resolve => setTimeout(() => resolve(false), 5000)),
  ]);
  if (!graceful && dashboardProcess.exitCode === null) {
    dashboardProcess.kill("SIGKILL");
    await exited;
  }
}

function initializeIsolatedRuntime(root) {
  const runtime = path.join(root, "Actanara");
  const config = path.join(runtime, "config");
  fs.mkdirSync(config, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(config, "runtime.json"), JSON.stringify({
    instanceId: "playwright-share-isolated",
    schemaVersion: 1,
    createdAt: "2026-07-19T00:00:00+08:00",
    generatedDiaryRoot: path.join(runtime, "artifacts", "diary"),
    ragMode: "v2",
  }, null, 2) + "\n", { mode: 0o600 });
  fs.writeFileSync(path.join(config, "settings.json"), JSON.stringify({
    schemaVersion: 1,
    general: { locale: "zh-CN", timezone: "Asia/Hong_Kong" },
    pipeline: { languageProfile: "zh" },
    schedule: { enabled: false, timezone: "Asia/Hong_Kong" },
    dataBackup: { enabled: false },
  }, null, 2) + "\n", { mode: 0o600 });
  return runtime;
}

test.beforeAll(async () => {
  isolatedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "actanara-dashboard-share-"));
  const runtime = initializeIsolatedRuntime(isolatedRoot);
  const port = await listenPort();
  dashboardOrigin = `http://127.0.0.1:${port}`;
  const pythonPath = [path.join(repoRoot, "src"), path.join(repoRoot, "src", "dashboard")]
    .concat(process.env.PYTHONPATH ? [process.env.PYTHONPATH] : [])
    .join(path.delimiter);
  dashboardProcess = spawn(
    path.join(repoRoot, ".venv", "bin", "python"),
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
    {
      cwd: path.join(repoRoot, "src", "dashboard"),
      env: {
        ...process.env,
        ACTANARA_HOME: runtime,
        ACTANARA_LOCATION_FILE: path.join(isolatedRoot, "location.json"),
        PYTHONPATH: pythonPath,
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  const capture = chunk => {
    serverOutput = (serverOutput + chunk.toString("utf8")).slice(-8000);
  };
  dashboardProcess.stdout.on("data", capture);
  dashboardProcess.stderr.on("data", capture);
  await waitForDashboard(dashboardProcess, dashboardOrigin);
  fs.mkdirSync(evidenceDir, { recursive: true, mode: 0o700 });
});

test.afterAll(async () => {
  await stopDashboard();
  if (isolatedRoot) {
    const resolved = path.resolve(isolatedRoot);
    const temp = path.resolve(os.tmpdir());
    if (!resolved.startsWith(temp + path.sep) || !path.basename(resolved).startsWith("actanara-dashboard-share-")) {
      throw new Error("refusing to remove an unrecognized Playwright runtime directory");
    }
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

function dashboardState(status = "ready") {
  return { schemaVersion: 1, status, sourceErrors: [] };
}

function reportFixture(url) {
  const start = url.searchParams.get("start") || "2026-06-29";
  const days = Math.max(1, Number(url.searchParams.get("days") || 7));
  const first = new Date(`${start}T00:00:00Z`);
  const series = Array.from({ length: Math.min(days, 31) }, (_, index) => {
    const date = new Date(first);
    date.setUTCDate(date.getUTCDate() + index);
    return {
      date: date.toISOString().slice(0, 10),
      tokens: 9000 + index * 1375,
      messages: 12 + index,
      cacheHitRate: 76 + index / 3,
    };
  });
  const end = series[series.length - 1].date;
  return {
    period: `${start} ~ ${end}`,
    days,
    kpi: {
      totalTokens: 456789,
      totalMessages: 321,
      totalApiCalls: 45,
      activeSessions: 17,
      totalSessions: 19,
      cacheHitRate: 82.4,
      cronSuccessRate: 96.8,
      agentCount: 4,
    },
    dailyTokenSeries: series,
    models: [],
    workspaceUsage: [],
    agentActivity: {},
    workloadComparison: {
      totalTokens: { delta: 56789, deltaPercent: 14.2 },
      totalMessages: { delta: 24, deltaPercent: 8.1 },
      cacheHitRate: { delta: 3.2, deltaPercent: 3.2 },
    },
    taskStats: { completed: 9, inProgress: 2 },
    cronStats: { success: 30, failed: 1, rate: 96.8 },
    knowledgePeriod: {
      rag: { deltaCount: 27, totalCount: 180, startCount: 153 },
      memory: { deltaCount: 3, totalCount: 40, startCount: 37 },
    },
    highFrequencyTopics: [{ topic: forbiddenMarker, count: 99 }],
    summaryTopics: [forbiddenMarker],
    lessons: [{ agent: "test", problem: forbiddenMarker, suggestion: forbiddenMarker }],
    periodSummary: { lead: forbiddenMarker, highlights: [forbiddenMarker], lessons: [forbiddenMarker] },
    hourlyHeatmap: { dates: [], periods: [] },
    assetHourlyHeatmap: { dates: [], periods: [] },
    dataFreshness: { source: "fixture", absolutePath: `/private/${forbiddenMarker}` },
    apiKey: forbiddenMarker,
    secret: forbiddenMarker,
    secretRef: `env:${forbiddenMarker}`,
    headers: { Authorization: forbiddenMarker },
    rawContent: forbiddenMarker,
    dashboardState: dashboardState(),
  };
}

function aiAssetsFixture() {
  const trend30d = Array.from({ length: 14 }, (_, index) => ({
    date: `2026-07-${String(index + 1).padStart(2, "0")}`,
    slots: { "上午": 3200 + index * 100, "下午": 4200 + index * 120, "晚上": 2600 + index * 80, "凌晨": 400 },
  }));
  return {
    totalTokens: 987654,
    totalMessages: 654,
    agentCount: 6,
    activeDayCount: 28,
    tools: [{
      name: "Codex",
      emoji: "🤖",
      allTimeTokens: 987654,
      todayTokens: 24000,
      todayMessages: 18,
      firstActivity: "2026-06-01",
      lastActivity: "2026-07-19",
    }],
    trend30d,
    diary: { count: 48, firstDate: "2026-06-01", lastDate: "2026-07-19", totalWords: 86000 },
    rag: { entries: 315, status: "ready" },
    cronJobs: { total: 24, success: 23, failed: 1, successRate: 95.8 },
    agents: [{
      name: forbiddenMarker,
      displayName: forbiddenMarker,
      model: "fixture-model",
      sessionCount: 2,
      totalMessages: 22,
      lastActive: "2026-07-19",
      source: "fixture",
      workspace: `/private/${forbiddenMarker}`,
    }],
    agentTree: [],
    workspaceUsage: [{ name: forbiddenMarker, path: `/private/${forbiddenMarker}` }],
    models: [{ name: forbiddenMarker }],
    infrastructure: {
      devices: [{ name: forbiddenMarker, path: `/private/${forbiddenMarker}` }],
      services: [],
    },
    toolConfigs: [{ name: forbiddenMarker, path: `/private/${forbiddenMarker}`, ports: [] }],
    storage: { categories: [], tools: [] },
    skills: { byTool: {} },
    dataFreshness: { aiAssets: { source: "fixture", absolutePath: `/private/${forbiddenMarker}` } },
    apiKey: forbiddenMarker,
    secret: forbiddenMarker,
    secretRef: `env:${forbiddenMarker}`,
    headers: { Authorization: forbiddenMarker },
    rawContent: forbiddenMarker,
    updatedAt: "2026-07-19T12:00:00+08:00",
    dashboardState: dashboardState(),
  };
}

function settingsFixture(language) {
  return {
    schemaVersion: 1,
    general: { locale: language === "en" ? "en-US" : "zh-CN", timezone: "Asia/Hong_Kong" },
    pipeline: { languageProfile: language },
    schedule: { enabled: false, timezone: "Asia/Hong_Kong" },
    features: {},
  };
}

async function installApiFixtures(page, language) {
  await page.route(/https:\/\/(?:fonts\.googleapis\.com|cdn\.jsdelivr\.net)\/.*/, route => route.abort());
  await page.route("**/events/**", route => route.abort());
  await page.route("**/api/**", async route => {
    const requestUrl = new URL(route.request().url());
    const pathname = requestUrl.pathname;
    let payload;
    if (pathname === "/api/settings") {
      payload = settingsFixture(language);
    } else if (pathname === "/api/diary-list") {
      const item = {
        date: "0701",
        fullDate: "2026-07-01",
        displayDate: "07-01",
        dayOfWeek: language === "en" ? "Wednesday" : "星期三",
        isBlankDay: false,
      };
      payload = requestUrl.searchParams.get("envelope") === "1"
        ? { items: [item], dashboardState: dashboardState() }
        : [item];
    } else if (pathname === "/api/weekly-report") {
      payload = reportFixture(requestUrl);
    } else if (pathname === "/api/ai-assets") {
      payload = aiAssetsFixture();
    } else if (pathname === "/api/token-clock") {
      payload = { tools: [], dashboardState: dashboardState("empty") };
    } else if (pathname === "/api/tokens") {
      payload = { summary: {}, today: {}, dashboardState: dashboardState() };
    } else if (pathname === "/api/tasks") {
      payload = { tasks: [], dashboardState: dashboardState("empty") };
    } else if (pathname === "/api/background-tasks") {
      payload = { activeCount: 0, tasks: [] };
    } else if (pathname === "/api/msgbox") {
      payload = { messages: [], unreadCount: 0 };
    } else {
      payload = { dashboardState: dashboardState("empty") };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function waitForShell(page, mobile) {
  await page.goto(`${dashboardOrigin}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.locator(mobile ? ".mobile-bottom-nav" : "aside.sidebar").waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => document.querySelector("#month-nav")?.querySelector("[data-report-id]"), null, { timeout: 20_000 });
}

function assertAllowlistedPayload(payload) {
  expect(payload).not.toBeNull();
  expect(JSON.stringify(payload)).not.toContain(forbiddenMarker);
  const visit = value => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    for (const [key, child] of Object.entries(value)) {
      expect(forbiddenPayloadKeys.has(key), `share payload exposed forbidden key ${key}`).toBeFalsy();
      visit(child);
    }
  };
  visit(payload);
  expect(Object.keys(payload).sort()).toEqual([
    "kind", "locale", "metrics", "outcomes", "range", "schemaVersion", "summary", "theme", "title", "trend",
  ]);
}

async function verifyPreview(page, key, language) {
  const image = page.locator("#actanaraSharePreviewImage");
  await image.waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => {
    const node = document.getElementById("actanaraSharePreviewImage");
    return node?.complete && node.naturalWidth > 0;
  }, null, { timeout: 20_000 });
  const imageState = await image.evaluate(node => {
    const sample = document.createElement("canvas");
    sample.width = 32;
    sample.height = 32;
    const context = sample.getContext("2d");
    context.drawImage(node, 0, 0, sample.width, sample.height);
    const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
    let opaque = 0;
    let nonWhite = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 3] > 0) opaque += 1;
      if (pixels[index + 3] > 0 && !(pixels[index] > 250 && pixels[index + 1] > 250 && pixels[index + 2] > 250)) nonWhite += 1;
    }
    return { width: node.naturalWidth, height: node.naturalHeight, opaque, nonWhite };
  });
  expect(imageState.width).toBe(1200);
  expect(imageState.height).toBe(1500);
  expect(imageState.opaque).toBeGreaterThan(0);
  expect(imageState.nonWhite).toBeGreaterThan(10);
  await expect(page.locator("#modal-title")).toHaveText(language === "en" ? "Share Image Preview" : "分享图片预览");
  await expect(page.locator("#actanaraShareCopyBtn")).toContainText(language === "en" ? "Copy PNG" : "复制 PNG");
  await expect(page.locator("#actanaraShareDownloadBtn")).toContainText(language === "en" ? "Save PNG" : "保存 PNG");
  const payload = await page.evaluate(payloadKey => actanaraSharePayload(payloadKey), key);
  assertAllowlistedPayload(payload);
  return payload;
}

async function closePreview(page) {
  await page.keyboard.press("Escape");
  await expect(page.locator("#modal")).toHaveAttribute("aria-hidden", "true");
}

async function savePreviewEvidence(page, filename) {
  if (!filename) return;
  const screenshotPath = path.join(evidenceDir, filename);
  await page.screenshot({ path: screenshotPath });
  fs.chmodSync(screenshotPath, 0o600);
  expect(fs.statSync(screenshotPath).size).toBeGreaterThan(0);
}

async function openWeeklyPreview(page, language, evidenceFilename = "") {
  const buttonId = await page.evaluate(() => {
    const entry = document.querySelector("[data-report-id]");
    entry.click();
    const reportId = entry.dataset.reportId;
    return `wr_${reportId.replace(/[^a-zA-Z0-9]/g, "_")}_shareBtn`;
  });
  const button = page.locator(`#${buttonId}`);
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();
  await verifyPreview(page, buttonId.replace(/_shareBtn$/, ""), language);
  await savePreviewEvidence(page, evidenceFilename);
  await closePreview(page);
}

async function openMonthlyPreview(page, language, evidenceFilename = "") {
  await page.evaluate(() => document.querySelector("[data-month-id]").click());
  const button = page.locator("#mrShareBtn");
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();
  await verifyPreview(page, "mr", language);
  await savePreviewEvidence(page, evidenceFilename);
  await closePreview(page);
}

async function openAiAssetsPreview(page, language, evidenceFilename = "") {
  await page.evaluate(() => showPage("static"));
  const button = page.locator("#aiAssetsShareBtn");
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();
  await verifyPreview(page, "ai-assets", language);
  await savePreviewEvidence(page, evidenceFilename);
}

async function verifyClipboardAndDownload(page, language) {
  await page.evaluate(() => {
    class IsolatedClipboardItem {
      constructor(items) {
        this.items = items;
        this.types = Object.keys(items);
      }
    }
    Object.defineProperty(window, "ClipboardItem", { configurable: true, value: IsolatedClipboardItem });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        write: async items => {
          const blob = items[0].items["image/png"];
          window.__shareClipboardWrite = { type: blob.type, size: blob.size, types: items[0].types };
        },
      },
    });
  });
  await page.locator("#actanaraShareCopyBtn").click();
  await expect(page.locator("#actanaraShareStatus")).toContainText(language === "en" ? "copied" : "已复制", { ignoreCase: true });
  const clipboard = await page.evaluate(() => window.__shareClipboardWrite);
  expect(clipboard.type).toBe("image/png");
  expect(clipboard.size).toBeGreaterThan(0);
  expect(clipboard.types).toContain("image/png");

  await page.evaluate(() => {
    Object.defineProperty(window, "ClipboardItem", { configurable: true, value: undefined });
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
  });
  await page.locator("#actanaraShareCopyBtn").click();
  await expect(page.locator("#actanaraShareStatus")).toContainText(language === "en" ? "cannot copy" : "无法复制");
  expect(await page.evaluate(() => document.activeElement?.id)).toBe("actanaraShareDownloadBtn");

  const downloadPending = page.waitForEvent("download");
  await page.locator("#actanaraShareDownloadBtn").click();
  const download = await downloadPending;
  expect(download.suggestedFilename()).toMatch(/^actanara-ai-assets-\d{4}-\d{2}-\d{2}\.png$/);
  const downloadedPath = await download.path();
  expect(downloadedPath).toBeTruthy();
  expect(fs.statSync(downloadedPath).size).toBeGreaterThan(0);
}

test("weekly, monthly, and AI Assets share locally across desktop/mobile zh/en", async ({ browser }, testInfo) => {
  test.setTimeout(3 * 60_000);
  test.skip(testInfo.project.name !== "chromium-desktop", "this isolated spec owns its desktop/mobile context matrix");
  const matrix = [
    { name: "desktop-zh", language: "zh", locale: "zh-CN", viewport: { width: 1440, height: 1000 }, mobile: false, colorScheme: "light" },
    { name: "desktop-en", language: "en", locale: "en-US", viewport: { width: 1440, height: 1000 }, mobile: false, colorScheme: "dark" },
    { name: "mobile-zh", language: "zh", locale: "zh-CN", viewport: { width: 412, height: 915 }, mobile: true, colorScheme: "light" },
    { name: "mobile-en", language: "en", locale: "en-US", viewport: { width: 412, height: 915 }, mobile: true, colorScheme: "dark" },
  ];

  for (const scenario of matrix) {
    await withBrowserContext(browser, {
      viewport: scenario.viewport,
      locale: scenario.locale,
      colorScheme: scenario.colorScheme,
      isMobile: scenario.mobile,
      hasTouch: scenario.mobile,
      acceptDownloads: true,
    }, async context => {
      const page = await context.newPage();
      const pageErrors = [];
      page.on("pageerror", error => pageErrors.push(error.message));
      await installApiFixtures(page, scenario.language);
      await waitForShell(page, scenario.mobile);
      await expect(page.locator("html")).toHaveAttribute("lang", scenario.language === "en" ? "en-US" : "zh-CN");

      const isPrimaryEvidence = scenario.name === "desktop-zh";
      await openWeeklyPreview(page, scenario.language, isPrimaryEvidence ? "weekly-share-preview.png" : "");
      await openMonthlyPreview(page, scenario.language, isPrimaryEvidence ? "monthly-share-preview.png" : "");
      await openAiAssetsPreview(page, scenario.language, isPrimaryEvidence ? "ai-assets-share-preview.png" : "");

      if (scenario.name === "desktop-zh") await verifyClipboardAndDownload(page, scenario.language);

      if (scenario.mobile) {
        const overflow = await page.evaluate(() => ({
          viewport: innerWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
          panel: document.getElementById("modal-panel")?.getBoundingClientRect().width || 0,
        }));
        expect(overflow.document).toBeLessThanOrEqual(overflow.viewport + 1);
        expect(overflow.body).toBeLessThanOrEqual(overflow.viewport + 1);
        expect(overflow.panel).toBeLessThanOrEqual(overflow.viewport);
      }

      await page.locator("#modal-panel").evaluate(async node => {
        await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {})));
      });
      const screenshotPath = path.join(evidenceDir, `dashboard-share-${scenario.name}.png`);
      await page.screenshot({ path: screenshotPath });
      fs.chmodSync(screenshotPath, 0o600);
      expect(fs.statSync(screenshotPath).size).toBeGreaterThan(0);
      expect(pageErrors.filter(message => /share png render failed/i.test(message))).toEqual([]);
      await closePreview(page);
    });
  }
});
