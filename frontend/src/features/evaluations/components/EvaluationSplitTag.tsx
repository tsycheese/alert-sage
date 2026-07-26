import { Tag } from "antd";
import type { ReactElement } from "react";

import type { RagEvaluationSplit } from "../../../api/generated";
import { evaluationSplitLabels } from "../model";

export function EvaluationSplitTag({ split }: { split: RagEvaluationSplit }): ReactElement {
  return <Tag color={split === "test" ? "purple" : "blue"}>{evaluationSplitLabels[split]}</Tag>;
}
