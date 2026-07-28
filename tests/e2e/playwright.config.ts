import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.ALERT_SAGE_E2E_BASE_URL ?? "http://127.0.0.1:25173";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 150_000,
  expect: {
    timeout: 20_000,
  },
  outputDir: "../../test-results/playwright",
  reporter: [
    ["line"],
    ["html", { outputFolder: "../../playwright-report", open: "never" }],
  ],
  use: {
    baseURL,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
