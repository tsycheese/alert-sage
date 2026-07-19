import type { AlertSeverity, AlertStatus } from "../../api/generated";

export const ALERT_SEVERITIES = ["info", "warning", "critical"] as const satisfies readonly AlertSeverity[];
export const ALERT_STATUSES = [
  "received",
  "running",
  "waiting_for_approval",
  "reanalyzing",
  "completed",
  "rejected",
  "failed",
] as const satisfies readonly AlertStatus[];

export const SEVERITY_LABELS: Record<AlertSeverity, string> = {
  info: "提示",
  warning: "警告",
  critical: "严重",
};

export const STATUS_LABELS: Record<AlertStatus, string> = {
  received: "已接收",
  running: "诊断中",
  waiting_for_approval: "等待确认",
  reanalyzing: "重新分析",
  completed: "已完成",
  rejected: "已驳回",
  failed: "失败",
};
