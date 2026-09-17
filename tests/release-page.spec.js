import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const releasePageUrl = pathToFileURL(path.join(repoRoot, "docs", "index.html")).href;
const dashboardDemoRoot = path.join(repoRoot, "docs", "dashboard-demo");
const dashboardDemoUrl = pathToFileURL(path.join(dashboardDemoRoot, "index.html")).href;

test("release page renders core content", async ({ page }) => {
  await page.goto(releasePageUrl);

  await expect(page).toHaveTitle("Actanara - Local AI Operations Runtime");
  await expect(page.getByRole("heading", { name: "Actanara", level: 1 })).toBeVisible();
  const productImage = page.getByRole("img", { name: "Actanara v1.8.0 Dashboard with sample data" });
  await expect(productImage).toBeVisible();
  const imageState = await productImage.evaluate(image => ({
    complete: image.complete,
    naturalHeight: image.naturalHeight,
    naturalWidth: image.naturalWidth,
  }));
  expect(imageState.complete).toBe(true);
  expect(imageState.naturalHeight).toBeGreaterThan(0);
  expect(imageState.naturalWidth).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: "专属 Nova-RAG" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nova-Task 任务证据" })).toBeVisible();
});

test("release page local fragment links resolve to existing targets", async ({ page }) => {
  await page.goto(releasePageUrl);

  const fragmentLinks = await page.locator('a[href^="#"]').evaluateAll(links => links.map(link => {
    const href = link.getAttribute("href") || "";
    return {
      href,
      targetExists: href.length > 1 && document.getElementById(href.slice(1)) !== null,
    };
  }));

  expect([...new Set(fragmentLinks.map(link => link.href))].sort()).toEqual([
    "#architecture",
    "#features",
    "#install",
    "#top",
  ]);
  expect(fragmentLinks.filter(link => !link.targetExists)).toEqual([]);
});

test("release page navigation and CTA hrefs match product sections", async ({ page }, testInfo) => {
  await page.goto(releasePageUrl);

  const nav = page.getByRole("navigation", { name: "Primary navigation" });
  const hero = page.locator(".hero");
  const footerCta = page.locator(".cta");
  const fragmentContracts = [
    [nav.getByRole("link", { name: "Actanara home", exact: true }), "#top"],
    [nav.getByRole("link", { name: "功能", exact: true, includeHidden: true }), "#features"],
    [nav.getByRole("link", { name: "架构", exact: true, includeHidden: true }), "#architecture"],
    [nav.getByRole("link", { name: "安装", exact: true, includeHidden: true }), "#install"],
    [hero.getByRole("link", { name: "开始安装", exact: true }), "#install"],
    [hero.getByRole("link", { name: "查看系统功能", exact: true }), "#features"],
  ];

  for (const [link, target] of fragmentContracts) {
    await expect(link).toHaveAttribute("href", target);
  }

  if (testInfo.project.name === "chromium-desktop") {
    for (const [label, target] of [
      ["功能", "#features"],
      ["架构", "#architecture"],
      ["安装", "#install"],
    ]) {
      await nav.getByRole("link", { name: label, exact: true, includeHidden: true }).click();
      expect(new URL(page.url()).hash).toBe(target);
      await expect(page.locator(target)).toBeInViewport();
    }
  }

  for (const [link, target] of fragmentContracts.slice(4)) {
    if (new URL(page.url()).hash === target) {
      await page.evaluate(() => {
        window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#top`);
        document.getElementById("top")?.scrollIntoView();
      });
    }
    await link.click();
    expect(new URL(page.url()).hash).toBe(target);
    await expect(page.locator(target)).toBeInViewport();
  }

  if (testInfo.project.name === "chromium-mobile") {
    for (const link of [
      nav.getByRole("link", { name: "View Repository", exact: true }),
      hero.getByRole("link", { name: "开始安装", exact: true }),
      hero.getByRole("link", { name: "查看系统功能", exact: true }),
      footerCta.getByRole("button", { name: "复制安装命令", exact: true }),
      footerCta.getByRole("link", { name: "阅读 README", exact: true }),
    ]) {
      await link.scrollIntoViewIfNeeded();
      await expect(link).toBeInViewport();
    }
  }
});

