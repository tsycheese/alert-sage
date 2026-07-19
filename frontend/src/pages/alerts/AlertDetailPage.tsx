import { ArrowLeftOutlined, ClockCircleOutlined, DatabaseOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Result,
  Row,
  Skeleton,
  Space,
  Statistic,
  Typography,
} from "antd";
import type { ReactElement } from "react";
import { Link, useParams } from "react-router";

import { ApiClientError } from "../../api/http";
import { AlertSeverityTag } from "../../features/alerts/components/AlertSeverityTag";
import { AlertStatusTag } from "../../features/alerts/components/AlertStatusTag";
import { WorkflowPanel } from "../../features/alerts/components/WorkflowPanel";
import { alertDetailOptions } from "../../features/alerts/queryOptions";

const { Paragraph, Text, Title } = Typography;
const DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "medium",
  hour12: false,
});
const DESCRIPTION_COLUMNS = { xs: 1, sm: 1, md: 2, lg: 3 } as const;
const STATISTIC_TEXT_STYLE = { fontSize: 20 } as const;

function formatDate(value: string): string {
  return DATE_FORMATTER.format(new Date(value));
}

function metricValue(payload: Record<string, unknown>, key: string): number | string {
  const value = payload[key];
  return typeof value === "number" ? value : "—";
}

export function AlertDetailPage(): ReactElement {
  const { alertId = "" } = useParams();
  const alertQuery = useQuery({
    ...alertDetailOptions(alertId),
    enabled: Boolean(alertId),
  });

  if (alertQuery.isPending) {
    return (
      <Card className="content-card detail-skeleton" aria-label="告警详情加载中">
        <Skeleton active paragraph={{ rows: 8 }} />
      </Card>
    );
  }

  if (alertQuery.error instanceof ApiClientError && alertQuery.error.status === 404) {
    return (
      <Result
        status="404"
        title="告警不存在"
        subTitle="该告警可能已删除，或链接中的 ID 不正确。"
        extra={
          <Link to="/alerts">
            <Button type="primary">返回告警中心</Button>
          </Link>
        }
      />
    );
  }

  if (alertQuery.isError) {
    return (
      <Alert
        type="error"
        showIcon
        title="告警详情加载失败"
        description="请检查 API 服务和数据库连接后重试。"
        action={<Button onClick={() => void alertQuery.refetch()}>重新加载</Button>}
      />
    );
  }

  const alert = alertQuery.data;
  return (
    <section aria-labelledby="alert-detail-title">
      <Link to="/alerts" className="back-link">
        <ArrowLeftOutlined /> 返回告警中心
      </Link>

      <div className="page-heading detail-page-heading">
        <div>
          <Space wrap size={8} className="detail-tags">
            <AlertSeverityTag severity={alert.severity} />
            <AlertStatusTag status={alert.status} />
          </Space>
          <Title level={1} id="alert-detail-title">
            {alert.alert_name}
          </Title>
          <Paragraph className="monospace">{alert.external_alert_id}</Paragraph>
        </div>
      </div>

      <Row gutter={[16, 16]} className="metric-row">
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card">
            <Statistic title="当前值" value={metricValue(alert.payload, "value")} precision={2} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card">
            <Statistic title="告警阈值" value={metricValue(alert.payload, "threshold")} precision={2} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card">
            <Statistic title="服务" value={alert.service} valueStyle={STATISTIC_TEXT_STYLE} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card className="metric-card">
            <Statistic
              title="实例"
              value={alert.instance ?? "未指定"}
              valueStyle={STATISTIC_TEXT_STYLE}
            />
          </Card>
        </Col>
      </Row>

      <Card className="content-card detail-card" title="告警信息">
        <Descriptions column={DESCRIPTION_COLUMNS} colon={false}>
          <Descriptions.Item label="内部 ID">
            <Text copyable={{ text: alert.id }} className="monospace compact-id">
              {alert.id}
            </Text>
          </Descriptions.Item>
          <Descriptions.Item label="来源">{alert.source}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{formatDate(alert.started_at)}</Descriptions.Item>
          <Descriptions.Item label="接收时间">{formatDate(alert.created_at)}</Descriptions.Item>
          <Descriptions.Item label="更新时间">{formatDate(alert.updated_at)}</Descriptions.Item>
          <Descriptions.Item label="指纹">
            <Text copyable={{ text: alert.fingerprint ?? "" }} className="monospace compact-id">
              {alert.fingerprint ?? "—"}
            </Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            className="content-card detail-card"
            title={
              <Space>
                <DatabaseOutlined /> 原始 Payload
              </Space>
            }
          >
            <pre className="json-viewer">{JSON.stringify(alert.payload, null, 2)}</pre>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            className="content-card detail-card"
            title={
              <Space>
                <ClockCircleOutlined /> 诊断工作流
              </Space>
            }
          >
            <WorkflowPanel alertId={alert.id} />
          </Card>
        </Col>
      </Row>
    </section>
  );
}
