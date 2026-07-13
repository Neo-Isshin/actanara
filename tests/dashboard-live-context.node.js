import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { withBrowserContext } from "./dashboard-live-context.js";

test("route cleanup runs before context close", async () => {
  const calls = [];
  let releaseCleanup;
  const cleanupGate = new Promise(resolve => { releaseCleanup = resolve; });
  const page = {
    async unrouteAll(options) {
      calls.push(["unrouteAll", options]);
      await cleanupGate;
    },
  };
  const context = {
    pages: () => [page],
    async unrouteAll(options) { calls.push(["context.unrouteAll", options]); },
    async close() { calls.push(["close"]); },
  };
  const browser = { async newContext() { return context; } };

  const result = withBrowserContext(browser, {}, async () => "done");
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [["unrouteAll", { behavior: "ignoreErrors" }]]);
  releaseCleanup();

  assert.equal(await result, "done");
  assert.deepEqual(calls, [
    ["unrouteAll", { behavior: "ignoreErrors" }],
    ["context.unrouteAll", { behavior: "ignoreErrors" }],
    ["close"],
  ]);
});

test("cleanup failures do not mask the action failure", async () => {
  const primary = new Error("primary action failed");
  const page = { async unrouteAll() { throw new Error("route cleanup failed"); } };
  const context = {
    pages: () => [page],
    async unrouteAll() {},
    async close() { throw new Error("context close failed"); },
  };
  const browser = { async newContext() { return context; } };

  await assert.rejects(
    withBrowserContext(browser, {}, async () => { throw primary; }),
    error => error === primary,
  );
});

test("cleanup failure is reported when the action succeeds", async () => {
  const cleanup = new Error("route cleanup failed");
  const page = { async unrouteAll() { throw cleanup; } };
  const context = { pages: () => [page], async unrouteAll() {}, async close() {} };
  const browser = { async newContext() { return context; } };

  await assert.rejects(
    withBrowserContext(browser, {}, async () => "done"),
    error => error === cleanup,
  );
});

test("context route cleanup failure is reported when the action succeeds", async () => {
  const cleanup = new Error("context route cleanup failed");
  const context = {
    pages: () => [],
    async unrouteAll() { throw cleanup; },
    async close() {},
  };
  const browser = { async newContext() { return context; } };

  await assert.rejects(
    withBrowserContext(browser, {}, async () => "done"),
    error => error === cleanup,
  );
});

test("live gate overlays the effective language and uses absolute context request URLs", () => {
  const specPath = fileURLToPath(new URL("./dashboard-live.spec.js", import.meta.url));
  const source = fs.readFileSync(specPath, "utf8");

  assert.match(source, /body\.pipeline\s*=\s*\{[^\n]*languageProfile:\s*locale/);
  assert.match(source, /waitForFunction\(expected\s*=>\s*document\.documentElement\.lang\s*===\s*expected/);
  assert.match(source, /waitForShell\(page,\s*\{\s*mobile:\s*item\.mobile\s*\}\)/);
  assert.match(source, /real token-clock scan leaves Dashboard health responsive/);
  assert.match(source, /installTokenClockSnapshot\(ctx,\s*tokenClockSnapshot\)/);
  assert.match(source, /editControl\.waitFor\(\{\s*state:\s*"visible"/);
  assert.doesNotMatch(
    source,
    /(?:ctx|cleanupContext)\.request\.(?:get|post|put|patch|delete)\(\s*["'`]\/api\//,
  );
});
