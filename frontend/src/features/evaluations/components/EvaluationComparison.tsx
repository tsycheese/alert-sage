import { Alert, Card, Col, Row, Space, Table, Tag, Typography } from "antd";
import type { ReactElement } from "react";
import type { ColumnsType } from "antd/es/table";

import type {
  RagEvaluationDetailResponse,
  RagEvaluationResultResponse,
} from "../../../api/generated";
import {
  formatMilliseconds,
  formatPercent,
  formatThreshold,
  isAbstentionQuery,
  readEvaluationMetrics,
} from "../model";

const { Text, Title } = Typography;

interface MetricDefinition {
  key: string;
  label: string;
  read: (metrics: NonNullable<ReturnType<typeof readEvaluationMetrics>>) => number | null;
  kind: "percent" | "milliseconds";
  higherIsBetter: boolean;
}

const metricDefinitions: MetricDefinition[] = [
  {
    key: "source-hit",
    label: "TopK 命中率",
    read: (metrics) => metrics.source_hit_rate_at_k,
    kind: "percent",
    higherIsBetter: true,
  },
  {
    key: "recall",
    label: "Recall@K",
    read: (metrics) => metrics.recall_at_k,
    kind: "percent",
    higherIsBetter: true,
  },
  {
    key: "mrr",
    label: "MRR@K",
    read: (metrics) => metrics.mean_reciprocal_rank,
    kind: "percent",
    higherIsBetter: true,
  },
  {
    key: "abstention",
    label: "拒答准确率",
    read: (metrics) => metrics.abstention_accuracy,
    kind: "percent",
    higherIsBetter: true,
  },
  {
    key: "false-positive",
    label: "无答案误命中率",
    read: (metrics) => metrics.no_answer_false_positive_rate,
    kind: "percent",
    higherIsBetter: false,
  },
  {
    key: "error-rate",
    label: "供应商错误率",
    read: (metrics) => metrics.error_rate,
    kind: "percent",
    higherIsBetter: false,
  },
  {
    key: "p50",
    label: "P50 延迟",
    read: (metrics) => metrics.latency_p50_ms,
    kind: "milliseconds",
    higherIsBetter: false,
  },
  {
    key: "p95",
    label: "P95 延迟",
    read: (metrics) => metrics.latency_p95_ms,
    kind: "milliseconds",
    higherIsBetter: false,
  },
];

interface ComparisonMetricRow {
  key: string;
  label: string;
  left: number | null;
  right: number | null;
  kind: "percent" | "milliseconds";
  higherIsBetter: boolean;
}

function formatMetric(value: number | null, kind: ComparisonMetricRow["kind"]): string {
  return kind === "percent" ? formatPercent(value) : formatMilliseconds(value);
}

function deltaCell(row: ComparisonMetricRow): ReactElement {
  if (row.left === null || row.right === null) return <Text type="secondary">—</Text>;
  const delta = row.right - row.left;
  const improved = row.higherIsBetter ? delta > 0 : delta < 0;
  const unchanged = Math.abs(delta) < Number.EPSILON;
  const display =
    row.kind === "percent"
      ? `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)} pp`
      : `${delta >= 0 ? "+" : ""}${Math.round(delta)} ms`;
  return <Text type={unchanged ? "secondary" : improved ? "success" : "danger"}>{display}</Text>;
}

const metricColumns: ColumnsType<ComparisonMetricRow> = [
  { title: "指标", dataIndex: "label", width: 190 },
  { title: "左侧运行", render: (_, row) => formatMetric(row.left, row.kind) },
  { title: "右侧运行", render: (_, row) => formatMetric(row.right, row.kind) },
  { title: "右侧变化", render: (_, row) => deltaCell(row) },
];

interface QueryComparisonRow {
  queryId: string;
  left: RagEvaluationResultResponse | null;
  right: RagEvaluationResultResponse | null;
}