test("release page external links match the approved destinations", async ({ page }) => {
  await page.goto(releasePageUrl);

  await expect(page.getByRole("link", { name: "View Repository", exact: true })).toHaveAttribute(
    "href",
    "https://github.com/Neo-Isshin/actanara",
  );
  await expect(page.getByRole("link", { name: "阅读 README", exact: true })).toHaveAttribute(
    "href",
    "https://github.com/Neo-Isshin/actanara/blob/main/README.md",
  );
  await expect(page.getByRole("link", { name: "MIT", exact: true })).toHaveAttribute(
    "href",
    "https://github.com/Neo-Isshin/actanara/blob/main/LICENSE",
  );
});

test("release page copy CTA writes the exact visible hosted install command", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__actanaraCopiedText = null;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async value => {
          window.__actanaraCopiedText = value;
        },
      },
    });
  });

  const command = (await page.locator("#install-command").textContent()).trim();
  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__actanaraCopiedText)).toBe(command);
  expect(command).toBe(
    "curl -fsSL https://github.com/Neo-Isshin/actanara/releases/latest/download/install.sh | sh",
  );
});

test("release page copy CTA uses and cleans fallback when Clipboard API is absent", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__actanaraFallbackText = null;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    document.execCommand = command => {
      window.__actanaraFallbackText = document.querySelector("textarea")?.value || null;
      return command === "copy";
    };
  });

  const command = (await page.locator("#install-command").textContent()).trim();
  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__actanaraFallbackText)).toBe(command);
  await expect(page.locator("textarea")).toHaveCount(0);
});

test("release page copy CTA falls back after Clipboard API rejection", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__actanaraFallbackCalls = 0;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async () => { throw new Error("permission denied"); } },
    });
    document.execCommand = () => {
      window.__actanaraFallbackCalls += 1;
      return true;
    };
  });

  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__actanaraFallbackCalls)).toBe(1);
  await expect(page.locator("textarea")).toHaveCount(0);
});

test("release page copy CTA reports fallback failure and still cleans temporary textarea", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    document.execCommand = () => false;
  });

  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("复制失败，请手动选择上方命令。");
  await expect(page.locator("textarea")).toHaveCount(0);
});

