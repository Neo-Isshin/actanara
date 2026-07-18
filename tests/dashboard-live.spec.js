import { expect, test } from "@playwright/test";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { withBrowserContext } from "./dashboard-live-context.js";

/*
 * Destructive, real-runtime Dashboard gate. It is deliberately outside the
 * isolated release suite and cannot run unless the operator supplies a URL.
 *
 * The gate mutates one non-secret Settings boolean and (when Nova-Task is
 * enabled) one uniquely named synthetic task. Settings are restored from a
 * fresh UI preimage in finally; the task is archived in finally. No LLM/RAG
 * secret field is read, filled, captured, logged, or written to evidence.
 *
 * Some states are deterministic response overlays (locale, loading, empty,
 * error, degraded, Chart CDN failure). They exercise the real candidate UI
 * and real API shape without changing the runtime merely to manufacture a
 * fault. Conditional gaps are emitted as explicit skipped checks.
 */

const repoRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const rawBaseUrl = String(process.env.ACTANARA_DASHBOARD_LIVE_BASE_URL || "").trim();
const defaultEvidenceDir = path.join(repoRoot, "test-results", "dashboard-live");
const evidenceDir = path.resolve(
  process.env.ACTANARA_DASHBOARD_LIVE_EVIDENCE_DIR || defaultEvidenceDir,
);

test.describe.configure({ mode: "serial" });
test.skip(!rawBaseUrl, "set ACTANARA_DASHBOARD_LIVE_BASE_URL to opt in to the real Dashboard gate");

function liveTarget(value) {
  const parsed = new URL(value);
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("live base URL must be http(s)");
  if (!["127.0.0.1", "localhost", "::1"].includes(hostname)) throw new Error("live base URL must be loopback");
  if (parsed.username || parsed.password || parsed.search || parsed.hash) throw new Error("live base URL must not contain credentials/query/fragment");
  if (parsed.pathname !== "/" && parsed.pathname !== "") throw new Error("live base URL must not contain a path");
  const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
  return {
    origin: `${parsed.protocol}//${parsed.host}`,
    evidence: { scheme: parsed.protocol.slice(0, -1), host: hostname, port: Number(port) },
  };
}

function envHash(name) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  return /^[a-f0-9]{64}$/.test(value) ? value : null;
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function safeError(error) {
  return { kind: error?.name || "Error" };
}

function overflowState(page) {
  return page.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
}

function noOverflow(state) {
  return state.document <= state.viewport + 1 && state.body <= state.viewport + 1;
}

async function localeOverlay(page, locale) {
  await page.route("**/api/settings", async route => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    body.general = { ...(body.general || {}), locale };
    body.pipeline = { ...(body.pipeline || {}), languageProfile: locale };
    await route.fulfill({ response, json: body });
  });
}

async function waitForShell(page, { mobile = false } = {}) {
  await page.locator(mobile ? ".mobile-bottom-nav" : "aside.sidebar").waitFor({ state: "visible", timeout: 20_000 });
  await page.waitForFunction(() => document.querySelector("#month-nav")?.innerHTML.length > 0, null, { timeout: 20_000 });
}

async function installTokenClockSnapshot(context, snapshot) {
  if (!snapshot || typeof snapshot !== "object") throw new Error("real token-clock snapshot is required");
  await context.route("**/api/token-clock", route => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(snapshot),
    });
  });
}

async function activateStaticRoute(page, selector, pageId, key = "Space") {
  const entry = page.locator(selector);
  await entry.focus();
  await page.keyboard.press(key);
  await page.waitForFunction(id => document.getElementById(id)?.classList.contains("active"), pageId);
  return page.evaluate(id => {
    const active = [...document.querySelectorAll(".page.active")];
    const target = document.getElementById(id);
    const heading = target?.querySelector(".page-title");
    return {
      activeCount: active.length,
      activeId: active[0]?.id || null,
      ariaHidden: target?.getAttribute("aria-hidden"),
      headingFocused: document.activeElement === heading,
      headingRole: heading?.getAttribute("role"),
    };
  }, pageId);
}

async function screenshot(page, name, screenshots) {
  const output = path.join(evidenceDir, name.replace(/[^a-z0-9._-]/gi, "-"));
  await page.screenshot({ path: output, fullPage: true });
  fs.chmodSync(output, 0o600);
  screenshots.push(output);
}

