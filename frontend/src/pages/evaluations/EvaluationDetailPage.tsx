import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Descriptions, Result, Skeleton, Space, Typography } from "antd";
import type { DescriptionsProps } from "antd";
import type { ReactElement } from "react";
import { Link, useParams } from "react-router";

import { ApiClientError } from "../../api/http";
import { EvaluationMetricGrid } from "../../features/evaluations/components/EvaluationMetricGrid";
import { EvaluationResultTable } from "../../features/evaluations/components/EvaluationResultTable";
import { EvaluationSplitTag } from "../../features/evaluations/components/EvaluationSplitTag";
import { EvaluationStatusTag } from "../../features/evaluations/components/EvaluationStatusTag";
import {
  formatDateTime,
  formatThreshold,
  readEvaluationMetrics,
} from "../../features/evaluations/model";
import { evaluationDetailOptions } from "../../features/evaluations/queryOptions";

const { Paragraph, Text, Title } = Typography;

export function EvaluationDetailPage(): ReactElement {
  const { runId = "" } = useParams();
  const detail = useQuery({ ...evaluationDetailOptions(runId), enabled: Boolean(runId) });

  if (detail.isPending) {
    return <Skeleton active className="detail-skeleton" />;
  }
  if (detail.isError) {
    const missing = detail.error instanceof ApiClientError && detail.error.status === 404;
    return (
      <Result
        status={missing ? "404" : "error"}
        title={missing ? "评测运行不存在" : "评测详情加载失败"}
        subTitle={missing ? "该运行可能已被删除或地址有误。" : "请检查 API 服务后重试。"}
        extra={<Link to="/evaluations">返回评测列表</Link>}
      />
    );
  }

  const { run, results } = detail.data;
  const metrics = readEvaluationMetrics(run);
  const descriptionItems: DescriptionsProps["items"] = [
    { key: "set", label: "评测集", children: `${run.evaluation_set_id} ${run.evaluation_set_version}` },
    { key: "sha", label: "文件 SHA", children: <Text className="monospace compact-id">{run.evaluation_set_sha256}</Text> },
    { key: "provider", label: "供应商", children: run.provider },
    { key: "top-k", label: "TopK", children: run.top_k },
    { key: "threshold", label: "分数阈值", children: formatThreshold(run.score_threshold) },
    { key: "attempt", label: "执行次数", children: run.attempt },
    { key: "progress", label: "问题进度", children: `${run.completed_query_count}/${run.query_count}` },
    { key: "build", label: "构建 Revision", children: run.build_revision ?? "未记录" },
    { key: "started", label: "开始时间", children: formatDateTime(run.started_at) },
    { key: "finished", label: "完成时间", children: formatDateTime(run.finished_at) },
    { key: "run", label: "运行 ID", children: <Text className="monospace compact-id">{run.id}</Text> },
    { key: "dataset", label: "Dataset ID", children: <Text className="monospace compact-id">{run.dataset_id ?? "—"}</Text> },
  ];

  return (
    <section aria-labelledby="evaluation-detail-title">
      <Link to="/evaluations" className="back-link">
        <ArrowLeftOutlined /> 返回评测列表
      </Link>
      <div className="page-heading detail-page-heading">
        <div>
          <Space size={[6, 6]} wrap className="detail-tags">
            <EvaluationSplitTag split={run.split} />
            <EvaluationStatusTag status={run.status} />
          </Space>
          <Title level={1} id="evaluation-detail-title">
            评测运行详情
          </Title>
          <Paragraph className="monospace">{run.id}</Paragraph>
        </div>
      </div>

      {run.status === "failed" ? (
        <Alert
          type="error"
          showIcon
          className="page-alert"
          title="评测运行失败"
          description={`${run.error_code ?? "unknown"}：${run.error_message ?? "未记录错误说明"}`}
        />
      ) : null}
      {run.status === "queued" || run.status === "running" ? (
        <Alert
          type="info"
          showIcon
          className="page-alert"
          title="评测仍在执行"
          description="页面每两秒刷新状态；逐题结果在完整批次提交后显示。"
        />
      ) : null}

      {metrics ? <EvaluationMetricGrid metrics={metrics} topK={run.top_k} /> : null}

      <Card title="运行参数与审计信息" className="content-card detail-card">
        <Descriptions items={descriptionItems} bordered column={{ xs: 1, sm: 2, lg: 3 }} />
      </Card>

      <Card className="content-card table-card">
        <div className="table-heading">
          <h2>逐题诊断</h2>
          <p>排名指标按案例 UUID 去重；错误和拒答分开统计。</p>
        </div>
        <EvaluationResultTable results={results} />
      </Card>
    </section>
  );
}
