import { Button, Form, Select, Space } from "antd";
import type { ReactElement } from "react";

import type { RagEvaluationRunStatus, RagEvaluationSplit } from "../../../api/generated";
import type { RagEvaluationListParams } from "../../../api/http";
import { evaluationSplitLabels, evaluationStatusLabels } from "../model";

interface EvaluationFilterValues {
  split?: RagEvaluationSplit;
  status?: RagEvaluationRunStatus;
}

interface EvaluationFiltersProps {
  values: RagEvaluationListParams;
  onApply: (values: EvaluationFilterValues) => void;
  onReset: () => void;
}

const splitOptions = Object.entries(evaluationSplitLabels).map(([value, label]) => ({
  value,
  label,
}));
const statusOptions = Object.entries(evaluationStatusLabels).map(([value, label]) => ({
  value,
  label,
}));

export function EvaluationFilters({
  values,
  onApply,
  onReset,
}: EvaluationFiltersProps): ReactElement {
  return (
    <Form<EvaluationFilterValues>
      layout="inline"
      className="evaluation-filters"
      initialValues={{ split: values.split, status: values.status }}
      onFinish={onApply}
    >
      <Form.Item name="split" label="数据分组">
        <Select allowClear placeholder="全部分组" options={splitOptions} className="filter-select" />
      </Form.Item>
      <Form.Item name="status" label="运行状态">
        <Select allowClear placeholder="全部状态" options={statusOptions} className="filter-select" />
      </Form.Item>
      <Form.Item className="filter-actions">
        <Space>
          <Button type="primary" htmlType="submit">
            筛选
          </Button>
          <Button htmlType="button" onClick={onReset}>
            重置
          </Button>
        </Space>
      </Form.Item>
    </Form>
  );
}
