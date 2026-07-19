import { Empty, Table, Typography } from "antd";
import type { TableColumnsType, TablePaginationConfig } from "antd";
import type { ReactElement } from "react";
import { Link } from "react-router";

import type { AlertResponse } from "../../../api/generated";
import { AlertSeverityTag } from "./AlertSeverityTag";
import { AlertStatusTag } from "./AlertStatusTag";

const { Text } = Typography;
const DATE_FORMATTER = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function formatDate(value: string): string {
  return DATE_FORMATTER.format(new Date(value));
}

function formatMetric(alert: AlertResponse): string {
  const value = alert.payload.value;
  const threshold = alert.payload.threshold;
  if (typeof value !== "number" || typeof threshold !== "number") {
    return "—";
  }
  return `${value} / ${threshold}`;
}

const COLUMNS: TableColumnsType<AlertResponse> = [
  {
    title: "等级",
    dataIndex: "severity",
    width: 90,
    render: (_, alert) => <AlertSeverityTag severity={alert.severity} />,
  },
  {
    title: "告警",
    dataIndex: "alert_name",
    width: 230,
    render: (_, alert) => (
      <div className="table-primary-cell">
        <Link to={`/alerts/${alert.id}`} className="alert-name-link">
          {alert.alert_name}
        </Link>
        <Text type="secondary" className="monospace table-secondary-text">
          {alert.external_alert_id}
        </Text>
      </div>
    ),
  },
  {
    title: "服务 / 实例",
    dataIndex: "service",
    width: 220,
    responsive: ["md"],
    render: (_, alert) => (
      <div className="table-primary-cell">
        <Text>{alert.service}</Text>
        <Text type="secondary" className="table-secondary-text">
          {alert.instance ?? "未指定实例"}
        </Text>
      </div>
    ),
  },
  {
    title: "当前值 / 阈值",
    key: "metric",
    width: 150,
    responsive: ["lg"],
    render: (_, alert) => <Text className="monospace">{formatMetric(alert)}</Text>,
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 120,
    render: (_, alert) => <AlertStatusTag status={alert.status} />,
  },
  {
    title: "开始时间",
    dataIndex: "started_at",
    width: 180,
    responsive: ["lg"],
    render: (value: string) => formatDate(value),
  },
  {
    title: "操作",
    key: "actions",
    width: 90,
    responsive: ["md"],
    render: (_, alert) => <Link to={`/alerts/${alert.id}`}>查看详情</Link>,
  },
];
const TABLE_SCROLL = { x: 720 } as const;

interface AlertTableProps {
  items: AlertResponse[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  onPageChange: (page: number, pageSize: number) => void;
}

export function AlertTable({
  items,
  total,
  page,
  pageSize,
  loading,
  onPageChange,
}: AlertTableProps): ReactElement {
  const pagination: TablePaginationConfig = {
    current: page,
    pageSize,
    total,
    showSizeChanger: true,
    pageSizeOptions: [10, 20, 50],
    showTotal: (count) => `共 ${count} 条告警`,
    onChange: onPageChange,
  };
  const locale = {
    emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无符合条件的告警" />,
  };

  return (
    <Table<AlertResponse>
      rowKey="id"
      columns={COLUMNS}
      dataSource={items}
      loading={loading}
      pagination={pagination}
      locale={locale}
      scroll={TABLE_SCROLL}
    />
  );
}
