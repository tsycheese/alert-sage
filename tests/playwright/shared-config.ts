import { devices } from "@playwright/test";

export const baseURL =
  process.env.ALERT_SAGE_E2E_BASE_URL ?? "http://127.0.0.1:25173";

export const sharedUse = {
  baseURL,
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  trace: "retain-on-failure" as const,
  screenshot: "only-on-failure" as const,
  video: "retain-on-failure" as const,
};

export const chromiumProject = {
  name: "chromium",
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
  },
};
