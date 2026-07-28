import { mkdir } from "node:fs/promises";
import path from "node:path";

import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  fillAlertForm,
  type AlertScenario,
} from "../playwright/alert-scenario";

const assetDirectory = path.resolve(process.cwd(), "docs/assets/showcase");
const scenario: AlertScenario = {
  externalAlertId: "v2-7c-showcase-checkout-database-regression",
  alertName: "HighCPUUsage",
  service: "checkout-service",
  instance: "checkout-api-showcase-01",
  value: "96.4",
  threshold: "80",
  startedAtLocal: "2026-07-28T10:00",
  summary: "CPU saturation after release while database queries exceed two seconds",
};

async function settleForScreenshot(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

async function captureFocusedWorkflow(
  page: Page,
  workflowCard: Locator,
  fileName: string,
): Promise<void> {
  const style = await page.addStyleTag({
    content: ".workflow-timeline { display: none !important; }",
  });
  try {
    await settleForScreenshot(page);
    await workflowCard.screenshot({
      path: path.join(assetDirectory, fileName),
      animations: "disabled",
    });
  } finally {
    await style.evaluate((element) => element.remove());
  }
}

test("captures the V2.7C portfolio evidence", async ({ page }) => {
  await mkdir(assetDirectory, { recursive: true });

  await page.goto("/alerts/new");
  await fillAlertForm(page, scenario);
  await page.getByRole("button", { name: "创建并查看详情" }).click();
  await expect(page).toHaveURL(/\/alerts\/[0-9a-f-]{36}$/);

  await page.getByRole("button", { name: "启动诊断" }).click();
  const workflowPanel = page.locator(".workflow-panel");
  const workflowCard = workflowPanel.locator(
    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' ant-card ')][1]",
  );
  await expect(workflowPanel.getByText("等待人工确认", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByRole("region", { name: "诊断报告 v1" })).toBeVisible();
  await page.addStyleTag({
    content: [
      ".app-header { position: static !important; }",
      ".ant-message { display: none !important; }",
    ].join("\n"),
  });

  const timeline = page.getByRole("region", { name: "诊断链路时间线" });
  const failedLogsEvent = timeline
    .getByRole("listitem")
    .filter({ hasText: "工具：logs" });
  await expect(failedLogsEvent).toHaveCount(1);
  await expect(failedLogsEvent).toContainText("工具失败");

  await captureFocusedWorkflow(page, workflowCard, "01-human-review.png");
  await settleForScreenshot(page);
  await timeline.screenshot({
    path: path.join(assetDirectory, "02-tool-degradation.png"),
    animations: "disabled",
  });

  await page.getByRole("button", { name: "批准建议" }).click();
  await expect(workflowPanel.getByText("已完成", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  const casePanel = page.locator(".case-panel");
  await expect(casePanel.getByText("已同步", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await expect(casePanel.getByText(/^mock-doc-/)).toBeVisible();

  await captureFocusedWorkflow(page, workflowCard, "03-case-synced.png");
});