function queryOutcome(result: RagEvaluationResultResponse | null): string {
  if (!result) return "缺少结果";
  if (result.error_code) return "调用错误";
  if (isAbstentionQuery(result)) return result.abstention_correct ? "正确拒答" : "误命中";
  if (!result.source_hit_at_k) return "未命中";
  if (!result.reciprocal_rank) return "命中（排名未知）";
  return `命中 #${Math.round(1 / result.reciprocal_rank)}`;
}

const queryColumns: ColumnsType<QueryComparisonRow> = [
  {
    title: "问题",
    dataIndex: "queryId",
    width: 280,
    render: (value: string) => <Text className="monospace">{value}</Text>,
  },
  { title: "左侧结果", render: (_, row) => queryOutcome(row.left) },
  { title: "右侧结果", render: (_, row) => queryOutcome(row.right) },
  {
    title: "延迟变化",
    render: (_, row) => {
      if (!row.left || !row.right) return "—";
      const delta = row.right.latency_ms - row.left.latency_ms;
      return (
        <Text type={delta < 0 ? "success" : delta > 0 ? "danger" : "secondary"}>
          {delta >= 0 ? "+" : ""}
          {Math.round(delta)} ms
        </Text>
      );
    },
  },
];

function RunSummaryCard({
  title,
  detail,
}: {
  title: string;
  detail: RagEvaluationDetailResponse;
}): ReactElement {
  return (
    <Card className="evaluation-compare-run-card">
      <Space direction="vertical" size={6}>
        <Text type="secondary">{title}</Text>
        <Text strong className="monospace evaluation-wrap-id">
          {detail.run.id}
        </Text>
        <Space wrap>
          <Tag>TopK {detail.run.top_k}</Tag>
          <Tag>阈值 {formatThreshold(detail.run.score_threshold)}</Tag>
          <Tag>{detail.run.provider}</Tag>
        </Space>
      </Space>
    </Card>
  );
}

export function EvaluationComparison({
  left,
  right,
}: {
  left: RagEvaluationDetailResponse;
  right: RagEvaluationDetailResponse;
}): ReactElement {
  const leftMetrics = readEvaluationMetrics(left.run);
  const rightMetrics = readEvaluationMetrics(right.run);
  if (!leftMetrics || !rightMetrics) {
    return <Alert type="warning" showIcon title="运行缺少可比较的汇总指标" />;
  }
  const metricRows = metricDefinitions.map((definition) => ({
    ...definition,
    left: definition.read(leftMetrics),
    right: definition.read(rightMetrics),
  }));
  const leftResults = new Map(left.results.map((result) => [result.query_id, result]));
  const rightResults = new Map(right.results.map((result) => [result.query_id, result]));
  const queryIds = [...new Set([...leftResults.keys(), ...rightResults.keys()])].sort();
  const queryRows = queryIds.map((queryId) => ({
    queryId,
    left: leftResults.get(queryId) ?? null,
    right: rightResults.get(queryId) ?? null,
  }));

  return (
    <Space direction="vertical" size={16} className="full-width">
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <RunSummaryCard title="左侧运行" detail={left} />
        </Col>
        <Col xs={24} lg={12}>
          <RunSummaryCard title="右侧运行" detail={right} />
        </Col>
      </Row>
      <Card className="content-card table-card">
        <div className="table-heading">
          <Title level={2}>核心指标变化</Title>
          <Text type="secondary">绿色表示右侧改善，红色表示右侧退化。</Text>
        </div>
        <Table<ComparisonMetricRow>
          rowKey="key"
          columns={metricColumns}
          dataSource={metricRows}
          pagination={false}
          scroll={{ x: 680 }}
        />
      </Card>
      <Card className="content-card table-card">
        <div className="table-heading">
          <Title level={2}>逐题差异</Title>
          <Text type="secondary">用于定位排名、拒答和延迟变化，不重新评价 test 参数。</Text>
        </div>
        <Table<QueryComparisonRow>
          rowKey="queryId"
          columns={queryColumns}
          dataSource={queryRows}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          scroll={{ x: 760 }}
        />
      </Card>
    </Space>
  );
}
