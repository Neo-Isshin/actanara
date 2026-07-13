import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  fullyParallel: true,
  reporter: "list",
  use: {
    channel: "chrome",
    trace: "retain-on-failure"
  },
  projects: [
    {
      name: "chromium-desktop",
      use: { ...devices["Desktop Chrome"], channel: "chrome" }
    },
    {
      name: "chromium-mobile",
      use: { ...devices["Pixel 7"], channel: "chrome" }
    }
  ]
});
