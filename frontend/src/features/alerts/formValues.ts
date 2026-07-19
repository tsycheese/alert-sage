import type { AlertCreate, AlertSeverity } from "../../api/generated";

export interface AlertCreateFormValues {
  source: string;
  external_alert_id: string;
  alert_name: string;
  service: string;
  instance?: string;
  severity: AlertSeverity;
  value: number;
  threshold: number;
  started_at_local: string;
  payload_summary?: string;
}

function localDateTimeValue(date: Date): string {
  const localTime = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localTime.toISOString().slice(0, 16);
}

export function createDemoFormValues(): AlertCreateFormValues {
  return {
    source: "web",
    external_alert_id: `web-demo-${Date.now()}`,
    alert_name: "HighCPUUsage",
    service: "order-service",
    instance: "order-service-01",
    severity: "critical",
    value: 92.5,
    threshold: 80,
    started_at_local: localDateTimeValue(new Date()),
    payload_summary: "CPU 使用率持续 5 分钟超过阈值",
  };
}

export function toAlertCreate(values: AlertCreateFormValues): AlertCreate {
  return {
    source: values.source,
    external_alert_id: values.external_alert_id.trim(),
    alert_name: values.alert_name.trim(),
    service: values.service.trim(),
    instance: values.instance?.trim() || null,
    severity: values.severity,
    value: values.value,
    threshold: values.threshold,
    started_at: new Date(values.started_at_local).toISOString(),
    payload: values.payload_summary?.trim()
      ? { summary: values.payload_summary.trim() }
      : {},
  };
}
