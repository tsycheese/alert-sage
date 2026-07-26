import { Card, Col, Row, Statistic } from "antd";
import type { ReactElement } from "react";

import type { EvaluationAggregateMetrics } from "../model";
import { formatMilliseconds, formatPercent } from "../model";

interface EvaluationMetricGridProps {
  metrics: EvaluationAggregateMetrics;
  topK: number;
}

export function EvaluationMetricGrid({ metrics, topK }: EvaluationMetricGridProps): ReactElement {
  return (
    <Row gutter={[16, 16]} className="evaluation-metric-grid">
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title={`Top${topK} 命中率`} value={formatPercent(metrics.source_hit_rate_at_k)} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title={`Recall@${topK}`} value={formatPercent(metrics.recall_at_k)} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title={`MRR@${topK}`} value={formatPercent(metrics.mean_reciprocal_rank)} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title="拒答准确率" value={formatPercent(metrics.abstention_accuracy)} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title="供应商错误率" value={formatPercent(metrics.error_rate)} />
        </Card>
      </Col>
      <Col xs={12} md={8} xl={4}>
        <Card className="metric-card">
          <Statistic title="P95 检索延迟" value={formatMilliseconds(metrics.latency_p95_ms)} />
        </Card>
      </Col>
    </Row>
  );
}
