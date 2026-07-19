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
const confirmationText = "BACK UP ACTANARA DATA";
const completedBackupId = "actanara-backup-v1-20260719T081500Z-fixture";

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
    instanceId: "playwright-backup-isolated",
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
    backup: { schedule: { enabled: false } },
  }, null, 2) + "\n", { mode: 0o600 });
  return runtime;
}

test.beforeAll(async () => {
  isolatedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "actanara-dashboard-backup-"));
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
    if (!resolved.startsWith(temp + path.sep) || !path.basename(resolved).startsWith("actanara-dashboard-backup-")) {
      throw new Error("refusing to remove an unrecognized Playwright runtime directory");
    }
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

function dashboardState(status = "ready") {
  return { schemaVersion: 1, status, sourceErrors: [] };
}

function defaultSelection() {
  return {
    database: true,
    diaryMarkdown: true,
    periodReports: true,
    ragV2: true,
    novaTaskExports: true,
    settings: true,
    workspaceAttribution: true,
    runtimeManifests: true,
  };
}

function initialSettings() {
  return {
    targetDirectory: "/tmp/actanara-playwright-backups",
    include: defaultSelection(),
    retention: { maxBackups: 7, maxAgeDays: 30 },
    schedule: { enabled: false, frequency: "weekly", timeOfDay: "05:00" },
  };
}

function statusPayload(settings, latestRun = null) {
  return {
    schemaVersion: 1,
    settings: JSON.parse(JSON.stringify(settings)),
    targetReadiness: {
      configured: Boolean(settings.targetDirectory),
      ready: Boolean(settings.targetDirectory),
      freeBytes: 4_000_000_000,
      requiredBytes: 50_000_000,
      warnings: [],
    },
    latestRun,
    lastSuccessfulScheduleBucket: null,
    lastSuccessfulScheduledBackupId: null,
    capabilities: { runNow: true, scheduled: true, verify: true, restore: false },
    confirmationTextRequired: confirmationText,
  };
}

function completedRun() {
  return {
    runId: "backup-run-completed-fixture",
    backupId: completedBackupId,
    status: "completed",
    trigger: "manual",
    startedAt: "2026-07-19T08:15:00Z",
    completedAt: "2026-07-19T08:15:02Z",
    fileCount: 23,
    totalBytes: 1_234_567,
    manifestSha256: "a".repeat(64),
    verification: { valid: true, backupId: completedBackupId, fileCount: 23, totalBytes: 1_234_567, errors: [] },
  };
}

function failedRun() {
  return {
    runId: "backup-run-failed-fixture",
    backupId: completedBackupId,
    status: "failed",
    trigger: "manual",
    startedAt: "2026-07-19T08:00:00Z",
    completedAt: "2026-07-19T08:00:01Z",
    error: { code: "backup-disk-space-low", message: "Insufficient disk space for isolated fixture." },
  };
}

