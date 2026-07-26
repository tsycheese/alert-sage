import { Button, Space, Table, Typography } from "antd";
import type { ReactElement } from "react";
import type { ColumnsType, TableRowSelection } from "antd/es/table/interface";
import { Link } from "react-router";

import type { RagEvaluationRunResponse } from "../../../api/generated";
import {
  areRunsComparable,
  formatDateTime,
  formatPercent,
  formatThreshold,
  readEvaluationMetrics,
} from "../model";
import { EvaluationSplitTag } from "./EvaluationSplitTag";
import { EvaluationStatusTag } from "./EvaluationStatusTag";

const { Text } = Typography;

const columns: ColumnsType<RagEvaluationRunResponse> = [
  {
    title: "运行",
    dataIndex: "id",
    width: 270,
    render: (id: string, run) => (
      <div className="table-primary-cell">
        <Link to={`/evaluations/${id}`} className="alert-name-link monospace">
          {id}
        </Link>
        <Text type="secondary" className="table-secondary-text">
          {formatDateTime(run.created_at)}
        </Text>
      </div>
    ),
  },
  {
    title: "分组 / 状态",
    width: 180,
    render: (_, run) => (
      <Space size={[4, 4]} wrap>
        <EvaluationSplitTag split={run.split} />
        <EvaluationStatusTag status={run.status} />
      </Space>
    ),
  },
  {
    title: "检索参数",
    width: 150,
    render: (_, run) => (
      <div className="table-primary-cell">
        <Text>TopK {run.top_k}</Text>
        <Text type="secondary" className="table-secondary-text">
          阈值 {formatThreshold(run.score_threshold)}
        </Text>
      </div>
    ),
  },
  {
    title: "核心结果",
    width: 190,
    render: (_, run) => {
      const metrics = readEvaluationMetrics(run);
      return metrics ? (
        <div className="table-primary-cell">
          <Text>命中 {formatPercent(metrics.source_hit_rate_at_k)}</Text>
          <Text type="secondary" className="table-secondary-text">
            MRR {formatPercent(metrics.mean_reciprocal_rank)} · 错误 {formatPercent(metrics.error_rate)}
          </Text>
        </div>
      ) : (
        <Text type="secondary">等待结果</Text>
      );
    },
  },
  {
    title: "进度",
    width: 110,
    render: (_, run) => `${run.completed_query_count}/${run.query_count}`,
  },
  {
    title: "操作",
    fixed: "right",
    width: 90,
    render: (_, run) => (
      <Link to={`/evaluations/${run.id}`}>
        <Button type="link" size="small">
          查看
        </Button>
      </Link>
    ),
  },
];

interface EvaluationRunTableProps {
  items: RagEvaluationRunResponse[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onPageChange: (page: number, pageSize: number) => void;
}

export function EvaluationRunTable({
  items,
  total,
  page,
  pageSize,
  loading,
  selectedIds,
  onSelectionChange,
  onPageChange,
}: EvaluationRunTableProps): ReactElement {
  const firstSelected = items.find((item) => item.id === selectedIds[0]);
  const rowSelection: TableRowSelection<RagEvaluationRunResponse> = {
    selectedRowKeys: selectedIds,
    onChange: (keys) => onSelectionChange(keys.map(String).slice(0, 2)),
    getCheckboxProps: (run) => ({
      disabled:
        run.status !== "completed" ||
        (firstSelected !== undefined &&
          run.id !== firstSelected.id &&
          !areRunsComparable(firstSelected, run)),
      name: `选择运行 ${run.id}`,
      "aria-label": `选择运行 ${run.id}`,
    }),
  };

  return (
    <Table<RagEvaluationRunResponse>
      rowKey="id"
      columns={columns}
      dataSource={items}
      rowSelection={rowSelection}
      loading={loading}
      scroll={{ x: 1050 }}
      locale={{ emptyText: "暂无符合条件的评测运行" }}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: [10, 20, 50],
        showTotal: (count) => `共 ${count} 条`,
        onChange: onPageChange,
      }}
    />
  );
}
