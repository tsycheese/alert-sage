import { Tag } from "antd";
import type { ReactElement } from "react";

import type { AlertStatus } from "../../../api/generated";
import { STATUS_LABELS } from "../constants";

const STATUS_COLORS: Record<AlertStatus, string> = {
  received: "default",
  running: "processing",
  waiting_for_approval: "gold",
  reanalyzing: "purple",
  completed: "success",
  rejected: "error",
  failed: "error",
};

interface AlertStatusTagProps {
  status: AlertStatus;
}

export function AlertStatusTag({ status }: AlertStatusTagProps): ReactElement {
  return <Tag color={STATUS_COLORS[status]}>{STATUS_LABELS[status]}</Tag>;
}
