import { Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ReactElement } from "react";
import type { ColumnsType } from "antd/es/table";

import type { RagEvaluationResultResponse } from "../../../api/generated";
import {
  formatMilliseconds,
  formatPercent,
  isAbstentionQuery,
  readRetrievedItems,
} from "../model";

const { Text } = Typography;
const difficultyLabels: Record<string, string> = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

function outcome(result: RagEvaluationResultResponse): ReactElement {
  if (result.error_code) return <Tag color="error">调用错误</Tag>;
  if (isAbstentionQuery(result)) {
    return result.abstention_correct ? (
      <Tag color="success">正确拒答</Tag>
    ) : (
      <Tag color="warning">误命中</Tag>
    );
  }
  return result.source_hit_at_k ? (
    <Tag color="success">命中</Tag>
  ) : (
    <Tag color="error">未命中</Tag>
  );
}

const columns: ColumnsType<RagEvaluationResultResponse> = [
  {
    title: "问题",
    width: 360,
    render: (_, result) => (
      <div className="evaluation-query-cell">
        <Text strong className="monospace">
          {result.query_id}
        </Text>
        <Text type="secondary">{result.query_text}</Text>
      </div>
    ),
  },
  {
    title: "难度",
    dataIndex: "difficulty",
    width: 80,
    render: (value: string) => difficultyLabels[value] ?? value,
  },
  {
    title: "结果",
    width: 110,
    render: (_, result) => outcome(result),
  },
  {
    title: "TopK 检索",
    width: 300,
    render: (_, result) => {
      const items = readRetrievedItems(result);
      if (items.length === 0) return <Text type="secondary">无返回片段</Text>;
      return (
        <Space direction="vertical" size={4} className="full-width">
          {items.map((item) => (
            <Tooltip key={`${item.rank}-${item.source}`} title={item.source || item.documentName}>
              <div className="evaluation-retrieved-item">
                <Text className="evaluation-rank">#{item.rank}</Text>
                <Text ellipsis>{item.documentName}</Text>
                <Text type="secondary">{formatPercent(item.score)}</Text>
              </div>
            </Tooltip>
          ))}
        </Space>
      );
    },
  },
  {
    title: "Recall / MRR",
    width: 140,
    render: (_, result) =>
      isAbstentionQuery(result)
        ? "—"
        : `${formatPercent(result.recall_at_k)} / ${formatPercent(result.reciprocal_rank)}`,
  },
  {
    title: "延迟",
    dataIndex: "latency_ms",
    width: 110,
    render: (value: number) => formatMilliseconds(value),
  },
];

export function EvaluationResultTable({
  results,
  loading = false,
}: {
  results: RagEvaluationResultResponse[];
  loading?: boolean;
}): ReactElement {
  return (
    <Table<RagEvaluationResultResponse>
      rowKey="id"
      columns={columns}
      dataSource={results}
      loading={loading}
      scroll={{ x: 1100 }}
      pagination={{ pageSize: 20, hideOnSinglePage: true }}
      locale={{ emptyText: "运行尚未生成逐题结果" }}
    />
  );
}
