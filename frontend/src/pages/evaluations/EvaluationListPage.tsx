import { ApartmentOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Space, Typography } from "antd";
import { useState } from "react";
import type { ReactElement } from "react";
import { useNavigate, useSearchParams } from "react-router";

import type { RagEvaluationRunStatus, RagEvaluationSplit } from "../../api/generated";
import { EvaluationFilters } from "../../features/evaluations/components/EvaluationFilters";
import { EvaluationRunTable } from "../../features/evaluations/components/EvaluationRunTable";
import { evaluationListOptions } from "../../features/evaluations/queryOptions";
import {
  DEFAULT_EVALUATION_PAGE_SIZE,
  readEvaluationSearchParams,
  writeEvaluationSearchParams,
} from "../../features/evaluations/searchParams";

const { Paragraph, Title } = Typography;

export function EvaluationListPage(): ReactElement {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const filters = readEvaluationSearchParams(searchParams);
  const evaluations = useQuery(evaluationListOptions(filters));

  function applyFilters(values: {
    split?: RagEvaluationSplit;
    status?: RagEvaluationRunStatus;
  }): void {
    setSelectedIds([]);
    setSearchParams(
      writeEvaluationSearchParams({
        ...values,
        page: 1,
        page_size: filters.page_size,
      }),
    );
  }

  function resetFilters(): void {
    setSelectedIds([]);
    setSearchParams(
      writeEvaluationSearchParams({ page: 1, page_size: DEFAULT_EVALUATION_PAGE_SIZE }),
    );
  }

  function changePage(page: number, pageSize: number): void {
    setSelectedIds([]);
    setSearchParams(
      writeEvaluationSearchParams({
        ...filters,
        page: pageSize === filters.page_size ? page : 1,
        page_size: pageSize,
      }),
    );
  }

  function compareSelected(): void {
    if (selectedIds.length !== 2) return;
    const search = new URLSearchParams({ left: selectedIds[0], right: selectedIds[1] });
    void navigate(`/evaluations/compare?${search.toString()}`);
  }

  return (
    <section aria-labelledby="evaluations-page-title">
      <div className="page-heading evaluation-page-heading">
        <div>
          <Title level={1} id="evaluations-page-title">
            RAG 评测
          </Title>
          <Paragraph>查看固定评测集的检索质量、拒答能力、错误率和延迟。</Paragraph>
        </div>
        <Button
          type="primary"
          size="large"
          icon={<ApartmentOutlined />}
          disabled={selectedIds.length !== 2}
          onClick={compareSelected}
        >
          对比已选运行{selectedIds.length ? `（${selectedIds.length}/2）` : ""}
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        className="page-alert"
        title="只读评测报告"
        description="只能选择同一评测集、同一 split 的两条已完成运行进行对比；test 结果不会在页面中触发重跑。"
      />

      <Card className="content-card filter-card">
        <EvaluationFilters
          key={searchParams.toString()}
          values={filters}
          onApply={applyFilters}
          onReset={resetFilters}
        />
      </Card>

      {evaluations.isError ? (
        <Alert
          type="error"
          showIcon
          className="page-alert"
          title="评测运行加载失败"
          description="请检查 API 和 PostgreSQL 状态后重试。"
          action={
            <Button size="small" onClick={() => void evaluations.refetch()}>
              重新加载
            </Button>
          }
        />
      ) : null}

      <Card className="content-card table-card">
        <Space className="table-heading" align="center">
          <div>
            <h2>评测运行</h2>
            <p>勾选两条兼容运行即可比较参数和逐题差异。</p>
          </div>
        </Space>
        <EvaluationRunTable
          items={evaluations.data?.items ?? []}
          total={evaluations.data?.total ?? 0}
          page={filters.page}
          pageSize={filters.page_size}
          loading={evaluations.isPending || evaluations.isFetching}
          selectedIds={selectedIds}
          onSelectionChange={setSelectedIds}
          onPageChange={changePage}
        />
      </Card>
    </section>
  );
}
