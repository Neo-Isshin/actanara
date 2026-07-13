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

  await expect(page).toHaveTitle("Open Nova - Local AI Operations Runtime");
  await expect(page.getByRole("heading", { name: "Open Nova", level: 1 })).toBeVisible();
  const productImage = page.getByRole("img", { name: "Open Nova dashboard product mockup" });
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
    [nav.getByRole("link", { name: "Open Nova home", exact: true }), "#top"],
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
    "https://github.com/Neo-Isshin/open-nova",
  );
  await expect(page.getByRole("link", { name: "阅读 README", exact: true })).toHaveAttribute(
    "href",
    "https://github.com/Neo-Isshin/open-nova/blob/v1.0.0/README.md",
  );
  await expect(page.getByRole("link", { name: "GPL-3.0-or-later", exact: true })).toHaveAttribute(
    "href",
    "https://github.com/Neo-Isshin/open-nova/blob/v1.0.0/LICENSE",
  );
});

test("release page copy CTA writes the exact visible hosted install command", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__openNovaCopiedText = null;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async value => {
          window.__openNovaCopiedText = value;
        },
      },
    });
  });

  const command = (await page.locator("#install-command").textContent()).trim();
  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__openNovaCopiedText)).toBe(command);
  expect(command).toBe(
    "zsh -c \"$(curl -fsSL 'https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.0/install/bootstrap.sh')\"",
  );
});

test("release page copy CTA uses and cleans fallback when Clipboard API is absent", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__openNovaFallbackText = null;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    document.execCommand = command => {
      window.__openNovaFallbackText = document.querySelector("textarea")?.value || null;
      return command === "copy";
    };
  });

  const command = (await page.locator("#install-command").textContent()).trim();
  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__openNovaFallbackText)).toBe(command);
  await expect(page.locator("textarea")).toHaveCount(0);
});

test("release page copy CTA falls back after Clipboard API rejection", async ({ page }) => {
  await page.goto(releasePageUrl);
  await page.evaluate(() => {
    window.__openNovaFallbackCalls = 0;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async () => { throw new Error("permission denied"); } },
    });
    document.execCommand = () => {
      window.__openNovaFallbackCalls += 1;
      return true;
    };
  });

  await page.getByRole("button", { name: "复制安装命令", exact: true }).click();

  await expect(page.getByRole("status")).toHaveText("安装命令已复制。");
  expect(await page.evaluate(() => window.__openNovaFallbackCalls)).toBe(1);
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

test("static dashboard demo exposes the representative synthetic pages", async ({ page }) => {
  await page.goto(dashboardDemoUrl);

  await expect(page).toHaveTitle("Open Nova Dashboard Demo");
  await expect(page.getByRole("heading", { name: "当日实时总览" })).toBeVisible();

  for (const [route, heading] of [
    ["assets", "AI 资产总览"],
    ["diary-a", "项目交付日记"],
    ["diary-b", "系统优化日记"],
    ["blank", "无活动日记"],
    ["weekly", "周报总览"],
    ["monthly", "月报总览"],
    ["tasks", "任务看板"],
    ["rag", "长期记忆搜索"],
  ]) {
    await page.locator(`button[data-route="${route}"]`).click();
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }

  await page.locator("#open-settings").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("这是合成数据静态示例。保存、服务控制和写操作均已禁用。")).toBeVisible();
});

test("static dashboard demo has no horizontal overflow", async ({ page }) => {
  await page.goto(dashboardDemoUrl);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("static dashboard demo is self-contained and excludes private runtime data", async () => {
  const source = ["index.html", "style.css", "app.js"]
    .map(file => fs.readFileSync(path.join(dashboardDemoRoot, file), "utf8"))
    .join("\n");

  for (const forbidden of [
    "/Users/",
    "/Volumes/",
    "secretRef",
    "apiKey",
    "Authorization",
    "Bearer ",
    "navigator.sendBeacon",
    "XMLHttpRequest",
    "EventSource(",
    "fetch(",
  ]) {
    expect(source).not.toContain(forbidden);
  }
});
