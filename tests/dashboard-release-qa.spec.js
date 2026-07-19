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

function initializeIsolatedRuntime(root, port) {
  const home = path.join(root, "Home");
  const runtime = path.join(root, "Actanara");
  const config = path.join(runtime, "config");
  fs.mkdirSync(home, { recursive: true, mode: 0o700 });
  fs.mkdirSync(config, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(config, "runtime.json"), JSON.stringify({
    instanceId: "playwright-release-qa-isolated",
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
    dashboard: {
      host: "127.0.0.1",
      port,
      publicBaseUrl: `http://127.0.0.1:${port}`,
      allowedOrigins: [`http://127.0.0.1:${port}`],
    },
  }, null, 2) + "\n", { mode: 0o600 });
  return { home, runtime };
}

test.beforeAll(async () => {
  isolatedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "actanara-dashboard-release-qa-"));
  const port = await listenPort();
  dashboardOrigin = `http://127.0.0.1:${port}`;
  const { home, runtime } = initializeIsolatedRuntime(isolatedRoot, port);
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
        HOME: home,
        ACTANARA_HOME: runtime,
        ACTANARA_LOCATION_FILE: path.join(isolatedRoot, "location.json"),
        ACTANARA_SECRET_BACKEND: "memory",
        ACTANARA_RUN_REAL_LAUNCHD_TESTS: "0",
        PYTHONDONTWRITEBYTECODE: "1",
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
    if (!resolved.startsWith(temp + path.sep) || !path.basename(resolved).startsWith("actanara-dashboard-release-qa-")) {
      throw new Error("refusing to remove an unrecognized Playwright runtime directory");
    }
    fs.rmSync(resolved, { recursive: true, force: true });
  }
});

function settingsFixture(language) {
  return {
    schemaVersion: 1,
    general: { locale: language === "en" ? "en-US" : "zh-CN", timezone: "Asia/Hong_Kong" },
    pipeline: { languageProfile: language },
    schedule: { enabled: false, timezone: "Asia/Hong_Kong" },
    dashboard: {
      host: "127.0.0.1",
      port: 3036,
      publicBaseUrl: "https://actanara.fixture.ts.net",
      allowedOrigins: ["https://actanara.fixture.ts.net"],
    },
    paths: {},
    runtimeSources: {},
    externalTools: {},
    llmProvider: {},
    features: {},
    authority: {},
  };
}

function pipelineFixture() {
  const tokens = {
    inputTokens: 1320,
    outputTokens: 420,
    cacheReadTokens: 180,
    cacheWriteTokens: 0,
    reasoningTokens: 96,
    totalTokens: 2016,
  };
  return {
    schemaVersion: 1,
    activeCount: 0,
    sources: { pipelineRuns: true },
    summary: { services: 1, byStatus: { completed: 1 }, bySource: { pipeline: 1 } },
    tasks: [{
      id: "pipeline-run-release-fixture",
      source: "pipeline",
      title: "Daily pipeline · 2026-07-18",
      subtitle: "All stages committed",
      status: "completed",
      progress: 100,
      startedAt: "2026-07-18T04:00:00+08:00",
      completedAt: "2026-07-18T04:00:08+08:00",
      tokenAttribution: { usageStatus: "reported", callDataAvailable: true, llmCallCount: 1, tokens },
      stageDetails: [{
        stageId: "narrative",
        name: "Narrative pass",
        status: "completed",
        startedAt: "2026-07-18T04:00:01+08:00",
        completedAt: "2026-07-18T04:00:04+08:00",
        durationSeconds: 3.2,
        provider: "fixture-primary",
        model: "fixture-model",
        llmCallCount: 1,
        retryCount: 1,
        fallbackCount: 1,
        tokenAttribution: { usageStatus: "reported", callDataAvailable: true, estimated: false, tokens },
        artifactPaths: ["artifacts/diary/diary-2026-07-18/report.md"],
        artifactCommitted: true,
        calls: [{
          callId: "call-release-fixture",
          chunkId: "chunk-01",
          status: "completed",
          providerId: "fixture-fallback",
          model: "fixture-model-fallback",
          usage: tokens,
          usageSource: "provider-response",
          retryCount: 1,
          fallbackCount: 1,
          attempts: [
            { provider: "fixture-primary", model: "fixture-model", status: "failed", failureClass: "rate-limit", httpStatus: 429, errorSummary: "rate limited" },
            { provider: "fixture-fallback", model: "fixture-model-fallback", status: "completed" },
          ],
        }],
      }],
    }],
  };
}

function providerFixture() {
  return {
    schemaVersion: 1,
    providers: [
      { entryId: "primary-fixture", mode: "custom", provider: "custom", model: "fixture-primary-model", endpoint: "https://provider.invalid/v1", api: "openai-compatible", hasApiKey: true, timeoutSeconds: 300, readiness: { ready: true, status: "ready" } },
      { entryId: "fallback-fixture", mode: "custom", provider: "custom", model: "fixture-fallback-model", endpoint: "https://fallback.invalid/v1", api: "openai-compatible", hasApiKey: true, timeoutSeconds: 300, readiness: { ready: false, status: "not-ready", error: "fixture readiness warning" } },
    ],
    catalog: [{ id: "custom", name: "Custom", enabled: true, endpoint: "", api: "openai-compatible", models: [] }],
  };
}

