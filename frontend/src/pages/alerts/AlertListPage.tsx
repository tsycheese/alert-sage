import { PlusOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Space, Typography } from "antd";
import type { ReactElement } from "react";
import { Link, useSearchParams } from "react-router";

import { AlertFilters } from "../../features/alerts/components/AlertFilters";
import { AlertTable } from "../../features/alerts/components/AlertTable";
import { alertListOptions } from "../../features/alerts/queryOptions";
import {
  DEFAULT_PAGE_SIZE,
  readAlertSearchParams,
  writeAlertSearchParams,
} from "../../features/alerts/searchParams";

const { Paragraph, Title } = Typography;

export function AlertListPage(): ReactElement {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = readAlertSearchParams(searchParams);
  const alerts = useQuery(alertListOptions(filters));

  function applyFilters(values: {
    status?: typeof filters.status;
    severity?: typeof filters.severity;
    service?: string;
  }): void {
    setSearchParams(
      writeAlertSearchParams({
        ...values,
        page: 1,
        page_size: filters.page_size,
      }),
    );
  }

  function resetFilters(): void {
    setSearchParams(writeAlertSearchParams({ page: 1, page_size: DEFAULT_PAGE_SIZE }));
  }

  function changePage(page: number, pageSize: number): void {
    setSearchParams(
      writeAlertSearchParams({
        ...filters,
        page: pageSize === filters.page_size ? page : 1,
        page_size: pageSize,
      }),
    );
  }

  return (
    <section aria-labelledby="alerts-page-title">
      <div className="page-heading">
        <div>
          <Title level={1} id="alerts-page-title">
            告警中心
          </Title>
          <Paragraph>接收、筛选并查看进入 AI 诊断流程的运维告警。</Paragraph>
        </div>
        <Link to="/alerts/new">
          <Button type="primary" size="large" icon={<PlusOutlined />}>
            创建模拟告警
          </Button>
        </Link>
      </div>

      <Card className="content-card filter-card">
        <AlertFilters
          key={searchParams.toString()}
          values={filters}
          onApply={applyFilters}
          onReset={resetFilters}
        />
      </Card>

      {alerts.isError ? (
        <Alert
          type="error"
          showIcon
          title="告警列表加载失败"
          description="请检查 API 服务和数据库连接后重试。"
          action={
            <Button size="small" onClick={() => void alerts.refetch()}>
              重新加载
            </Button>
          }
          className="page-alert"
        />
      ) : null}

      <Card className="content-card table-card">
        <Space className="table-heading" align="center">
          <div>
            <h2>告警记录</h2>
            <p>页面状态来自 PostgreSQL 业务表。</p>
          </div>
        </Space>
        <AlertTable
          items={alerts.data?.items ?? []}
          total={alerts.data?.total ?? 0}
          page={filters.page}
          pageSize={filters.page_size}
          loading={alerts.isPending || alerts.isFetching}
          onPageChange={changePage}
        />
      </Card>
    </section>
  );
}
