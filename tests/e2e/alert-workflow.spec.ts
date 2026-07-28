import { randomUUID } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

interface AlertScenario {
  externalAlertId: string;
  alertName: string;
  service: string;
  instance: string;
  value: string;
  threshold: string;
  startedAtLocal: string;
  summary: string;
}

function createScenario(): AlertScenario {
  return {
    externalAlertId: `e2e-v2-7b-${randomUUID()}`,
    alertName: "HighCPUUsage",
    service: "checkout-service",
    instance: "checkout-api-e2e-01",
    value: "96.4",
    threshold: "80",
    startedAtLocal: "2026-07-28T10:00",
    summary: "CPU saturation after release while database queries exceed two seconds",
  };
}

async function fillAlertForm(page: Page, scenario: AlertScenario): Promise<void> {
  const form = page.getByRole("form", { name: "创建模拟告警" });
  await expect(form).toBeVisible();
  await form.getByLabel("外部告警 ID").fill(scenario.externalAlertId);
  await form.getByLabel("告警名称").fill(scenario.alertName);
  await form.getByLabel("服务").fill(scenario.service);
  await form.getByLabel("实例").fill(scenario.instance);
  await form.getByLabel("当前值").fill(scenario.value);
  await form.getByLabel("阈值").fill(scenario.threshold);
  await form.getByLabel("开始时间").fill(scenario.startedAtLocal);
  await form.getByLabel("告警摘要").fill(scenario.summary);
}

test("completes the durable alert workflow and replays the same alert", async ({ page }) => {
  const scenario = createScenario();

  await page.goto("/alerts/new");
  await fillAlertForm(page, scenario);
  await page.getByRole("button", { name: "创建并查看详情" }).click();

  await expect(page).toHaveURL(/\/alerts\/[0-9a-f-]{36}$/);
  const detailUrl = page.url();
  await expect(page.getByRole("heading", { level: 1, name: scenario.alertName })).toBeVisible();
  await expect(page.getByText(scenario.externalAlertId, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "启动诊断" }).click();
  const workflowPanel = page.locator(".workflow-panel");
  await expect(workflowPanel.getByText("等待人工确认", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.getByRole("region", { name: "诊断报告 v1" })).toBeVisible();

  const timeline = page.getByRole("region", { name: "诊断链路时间线" });
  const failedLogsEvent = timeline
    .getByRole("listitem")
    .filter({ hasText: "工具：logs" });
  await expect(failedLogsEvent).toHaveCount(1);
  await expect(failedLogsEvent).toContainText("工具失败");

  await page.reload();
  await expect(workflowPanel.getByText("等待人工确认", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "诊断报告 v1" })).toBeVisible();

  await page.getByRole("button", { name: "批准建议" }).click();
  await expect(workflowPanel.getByText("已完成", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  const casePanel = page.locator(".case-panel");
  await expect(casePanel).toBeVisible();
  await expect(casePanel.getByText("已同步", { exact: true })).toBeVisible({
    timeout: 120_000,
  });
  await expect(casePanel.getByText(/^mock-doc-/)).toBeVisible();

  await page.goto("/alerts/new");
  await fillAlertForm(page, scenario);
  const replayResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/v1/alerts",
  );
  await page.getByRole("button", { name: "创建并查看详情" }).click();
  const replayResponse = await replayResponsePromise;

  expect(replayResponse.status()).toBe(200);
  expect(replayResponse.headers()["x-idempotent-replay"]).toBe("true");
  await expect(page).toHaveURL(detailUrl);
  await expect(workflowPanel.getByText("已完成", { exact: true })).toBeVisible();
  await expect(casePanel.getByText("已同步", { exact: true })).toBeVisible();
});
