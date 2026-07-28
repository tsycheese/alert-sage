import { defineConfig } from "@playwright/test";

import { chromiumProject, sharedUse } from "../playwright/shared-config";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 150_000,
  expect: {
    timeout: 20_000,
  },
  outputDir: "../../test-results/showcase",
  reporter: [["line"]],
  use: sharedUse,
  projects: [
    {
      ...chromiumProject,
      use: {
        ...chromiumProject.use,
        viewport: { width: 980, height: 900 },
      },
    },
  ],
});
