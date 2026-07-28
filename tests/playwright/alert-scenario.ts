import { expect, type Page } from "@playwright/test";

export interface AlertScenario {
  externalAlertId: string;
  alertName: string;
  service: string;
  instance: string;
  value: string;
  threshold: string;
  startedAtLocal: string;
  summary: string;
}

export async function fillAlertForm(
  page: Page,
  scenario: AlertScenario,
): Promise<void> {
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