function ragFixture() {
  const externalSources = {
    enabled: true,
    mode: "supplement",
    paths: ["/fixture/content"],
    recursive: true,
    include: ["**/*.md", "**/*.pdf"],
    exclude: ["private/**"],
    maxFileBytes: 10485760,
    maxTotalBytes: 268435456,
    maxFiles: 5000,
    symlinkPolicy: "reject",
  };
  const rag = {
    enabled: true,
    mode: "v2",
    embedding: { mode: "local", provider: "local", providerId: "local", model: "fixture-embedding" },
    retrieval: { topK: 8, recencyHalfLifeDays: 7 },
    indexing: { enabled: true, defaultFullRebuild: false, externalSources },
    server: { enabled: true },
  };
  const status = {
    schemaVersion: 1,
    productEnabled: true,
    mode: "v2",
    externalSources,
    profile: {
      configured: { mode: "local", model: "fixture-embedding", dimension: 384 },
      active: { mode: "local", model: "fixture-embedding", dimension: 384 },
    },
    activeRunId: "rag-fixture-active",
    index: { ready: true, chunks: 27, documents: 2 },
    server: { healthy: true },
    search: { available: true },
  };
  return { rag, status };
}

function tailscaleFixture() {
  return {
    schemaVersion: 1,
    mode: "tailnet-only",
    installed: true,
    loginState: "connected",
    connected: true,
    reachable: true,
    ips: { ipv4: "100.64.0.18", ipv6: null },
    dns: { magicDnsEnabled: true, name: "actanara.fixture.ts.net", origin: "https://actanara.fixture.ts.net" },
    dashboardAccess: { origin: "https://actanara.fixture.ts.net", originAllowed: true, ready: true },
    serve: { enabled: false, managed: false, exclusiveManaged: false, conflict: false, exposesNovaRag: false, enableConfirmationTextRequired: "ENABLE ACTANARA TAILNET SERVE", disableConfirmationTextRequired: "DISABLE ACTANARA TAILNET SERVE" },
    funnel: { available: false, enabled: false, risk: "high", reason: "disabled-by-policy" },
    canEnableServe: true,
    canDisableServe: false,
    errors: [],
  };
}

async function installApiFixtures(page, language) {
  const rag = ragFixture();
  await page.route(/https:\/\/(?:fonts\.googleapis\.com|cdn\.jsdelivr\.net)\/.*/, route => route.abort());
  await page.route("**/events/**", route => route.abort());
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();
    const json = (payload, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
    if (pathname === "/api/settings") return json(settingsFixture(language));
    if (pathname === "/api/background-tasks") return json(pipelineFixture());
    if (pathname === "/api/llm-provider-chain" && method === "GET") return json(providerFixture());
    if (pathname === "/api/llm-provider-chain/test" && method === "POST") {
      return json({ ok: true, status: "ready", provider: "custom", model: "fixture-primary-model", latencyMs: 24 });
    }
    if (pathname === "/api/rag/settings") return json({ rag: rag.rag, status: rag.status });
    if (pathname === "/api/rag/status") return json(rag.status);
    if (pathname === "/api/rag/external-sources/plan" && method === "POST") {
      return json({
        schemaVersion: 1,
        dryRun: true,
        status: "plan",
        canExecute: true,
        mode: "replace",
        summary: { sourceRecordCount: 2, chunkCount: 7, parseErrorCount: 0, blockingSourceCount: 0 },
        blockers: [],
        sources: [
          { sourcePath: "/fixture/content/release.md", parserStatus: "parsed", parserVersion: "markdown-v1" },
          { sourcePath: "/fixture/content/guide.pdf", parserStatus: "cached", parserVersion: "pdf-pypdf-v1" },
        ],
      });
    }
    if (pathname === "/api/settings/tailscale/status") return json(tailscaleFixture());
    if (pathname === "/api/settings/scheduler") return json({ installed: false, running: false });
    if (pathname === "/api/settings/startup") return json({});
    if (pathname === "/api/diary/periods") return json({ months: [], weeks: [] });
    if (pathname === "/api/time-context") return json({ timezone: "Asia/Hong_Kong", businessDate: "2026-07-19", dayOfWeek: language === "en" ? "Sunday" : "星期日" });
    if (pathname === "/api/diary/today") return json({ status: "empty" });
    if (pathname === "/api/msgbox") return json({ messages: [], unreadCount: 0 });
    return json({});
  });
}

