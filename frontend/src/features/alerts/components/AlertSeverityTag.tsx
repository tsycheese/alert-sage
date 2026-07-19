import { Tag } from "antd";
import type { ReactElement } from "react";

import type { AlertSeverity } from "../../../api/generated";
import { SEVERITY_LABELS } from "../constants";

const SEVERITY_COLORS: Record<AlertSeverity, string> = {
  info: "blue",
  warning: "orange",
  critical: "red",
};

interface AlertSeverityTagProps {
  severity: AlertSeverity;
}

export function AlertSeverityTag({ severity }: AlertSeverityTagProps): ReactElement {
  return <Tag color={SEVERITY_COLORS[severity]}>{SEVERITY_LABELS[severity]}</Tag>;
}