test("release page has no horizontal overflow", async ({ page }) => {
  await page.goto(releasePageUrl);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("static dashboard demo exposes the v1.8 asset-first Dashboard pages", async ({ page }, testInfo) => {
  test.slow();
  await page.goto(dashboardDemoUrl);

  await expect(page).toHaveTitle("Actanara");
  for (const profile of ["en", "zh"]) {
    await page.evaluate(value => window.applyStaticDashboardText(value), profile);
    await expect(page).toHaveTitle("Actanara");
  }
  await expect(page.locator("#page-home")).toBeVisible();
  await expect(page.locator("#dashboardMetrics")).toContainText("任务成果");
  await expect(page.locator("#dashboardRecent .dash-asset-row")).toHaveCount(12);
  await expect(page.locator("#page-home")).not.toContainText("9.52B");
  await expect(page.locator(".demo-notice")).toContainText("v1.8.0");
  await page.evaluate(() => window.showPage("overview"));
  await expect(page.locator("#page-overview .page-title")).toHaveText("用量与活动");
  await expect(page.locator("#agentTableContainer")).toContainText("活跃");
  await expect(page.locator("#agentTableContainer")).not.toContainText("undefined");

  if (testInfo.project.name === 'chromium-desktop') {
    await expect(page.locator(".sidebar-bottom-nav")).toBeVisible();
    await expect(page.locator(".sidebar-utility")).toBeVisible();
  } else await expect(page.locator('.mobile-bottom-nav')).toBeVisible();
  await expect(page.locator('#month-nav .nav-item[onclick*="loadMonthlyReportById"]')).toHaveCount(1);

  await page.evaluate(() => window.showPage("static"));
  await expect(page.locator("#page-static")).toBeVisible();
  await expect(page.locator("#page-static .page-title")).toHaveText("AI 资产总览");
  await expect(page.locator('#dashboardCanonicalSkillList [data-dash-skill]')).toHaveCount(2);
  await page.locator('#dashboardCanonicalSkillList [data-dash-skill]').first().click();
  await expect(page.locator('#modal-body')).toContainText('Verify restored files');
  await page.keyboard.press('Escape');
  await expect(page.locator("#aaDevices .aa-device-name").filter({hasText: 'Mac mini (Isshin)'})).toHaveCount(1);
  await expect(page.locator("#aaDevices .aa-device-name").filter({hasText: '华硕路由器'})).toHaveCount(1);
  await expect(page.locator("#aaDevices")).toContainText("Mac mini (Isshin)");
  await expect(page.locator("#aaDevices")).toContainText("华硕路由器");

  await page.evaluate(() => window.showPage('home'));
  await page.locator('#dashboardAssetSearch').fill('备份');
  await expect(page.locator('#dashboardRecent .dash-asset-row')).toHaveCount(1);
  await page.locator('#dashboardAssetSearch').fill('');
  await page.locator('#dashboardAssetType').selectOption('report');
  await expect(page.locator('#dashboardRecent .dash-asset-row')).toHaveCount(6);
  await page.locator('#dashboardRecent .dash-asset-row').first().click();
  await page.locator('[data-dash-open-record]').click();
  await expect(page.locator('#modal-body')).toContainText('在线演示文档，非真实运行记录');
  await page.keyboard.press('Escape');
  await page.evaluate(() => window.showPage('rag-search'));
  await page.locator('#ragPageSearchQuery').fill('验证');
  await page.locator('#ragPageSearchQuery').press('Enter');
  await expect(page.locator('#ragPageSearchResults')).toContainText('demo-lexical');
  await expect(page.locator('#ragPageSearchResults')).toContainText('恢复完成不等于恢复验证通过');
  const mutation = await page.evaluate(async () => {
    const response = await fetch('/api/ai-assets/refresh', {method: 'POST'});
    return {status: response.status, body: await response.json()};
  });
  expect(mutation.status).toBe(403);
  expect(mutation.body.code).toBe('demo-read-only');
  await page.locator('#historyBackfillButton').click();
  await expect(page.locator('#modal-body')).toContainText('这项操作需要连接本地');
  await page.keyboard.press('Escape');

  await page.evaluate(() => window.loadReport("2026-W27"));
  await expect(page.locator("#page-report-2026-W27")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#page-report-2026-W27")).toContainText("W27");

  await page.evaluate(() => window.showDiaryByDate("2026-07-05", null));
  await expect(page.locator("#page-day-0705")).toBeVisible();
  await expect(page.locator("#page-day-0705")).toContainText("Xcode.app（已删除）");

  await page.evaluate(() => window.showDiaryByDate("2026-07-04", null));
  await expect(page.locator("#page-day-0704")).toBeVisible();
  await expect(page.locator("#page-day-0704")).toContainText("今日无活动");

  await page.evaluate(() => window.showDiaryByDate("2026-07-03", null));
  await expect(page.locator("#page-day-0703")).toBeVisible();

  await expect(page.locator('a[href="tasks.html"]').first()).toHaveAttribute("href", "tasks.html");
  await page.goto(new URL("tasks.html", dashboardDemoUrl).href);
  await expect(page).toHaveTitle("Actanara — Nova Task");
  for (const profile of ["en", "zh"]) {
    await page.evaluate(value => {
      taskLanguageProfile = value;
      applyTaskText();
    }, profile);
    await expect(page).toHaveTitle("Actanara — Nova Task");
  }
  await expect(page.getByRole("heading", { name: "任务看板", exact: true })).toBeVisible();
});

test("static dashboard demo aggregates both SSE transports without conflating source health", async ({ page }) => {
  await page.goto(dashboardDemoUrl);
  const status = page.locator("#sseStatus");
  await expect(status).toHaveText("🟢 已连接");

  await page.evaluate(() => {
    ACTANARA_SSE_STREAM_STATES.clear();
    updateSseStreamState("tokens", { transport: "connecting", retrySeconds: 0, sourceWarnings: [] });
    updateSseStreamState("tasks", { transport: "connecting", retrySeconds: 0, sourceWarnings: [] });
    updateSseStreamState("tokens", { transport: "connected" });
  });
  await expect(status).toHaveText("⏳ 连接中");

  await page.evaluate(() => {
    updateSseStreamState("tasks", { transport: "connected" });
  });
  await expect(status).toHaveText("🟢 已连接");

  await page.evaluate(() => {
    const sourceWarnings = sseSourceWarnings({
      dashboardState: {
        status: "degraded",
        sourceErrors: [{ source: "token-clock", code: "scan-failed" }],
      },
    });
    updateSseStreamState("tokens", { transport: "connected", sourceWarnings });
  });
  await expect(status).toHaveText("🟢 已连接");
  await expect(status).toHaveAttribute("data-source-health", "degraded");
  await expect(status).toHaveAttribute("aria-label", /数据源告警：token-clock: scan-failed/);

  await page.evaluate(() => {
    updateSseStreamState("tokens", {
      transport: "reconnecting",
      retrySeconds: 2,
      sourceWarnings: [],
    });
  });
  await expect(status).toHaveText("🔴 重连 2s");
});

test("static dashboard demo has no horizontal overflow", async ({ page }) => {
  await page.goto(dashboardDemoUrl);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("static dashboard demo uses local assets and excludes private hosts, machine paths, and credentials", async () => {
  const publicTextFiles = [
    "README.md",
    "index.html",
    "tasks.html",
    "css/style.css",
    "css/dashboard.css",
    "css/demo.css",
    "js/app.js",
    "js/dashboard.js",
    "js/demo-assets.js",
    "js/autorefresh.js",
    "js/static-mock.js",
  ];
  const source = publicTextFiles
    .map(file => fs.readFileSync(path.join(dashboardDemoRoot, file), "utf8"))
    .join("\n");

  for (const privateLocationPattern of [
    /(?<![a-z0-9._-])\/(?:Users?|Volumes)\/[^/\s"'<>]+/i,
    /-Users-[a-z0-9_-]+--/i,
    /\bgitea\.[a-z0-9.-]+\b/i,
    /\b[a-z0-9.-]+\.cloud\b/i,
    /\b(?:git@|ssh:\/\/)[a-z0-9.-]+\b/i,
  ]) {
    expect(source).not.toMatch(privateLocationPattern);
  }

  for (const credentialPattern of [
    /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
    /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
    /\bsk-[A-Za-z0-9_-]{20,}\b/,
    /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/,
    /Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~-]{12,}/i,
  ]) {
    expect(source).not.toMatch(credentialPattern);
  }

  for (const pageFile of ["index.html", "tasks.html"]) {
    const html = fs.readFileSync(path.join(dashboardDemoRoot, pageFile), "utf8");
    expect(html).not.toMatch(/<script[^>]+src=["']https?:\/\//i);
    expect(html).toContain('src="js/static-mock.js"');
  }

  expect(fs.existsSync(path.join(dashboardDemoRoot, "js", "vendor", "chart.umd.min.js"))).toBe(true);
  expect(fs.existsSync(path.join(dashboardDemoRoot, "js", "vendor", "marked.min.js"))).toBe(true);
  const demoEntries = fs.readdirSync(dashboardDemoRoot, { recursive: true });
  expect(demoEntries.some(entry => path.basename(entry) === ".DS_Store")).toBe(false);
});