async function openShell(page, mobile) {
  await page.goto(`${dashboardOrigin}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.locator(mobile ? ".mobile-bottom-nav" : "aside.sidebar").waitFor({ state: "visible", timeout: 20_000 });
}

async function settleModal(page) {
  await page.locator("#modal-panel").evaluate(async node => {
    await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {})));
  });
}

async function screenshot(page, name) {
  await settleModal(page);
  const target = path.join(evidenceDir, name);
  await page.screenshot({ path: target });
  fs.chmodSync(target, 0o600);
  expect(fs.statSync(target).size).toBeGreaterThan(0);
}

async function openPipelineDetails(page) {
  await page.locator("#taskMonitorButton").click();
  await page.locator(".pipeline-run-details").waitFor({ state: "visible" });
  await page.locator(".pipeline-run-details > summary").click();
  await page.locator(".pipeline-stage-row > summary").click();
  await expect(page.locator(".pipeline-call-row")).toContainText("fixture-fallback");
  await expect(page.locator(".pipeline-token-row")).toContainText("2,016");
  await expect(page.locator(".pipeline-attempts")).toContainText("rate-limit");
}

test("release QA covers pipeline details on desktop and 390px mobile", async ({ browser }, testInfo) => {
  test.setTimeout(2 * 60_000);
  test.skip(testInfo.project.name !== "chromium-desktop", "this isolated spec owns its desktop/mobile context matrix");

  await withBrowserContext(browser, { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" }, async context => {
    const page = await context.newPage();
    await installApiFixtures(page, "zh");
    await openShell(page, false);
    await openPipelineDetails(page);
    await screenshot(page, "desktop-pipeline-details.png");
  });

  await withBrowserContext(browser, { viewport: { width: 390, height: 844 }, locale: "en-US", isMobile: true, hasTouch: true }, async context => {
    const page = await context.newPage();
    await installApiFixtures(page, "en");
    await openShell(page, true);
    await openPipelineDetails(page);
    const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth, modal: document.getElementById("modal-panel")?.getBoundingClientRect().width || 0 }));
    expect(overflow.document).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.body).toBeLessThanOrEqual(overflow.viewport + 1);
    expect(overflow.modal).toBeLessThanOrEqual(overflow.viewport);
    await screenshot(page, "mobile-pipeline-details.png");
  });
});

test("release QA covers provider chain editing and readiness", async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "single deterministic desktop run");
  await withBrowserContext(browser, { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" }, async context => {
    const page = await context.newPage();
    await installApiFixtures(page, "zh");
    await openShell(page, false);
    await page.locator('button[onclick="openLlmProviderModal()"]:visible').click();
    await expect(page.locator("[data-llm-chain-index]")).toHaveCount(2);
    await page.locator(".llm-chain-add").click();
    await expect(page.locator("[data-llm-chain-index]")).toHaveCount(3);
    await page.locator('[data-llm-chain-index="2"] button[aria-label="移除"]').click();
    await expect(page.locator("[data-llm-chain-index]")).toHaveCount(2);
    await page.locator('[data-llm-chain-index="1"] button[aria-label="上移"]').click();
    await page.locator('[data-llm-chain-index="0"] .llm-chain-test button').click();
    await expect(page.locator("#llmProviderChainTest-0")).toContainText("24ms");
    await expect(page.locator('[data-chain-field="apiKey"]')).toHaveCount(2);
    expect(await page.locator('[data-chain-field="apiKey"]').evaluateAll(inputs => inputs.map(input => input.value))).toEqual(["", ""]);
    await screenshot(page, "desktop-provider-chain.png");
  });
});

test("release QA covers RAG external source modes, dry-run, and parser status", async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "single deterministic desktop run");
  await withBrowserContext(browser, { viewport: { width: 1440, height: 1000 }, locale: "en-US" }, async context => {
    const page = await context.newPage();
    await installApiFixtures(page, "en");
    await openShell(page, false);
    await page.evaluate(() => showPage("rag-search"));
    await page.locator("#setRagExternalMode").waitFor({ state: "visible" });
    await expect(page.locator("#setRagExternalMode")).toHaveValue("supplement");
    await page.locator("#setRagExternalMode").selectOption("replace");
    await page.locator("#ragExternalPlanBtn").click();
    await expect(page.locator(".rag-external-plan-ready")).toContainText("Dry-run complete");
    await expect(page.locator(".rag-external-plan-row")).toHaveCount(2);
    await expect(page.locator("#ragExternalSources")).toContainText(".doc is unsupported");
    await page.locator("#ragExternalSources").scrollIntoViewIfNeeded();
    await screenshot(page, "desktop-rag-external-sources.png");
  });
});

test("release QA covers tailnet-only status and fail-closed Funnel UI", async ({ browser }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "single deterministic desktop run");
  await withBrowserContext(browser, { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" }, async context => {
    const page = await context.newPage();
    await installApiFixtures(page, "zh");
    await openShell(page, false);
    await page.locator('button[onclick="openSettingsModal()"]:visible').click();
    await page.locator('.settings-tab[data-tab="network"]').click();
    await expect(page.locator("#tailscaleStatus")).toContainText("actanara.fixture.ts.net");
    await expect(page.locator(".tailscale-funnel-boundary")).toContainText("当前安全策略禁止使用");
    expect(await page.locator('button[onclick*="funnel" i]').count()).toBe(0);
    await page.locator(".tailscale-settings").scrollIntoViewIfNeeded();
    await screenshot(page, "desktop-tailscale.png");
  });
});