function aiAssetsFixture() {
  return {
    totalTokens: 123456,
    totalMessages: 78,
    agentCount: 1,
    activeDayCount: 10,
    tools: [],
    trend30d: [],
    diary: {},
    rag: {},
    cronJobs: {},
    agents: [],
    agentTree: [],
    infrastructure: { devices: [], services: [] },
    storage: { categories: [], tools: [] },
    skills: { byTool: {} },
    toolConfigs: [],
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

async function delayed(milliseconds) {
  await new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function installApiFixtures(page, { language, initialFailure = false, verifyFailure = false }) {
  const state = {
    settings: initialSettings(),
    latestRun: initialFailure ? failedRun() : null,
    statusCallsAfterRun: 0,
    runQueued: false,
    mutationRequests: [],
    settingsBodies: [],
    runBodies: [],
  };
  await page.route(/https:\/\/(?:fonts\.googleapis\.com|cdn\.jsdelivr\.net)\/.*/, route => route.abort());
  await page.route("**/events/**", route => route.abort());
  await page.route("**/api/**", async route => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const pathname = requestUrl.pathname;
    const method = request.method();
    const json = async (payload, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
    if (pathname === "/api/ai-assets/backups/status") {
      if (!state.runQueued) {
        await delayed(300);
      } else {
        state.statusCallsAfterRun += 1;
        if (state.statusCallsAfterRun === 1) {
          state.latestRun = {
            runId: "backup-run-running-fixture",
            status: "running",
            trigger: "manual",
            startedAt: "2026-07-19T08:15:00Z",
          };
        } else {
          state.latestRun = completedRun();
        }
      }
      return json(statusPayload(state.settings, state.latestRun));
    }
    if (pathname === "/api/ai-assets/backups/settings" && method === "PUT") {
      const body = request.postDataJSON();
      state.mutationRequests.push({ pathname, method, csrf: request.headers()["x-actanara-csrf"] || "" });
      state.settingsBodies.push(body);
      await delayed(300);
      state.settings = JSON.parse(JSON.stringify(body.backup));
      return json(statusPayload(state.settings, state.latestRun));
    }
    if (pathname === "/api/ai-assets/backups/run" && method === "POST") {
      const body = request.postDataJSON();
      state.mutationRequests.push({ pathname, method, csrf: request.headers()["x-actanara-csrf"] || "" });
      state.runBodies.push(body);
      await delayed(150);
      state.runQueued = true;
      state.statusCallsAfterRun = 0;
      return json({ jobId: "backup-job-fixture", runId: "backup-run-queued-fixture", status: "queued" }, 202);
    }
    if (/^\/api\/ai-assets\/backups\/[^/]+\/verify$/.test(pathname) && method === "POST") {
      state.mutationRequests.push({ pathname, method, csrf: request.headers()["x-actanara-csrf"] || "" });
      await delayed(250);
      if (verifyFailure) return json({ error: "fixture verification error", code: "backup-verification-failed" }, 422);
      return json({
        valid: true,
        backupId: completedBackupId,
        fileCount: 23,
        totalBytes: 1_234_567,
        manifestSha256: "a".repeat(64),
        errors: [],
      });
    }
    let payload;
    if (pathname === "/api/settings") {
      payload = settingsFixture(language);
    } else if (pathname === "/api/diary-list") {
      const item = {
        date: "0719",
        fullDate: "2026-07-19",
        displayDate: "07-19",
        dayOfWeek: language === "en" ? "Sunday" : "星期日",
        isBlankDay: false,
      };
      payload = requestUrl.searchParams.get("envelope") === "1"
        ? { items: [item], dashboardState: dashboardState() }
        : [item];
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
    return json(payload);
  });
  return state;
}

async function openBackupModal(page, mobile, language) {
  await page.goto(`${dashboardOrigin}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.locator(mobile ? ".mobile-bottom-nav" : "aside.sidebar").waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => document.querySelector("#month-nav")?.innerHTML.length > 0, null, { timeout: 20_000 });
  await expect(page.locator("html")).toHaveAttribute("lang", language === "en" ? "en-US" : "zh-CN");
  await page.evaluate(() => showPage("static"));
  const entry = page.locator("#aiAssetsBackupBtn");
  await expect(entry).toBeVisible();
  await entry.click();
  await expect(page.locator("#modal-title")).toHaveText(language === "en" ? "AI Assets Data Backup" : "AI Assets 数据备份");
  await expect(page.locator("#modal-body")).toContainText(language === "en" ? "Reading backup settings" : "正在读取备份设置");
  await page.locator('[data-backup-modal="true"]').waitFor({ state: "visible", timeout: 20_000 });
}

async function assertBackupForm(page, language) {
  await expect(page.locator("[data-backup-include]")).toHaveCount(8);
  await expect(page.locator("#backupTargetDirectory")).toHaveValue("/tmp/actanara-playwright-backups");
  await expect(page.locator("#backupRetentionCount")).toHaveValue("7");
  await expect(page.locator("#backupRetentionDays")).toHaveValue("30");
  await expect(page.locator("#backupScheduleEnabled")).not.toBeChecked();
  await expect(page.locator("#backupScheduleFrequency")).toHaveValue("weekly");
  await expect(page.locator("#backupScheduleTime")).toHaveValue("05:00");
  const restore = page.locator(".backup-restore-contract");
  await expect(restore).toContainText(language === "en" ? "Restore is not available in this version" : "当前版本不提供 restore");
  expect(await page.locator('.backup-modal button:has-text("Restore"), .backup-modal button:has-text("恢复")').count()).toBe(0);
}

async function assertCsrfHeaders(context, state) {
  const cookies = await context.cookies(dashboardOrigin);
  const csrf = cookies.find(cookie => cookie.name === "actanara_dashboard_csrf");
  expect(csrf?.value).toBeTruthy();
  expect(state.mutationRequests.length).toBeGreaterThan(0);
  for (const request of state.mutationRequests) {
    expect(request.csrf, `${request.method} ${request.pathname} omitted CSRF`).toBe(csrf.value);
  }
}

async function waitForModalAnimation(page) {
  await page.locator("#modal-panel").evaluate(async node => {
    await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {})));
  });
}

test("AI Assets backup modal covers isolated desktop zh and mobile en workflows", async ({ browser }, testInfo) => {
  test.setTimeout(3 * 60_000);
  test.skip(testInfo.project.name !== "chromium-desktop", "this isolated spec owns its desktop/mobile context matrix");

  await withBrowserContext(browser, {
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    colorScheme: "light",
  }, async context => {
    const page = await context.newPage();
    const fixture = await installApiFixtures(page, { language: "zh" });
    await openBackupModal(page, false, "zh");
    await assertBackupForm(page, "zh");

    await page.locator("#backupTargetDirectory").fill("/tmp/actanara-playwright-backups-changed");
    await page.locator('[data-backup-include="diaryMarkdown"]').uncheck();
    await page.locator("#backupRetentionCount").fill("5");
    await page.locator("#backupRetentionDays").fill("21");
    await page.locator("#backupScheduleEnabled").check();
    await page.locator("#backupScheduleFrequency").selectOption("monthly");
    await page.locator("#backupScheduleTime").fill("06:45");

    await page.locator("#backupSaveBtn").click();
    await expect(page.locator("#backupSaveBtn")).toBeDisabled();
    await expect(page.locator("#backupSaveBtn")).toHaveText("正在保存…");
    await expect(page.locator("#backupActionStatus")).toHaveText("备份设置已保存。", { timeout: 10_000 });
    expect(fixture.settingsBodies[0]).toEqual({
      backup: {
        targetDirectory: "/tmp/actanara-playwright-backups-changed",
        include: { ...defaultSelection(), diaryMarkdown: false },
        retention: { maxBackups: 5, maxAgeDays: 21 },
        schedule: { enabled: true, frequency: "monthly", timeOfDay: "06:45" },
      },
    });

    await page.locator("#backupConfirmationText").fill(confirmationText);
    await page.locator("#backupRunBtn").click();
    await expect(page.locator("#backupActionStatus")).toContainText("备份已排队", { timeout: 10_000 });
    await expect(page.locator("#backupActionStatus")).toContainText("备份进行中", { timeout: 10_000 });
    await expect(page.locator("#backupActionStatus")).toContainText("备份完成并通过 manifest 验证", { timeout: 10_000 });
    expect(fixture.runBodies).toEqual([{ confirmationText }]);
    await expect(page.locator(".backup-latest")).toHaveAttribute("data-tone", "success");
    await expect(page.locator(".backup-latest")).toContainText(completedBackupId);

    await page.locator("#backupVerifyBtn").click();
    await expect(page.locator("#backupVerifyBtn")).toBeDisabled();
    await expect(page.locator("#backupVerifyBtn")).toHaveText("正在验证…");
    await expect(page.locator("#backupActionStatus")).toContainText("manifest、hash 与文件清单验证通过", { timeout: 10_000 });
    await assertCsrfHeaders(context, fixture);

    await page.locator("#modal-body").evaluate(node => { node.scrollTop = node.scrollHeight; });
    await waitForModalAnimation(page);
    const screenshotPath = path.join(evidenceDir, "dashboard-backup-desktop-zh.png");
    await page.screenshot({ path: screenshotPath });
    fs.chmodSync(screenshotPath, 0o600);
    expect(fs.statSync(screenshotPath).size).toBeGreaterThan(0);
  });

  await withBrowserContext(browser, {
    viewport: { width: 412, height: 915 },
    locale: "en-US",
    colorScheme: "dark",
    isMobile: true,
    hasTouch: true,
  }, async context => {
    const page = await context.newPage();
    const fixture = await installApiFixtures(page, { language: "en", initialFailure: true, verifyFailure: true });
    await openBackupModal(page, true, "en");
    await assertBackupForm(page, "en");
    await expect(page.locator(".backup-latest")).toHaveAttribute("data-tone", "error");
    await expect(page.locator(".backup-latest-error")).toContainText("Insufficient disk space for isolated fixture");

    await page.locator("#backupVerifyBtn").click();
    await expect(page.locator("#backupVerifyBtn")).toBeDisabled();
    await expect(page.locator("#backupVerifyBtn")).toHaveText("Verifying...");
    await expect(page.locator("#backupActionStatus")).toContainText("Backup verification failed: fixture verification error", { timeout: 10_000 });
    await assertCsrfHeaders(context, fixture);

    const overflow = await page.evaluate(() => {
      const body = document.getElementById("modal-body");
      const panel = document.getElementById("modal-panel");
      return {
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        pageBody: document.body.scrollWidth,
        panel: panel?.getBoundingClientRect().width || 0,
        modalClient: body?.clientWidth || 0,
        modalScroll: body?.scrollWidth || 0,
      };
    });
    expect(overflow.document).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.pageBody).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.panel).toBeLessThanOrEqual(overflow.viewport);
    expect(overflow.modalScroll).toBeLessThanOrEqual(overflow.modalClient + 1);

    await page.locator("#modal-body").evaluate(node => { node.scrollTop = node.scrollHeight; });
    await waitForModalAnimation(page);
    const screenshotPath = path.join(evidenceDir, "dashboard-backup-mobile-en.png");
    await page.screenshot({ path: screenshotPath });
    fs.chmodSync(screenshotPath, 0o600);
    expect(fs.statSync(screenshotPath).size).toBeGreaterThan(0);
  });
});