test("real Dashboard release gate", async ({ browser, playwright }, testInfo) => {
  // This serial gate owns a desktop/mobile matrix plus reversible Settings and
  // Task mutations. Keep individual waits bounded, but do not let Playwright's
  // generic 30-second test default terminate later checks before they run.
  test.setTimeout(5 * 60_000);
  test.skip(testInfo.project.name !== "chromium-desktop", "the live script owns its desktop/mobile context matrix");
  const target = liveTarget(rawBaseUrl);
  fs.mkdirSync(evidenceDir, { recursive: true, mode: 0o700 });

  const checks = [];
  const skipped = [];
  const screenshots = [];
  const marker = `ACTANARA-LIVE-${Date.now()}-${crypto.randomBytes(4).toString("hex")}`;
  let syntheticNodeId = null;
  let settingsPreimage = null;
  let settingsPage = null;
  let settingsContext = null;
  let tokenClockSnapshot = null;

  const record = (name, passed, detail = {}) => checks.push({ name, passed: Boolean(passed), detail });
  const skip = (name, reason) => skipped.push({ name, reason });
  const check = async (name, action) => {
    try {
      const detail = (await action()) || {};
      record(name, true, detail);
      return detail;
    } catch (error) {
      record(name, false, safeError(error));
      return null;
    }
  };
  const context = (options, action) => withBrowserContext(browser, options, async ctx => {
    await installTokenClockSnapshot(ctx, tokenClockSnapshot);
    return action(ctx);
  });

  try {
    const tokenClockProbe = await check("real token-clock scan leaves Dashboard health responsive", async () => withBrowserContext(
      browser,
      { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await localeOverlay(page, "zh-CN");
        const requestSeen = page.waitForRequest(
          request => request.url().endsWith("/api/token-clock") && request.method() === "GET",
          { timeout: 20_000 },
        );
        let responseSettled = false;
        const responsePending = page.waitForResponse(
          response => response.url().endsWith("/api/token-clock") && response.request().method() === "GET",
          { timeout: 180_000 },
        ).then(response => {
          responseSettled = true;
          return response;
        });
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await requestSeen;
        await waitForShell(page);
        await page.waitForTimeout(100);

        const healthContext = await playwright.request.newContext({ baseURL: target.origin });
        let healthStatus;
        let healthBody;
        const healthStarted = Date.now();
        const scanPendingAtHealthStart = !responseSettled;
        try {
          const health = await healthContext.get("/health", { timeout: 10_000 });
          healthStatus = health.status();
          healthBody = await health.json();
        } finally {
          await healthContext.dispose();
        }
        const healthLatencyMs = Date.now() - healthStarted;
        expect(healthStatus).toBe(200);
        expect(healthBody.status).toBe("ok");
        expect(healthLatencyMs).toBeLessThan(5_000);

        const response = await responsePending;
        expect(response.status()).toBe(200);
        const payload = await response.json();
        expect(["ready", "empty", "degraded"]).toContain(payload.dashboardState?.status);
        tokenClockSnapshot = payload;
        return {
          responseStatus: response.status(),
          dashboardState: payload.dashboardState.status,
          sourceErrorCount: Array.isArray(payload.sourceErrors) ? payload.sourceErrors.length : 0,
          toolCount: Array.isArray(payload.tools) ? payload.tools.length : 0,
          healthStatus,
          healthLatencyMs,
          scanPendingAtHealthStart,
        };
      },
    ));
    expect(tokenClockProbe).not.toBeNull();
    expect(tokenClockSnapshot).not.toBeNull();

    await check("desktop zh canonical routes, keyboard, focus and overflow", async () => context(
      { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await localeOverlay(page, "zh-CN");
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await waitForShell(page);
        await expect(page).toHaveTitle("Actanara");
        await expect(page.locator("#sseStatus")).toHaveText("🟢 已连接", { timeout: 20_000 });
        await expect(page.locator("#sseStatus")).toHaveAttribute("data-source-health", "ready");
        const routeStates = [];
        for (const [selector, pageId] of [
          ['aside.sidebar [data-page-id="page-overview"]', "page-overview"],
          ['aside.sidebar [data-page-id="page-static"]', "page-static"],
          ['aside.sidebar [data-page-id="page-rag-search"]', "page-rag-search"],
          ['aside.sidebar [data-page-id="page-foundation-ops"]', "page-foundation-ops"],
        ]) routeStates.push(await activateStaticRoute(page, selector, pageId));
        const overflow = await overflowState(page);
        await screenshot(page, "desktop-zh-canonical.png", screenshots);
        expect(routeStates.every(state => state.activeCount === 1 && state.ariaHidden === "false" && state.headingFocused && state.headingRole === "heading")).toBe(true);
        expect(noOverflow(overflow)).toBe(true);
        return {
          routeCount: routeStates.length,
          overflow,
          title: await page.title(),
          connectionStatus: await page.locator("#sseStatus").textContent(),
        };
      },
    ));

    await check("dynamic diary/week/month routes and history dialog", async () => context(
      { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await localeOverlay(page, "zh-CN");
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await waitForShell(page);
        const counts = await page.evaluate(() => ({
          diary: document.querySelectorAll("[data-diary-date]").length,
          week: document.querySelectorAll("[data-report-id]").length,
          month: document.querySelectorAll("[data-month-id]").length,
        }));
        if (!counts.diary || !counts.week || !counts.month) {
          skip("dynamic diary/week/month activation", `runtime contract/data absent: ${JSON.stringify(counts)}`);
        } else {
          await page.evaluate(() => document.querySelectorAll("#month-nav .nav-section-title").forEach(el => {
            if (el.getAttribute("aria-expanded") === "false") el.click();
          }));
          for (const selector of ["[data-diary-date]", "[data-report-id]", "[data-month-id]"]) {
            const item = page.locator(selector).first();
            await item.scrollIntoViewIfNeeded();
            await item.focus();
            await page.keyboard.press("Space");
            await page.waitForTimeout(250);
          }
        }
        const trigger = page.locator("#historyBackfillButton");
        await trigger.focus();
        await trigger.click();
        await page.locator("#modal-panel").waitFor({ state: "visible" });
        const dialog = await page.locator("#modal-panel").evaluate(el => ({
          role: el.getAttribute("role"),
          modal: el.getAttribute("aria-modal"),
          focusInside: el.contains(document.activeElement),
        }));
        await page.keyboard.press("Escape");
        expect(dialog).toEqual({ role: "dialog", modal: "true", focusInside: true });
        expect(await trigger.evaluate(el => document.activeElement === el)).toBe(true);
        return { ...counts, historyDialog: true };
      },
    ));

    await check("desktop en and mobile zh/en canonical routes and overflow", async () => {
      const matrix = [];
      for (const item of [
        { name: "desktop-en", locale: "en-US", viewport: { width: 1440, height: 1000 }, mobile: false },
        { name: "mobile-zh", locale: "zh-CN", viewport: { width: 412, height: 915 }, mobile: true },
        { name: "mobile-en", locale: "en-US", viewport: { width: 412, height: 915 }, mobile: true },
      ]) {
        await context({ viewport: item.viewport, locale: item.locale, isMobile: item.mobile }, async ctx => {
          const page = await ctx.newPage();
          await localeOverlay(page, item.locale);
          await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
          await waitForShell(page, { mobile: item.mobile });
          if (item.mobile) {
            for (const route of ["overview", "static", "rag-search", "foundation-ops"]) {
              await page.locator(`[data-mobile-page="${route}"]`).click();
            }
            await expect(page.locator(".mobile-bottom-nav")).toBeVisible();
          } else {
            await activateStaticRoute(page, 'aside.sidebar [data-page-id="page-overview"]', "page-overview");
          }
          await page.waitForFunction(expected => document.documentElement.lang === expected, item.locale, { timeout: 20_000 });
          const overflow = await overflowState(page);
          const lang = await page.locator("html").getAttribute("lang");
          expect(lang).toBe(item.locale);
          expect(noOverflow(overflow)).toBe(true);
          await screenshot(page, `${item.name}.png`, screenshots);
          matrix.push({ name: item.name, lang, overflow });
        });
      }
      return { matrix };
    });

    await check("loading, empty, error and degraded states are visible", async () => context(
      { viewport: { width: 1280, height: 900 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await localeOverlay(page, "zh-CN");
        await page.route("**/api/diary-list?**", route => route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [], dashboardState: { schemaVersion: 1, status: "empty", sourceErrors: [] } }),
        }));
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        await page.route("**/api/ai-assets", async route => {
          await gate;
          const response = await route.fetch();
          const body = await response.json();
          body.dashboardState = { schemaVersion: 1, status: "degraded", sourceErrors: [{ source: "ai-assets", code: "live-gate-degraded" }] };
          await route.fulfill({ response, json: body });
        });
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await expect(page.locator("#month-nav .nav-state-empty")).toBeVisible();
        await page.locator('aside.sidebar [data-page-id="page-static"]').click();
        await expect(page.locator("#aiAssetsLoading")).toBeVisible();
        release();
        await page.waitForFunction(() => (document.getElementById("aiAssetsLoading")?.textContent || "").includes("live-gate-degraded"));
        await expect(page.locator("#aiAssetsContent")).toBeVisible();
        await page.unroute("**/api/diary-list?**");
        await page.route("**/api/diary-list?**", route => route.fulfill({ status: 500, body: "{}", contentType: "application/json" }));
        await page.evaluate(() => window.loadDiaryNav());
        await expect(page.locator("#month-nav [role=alert]")).toBeVisible();
        await screenshot(page, "visible-states.png", screenshots);
        return { loading: true, empty: true, error: true, degraded: true };
      },
    ));

    await check("Chart CDN blocked keeps non-chart AI Assets content", async () => context(
      { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        let blocked = 0;
        await page.route("https://cdn.jsdelivr.net/npm/chart.js**", route => { blocked += 1; return route.abort("blockedbyclient"); });
        await localeOverlay(page, "zh-CN");
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await waitForShell(page);
        await page.locator('aside.sidebar [data-page-id="page-static"]').click();
        await page.waitForFunction(() => {
          const content = document.getElementById("aiAssetsContent");
          const loading = document.getElementById("aiAssetsLoading");
          return getComputedStyle(content).display !== "none" || (loading?.textContent || "").includes("暂无");
        }, null, { timeout: 20_000 });
        const state = await page.evaluate(() => ({
          content: getComputedStyle(document.getElementById("aiAssetsContent")).display,
          kpi: document.getElementById("aaKpi")?.childElementCount || 0,
          diary: (document.getElementById("aaDiary")?.textContent || "").trim().length,
          rag: (document.getElementById("aaRag")?.textContent || "").trim().length,
          cron: (document.getElementById("aaCron")?.textContent || "").trim().length,
        }));
        if (state.content === "none") skip("Chart CDN non-chart assertion", "runtime AI Assets state is empty");
        else expect(state.kpi > 0 && state.diary > 0 && state.rag > 0 && state.cron > 0).toBe(true);
        expect(blocked).toBeGreaterThan(0);
        await screenshot(page, "chart-cdn-blocked.png", screenshots);
        return { blocked, ...state };
      },
    ));

    await check("session, CSRF, Host and Origin security matrix", async () => {
      const unauth = await playwright.request.newContext({ baseURL: target.origin });
      try {
        const noSession = await unauth.get("/api/tokens");
        const badOrigin = await unauth.get("/api/tokens", { headers: { Origin: "https://invalid.example.test" } });
        const badHost = await unauth.get("/api/tokens", { headers: { Host: "invalid.example.test" } });
        expect([noSession.status(), badOrigin.status(), badHost.status()]).toEqual([401, 403, 403]);
      } finally { await unauth.dispose(); }
      return context({ viewport: { width: 800, height: 600 } }, async ctx => {
        const page = await ctx.newPage();
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        const csrf = (await ctx.cookies()).find(cookie => cookie.name === "actanara_dashboard_csrf")?.value;
        expect(csrf).toBeTruthy();
        const allowed = await ctx.request.get(`${target.origin}/api/tokens`, { headers: { Origin: target.origin } });
        const missing = await ctx.request.post(`${target.origin}/api/__live_csrf_probe__`, { data: {} });
        const matching = await ctx.request.post(`${target.origin}/api/__live_csrf_probe__`, { headers: { "X-Actanara-CSRF": csrf }, data: {} });
        expect([allowed.status(), missing.status(), matching.status()]).toEqual([200, 403, 404]);
        return { authenticatedGet: 200, missingCsrf: 403, matchingCsrfRouter: 404 };
      });
    });

    await check("Settings failure, save and finally-restorable preimage", async () => {
      settingsContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "zh-CN" });
      await installTokenClockSnapshot(settingsContext, tokenClockSnapshot);
      settingsPage = await settingsContext.newPage();
      await settingsPage.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
      await waitForShell(settingsPage);
      await settingsPage.locator('aside.sidebar button[data-i18n="settingsButton"]').click();
      await settingsPage.locator("#modal-body .settings-grid").waitFor({ state: "visible" });
      settingsPreimage = await settingsPage.locator("#setTargetDay").isChecked();
      await settingsPage.locator("#setTargetDay").setChecked(!settingsPreimage);
      let interceptedBody = null;
      await settingsPage.route("**/api/settings/bundle", async route => {
        interceptedBody = route.request().postDataJSON();
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "live-gate-injected-failure" }) });
      });
      await settingsPage.locator('[data-settings-focus-key="save"]').click();
      await expect(settingsPage.locator("#settingsSaveStatus")).toContainText("live-gate-injected-failure");
      await expect(settingsPage.locator("#modal")).toHaveAttribute("aria-hidden", "false");
      expect(Object.prototype.hasOwnProperty.call(interceptedBody || {}, "llmProvider")).toBe(false);
      await settingsPage.unroute("**/api/settings/bundle");
      const save = settingsPage.waitForResponse(r => r.url().endsWith("/api/settings/bundle") && r.request().method() === "PUT");
      await settingsPage.locator('[data-settings-focus-key="save"]').click();
      expect((await save).status()).toBe(200);
      return { preimage: settingsPreimage, injectedFailure: 503, saved: true, secretPayloadPresent: false };
    });

    await check("Task create/edit/review surface with synthetic cleanup marker", async () => context(
      { viewport: { width: 1440, height: 1000 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await page.goto(`${target.origin}/tasks`, { waitUntil: "domcontentloaded" });
        await page.waitForFunction(
          () => !/连接中|Connecting/.test(document.getElementById("statusText")?.textContent || ""),
          null,
          { timeout: 20_000 },
        );
        const status = await ctx.request.get(`${target.origin}/api/tasks/l1-review/status`);
        const statusBody = await status.json();
        if (statusBody.enabled === false || statusBody.dashboardState?.status === "unavailable") {
          skip("Task create/edit cleanup", "Nova-Task disabled");
          return { featureEnabled: false, reviewRead: status.status() };
        }
        await page.getByRole("button", { name: /总览编辑|Overview Edit/i }).click();
        await page.getByRole("button", { name: /新建任务|New Task/i }).click();
        await page.locator("#createNodeTitle").fill(marker);
        const createResponse = page.waitForResponse(r => r.url().endsWith("/api/tasks/nodes") && r.request().method() === "POST");
        await page.getByRole("button", { name: /创建|Create/i }).click();
        const created = await (await createResponse).json();
        syntheticNodeId = created.nodeId || created.node?.nodeId || null;
        expect(syntheticNodeId).toBeTruthy();
        // The POST response arrives before saveNewTask() finishes its
        // loadTaskData/openOverviewEditor refresh. Wait for that UI commit
        // instead of leaving an unbounded locator action pending.
        const activeTaskModal = page.locator("#taskModal.active");
        await expect(activeTaskModal.locator(".overview-toolbar")).toBeVisible({ timeout: 20_000 });
        await expect(page.locator("#taskModalTitle")).toHaveText(/总览编辑|Edit Overview/i, { timeout: 20_000 });
        const editControl = activeTaskModal.locator(`[data-task-focus-key="edit:${syntheticNodeId}"]`);
        await expect(editControl).toHaveCount(1);
        await editControl.waitFor({ state: "visible", timeout: 20_000 });
        await editControl.click({ timeout: 20_000 });
        await page.locator("#editNodeTitle").fill(`${marker}-edited`);
        const editResponse = page.waitForResponse(r => r.url().includes(`/api/tasks/nodes/${encodeURIComponent(syntheticNodeId)}`) && r.request().method() === "PATCH");
        await page.getByRole("button", { name: /保存|Save/i }).click();
        expect((await editResponse).status()).toBe(200);
        await expect(page.locator("#novaTaskCandidatePanel")).toBeVisible();
        await screenshot(page, "tasks-synthetic.png", screenshots);
        if (!Number(statusBody.l1ReviewCount || statusBody.pendingReviewCount || 0)) {
          skip("Task L1 review mutation", "no isolated synthetic candidate contract; read-only empty review surface verified");
        } else {
          skip("Task L1 review mutation", "existing review items are real data; read-only surface verified");
        }
        return { featureEnabled: true, created: true, edited: true, reviewRead: status.status(), markerSha256: sha256(marker) };
      },
    ));

    await check("RAG enabled search", async () => context(
      { viewport: { width: 1280, height: 900 }, locale: "zh-CN" },
      async ctx => {
        const page = await ctx.newPage();
        await page.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await waitForShell(page);
        const settingsResponse = await ctx.request.get(`${target.origin}/api/rag/settings`);
        const settings = await settingsResponse.json();
        if (settings.rag?.enabled === false || settings.rag?.mode === "disabled") {
          skip("RAG enabled search", "RAG is disabled in this runtime");
          return { enabled: false };
        }
        await page.locator('aside.sidebar [data-page-id="page-rag-search"]').click();
        await page.locator("#ragPageSearchQuery").fill("Actanara release validation");
        const responsePromise = page.waitForResponse(r => r.url().endsWith("/api/rag/search") && r.request().method() === "POST");
        await page.getByRole("button", { name: /搜索|Search/i }).last().click();
        const response = await responsePromise;
        const payload = await response.json();
        expect(response.status()).toBe(200);
        expect(payload.available).not.toBe(false);
        await expect(page.locator("#ragPageSearchResults .fo-job-error")).toHaveCount(0);
        return { enabled: true, available: payload.available !== false, resultCount: Array.isArray(payload.results) ? payload.results.length : 0 };
      },
    ));
  } finally {
    // Restore Settings through a freshly rendered form so concurrent values are
    // not overwritten by a stale whole-file snapshot.
    if (settingsContext && settingsPage && settingsPreimage !== null) {
      try {
        await settingsPage.unrouteAll({ behavior: "ignoreErrors" });
        await settingsPage.goto(`${target.origin}/dashboard`, { waitUntil: "domcontentloaded" });
        await waitForShell(settingsPage);
        await settingsPage.locator('aside.sidebar button[data-i18n="settingsButton"]').click();
        await settingsPage.locator("#modal-body .settings-grid").waitFor({ state: "visible" });
        const current = await settingsPage.locator("#setTargetDay").isChecked();
        if (current !== settingsPreimage) {
          await settingsPage.locator("#setTargetDay").setChecked(settingsPreimage);
          const restored = settingsPage.waitForResponse(r => r.url().endsWith("/api/settings/bundle") && r.request().method() === "PUT");
          await settingsPage.locator('[data-settings-focus-key="save"]').click();
          expect((await restored).status()).toBe(200);
        }
        record("Settings finally restore", true, { restored: true, preimage: settingsPreimage });
      } catch (error) {
        record("Settings finally restore", false, safeError(error));
      }
      await settingsContext.close().catch(() => {});
    }
    if (syntheticNodeId) {
      try {
        const cleanupContext = await browser.newContext();
        const cleanupPage = await cleanupContext.newPage();
        await cleanupPage.goto(`${target.origin}/tasks`, { waitUntil: "domcontentloaded" });
        const csrf = (await cleanupContext.cookies()).find(cookie => cookie.name === "actanara_dashboard_csrf")?.value;
        const response = await cleanupContext.request.patch(`${target.origin}/api/tasks/nodes/${encodeURIComponent(syntheticNodeId)}`, {
          headers: { "X-Actanara-CSRF": csrf || "" },
          data: { status: "archived" },
        });
        record("Task synthetic finally cleanup", response.status() === 200, { cleanup: "archived", status: response.status() });
        await cleanupContext.close();
      } catch (error) {
        record("Task synthetic finally cleanup", false, safeError(error));
      }
    }

    const screenshotHashes = {};
    for (const filename of screenshots) {
      if (fs.existsSync(filename)) screenshotHashes[path.basename(filename)] = sha256(fs.readFileSync(filename));
    }
    const evidence = {
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      browser: { name: "Chrome", version: browser.version() },
      target: target.evidence,
      candidate: {
        manifestSha256: envHash("ACTANARA_DASHBOARD_LIVE_CANDIDATE_MANIFEST_SHA256"),
        payloadSha256: envHash("ACTANARA_DASHBOARD_LIVE_CANDIDATE_PAYLOAD_SHA256"),
      },
      summary: {
        passed: checks.filter(item => item.passed).length,
        failed: checks.filter(item => !item.passed).length,
        skipped: skipped.length,
        total: checks.length,
      },
      checks,
      skipped,
      screenshotsSha256: screenshotHashes,
    };
    fs.writeFileSync(path.join(evidenceDir, "dashboard-live-evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  }

  expect(checks.filter(item => !item.passed).map(item => item.name)).toEqual([]);
});
