import { Tag } from "antd";
import type { ReactElement } from "react";

import type { RagEvaluationRunStatus } from "../../../api/generated";
import { evaluationStatusColors, evaluationStatusLabels } from "../model";

export function EvaluationStatusTag({
  status,
}: {
  status: RagEvaluationRunStatus;
}): ReactElement {
  return <Tag color={evaluationStatusColors[status]}>{evaluationStatusLabels[status]}</Tag>;
}
