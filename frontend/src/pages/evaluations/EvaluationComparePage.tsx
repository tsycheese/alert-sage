import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Alert, Result, Skeleton, Typography } from "antd";
import type { ReactElement } from "react";
import { Link, useSearchParams } from "react-router";

import { EvaluationComparison } from "../../features/evaluations/components/EvaluationComparison";
import { areRunsComparable } from "../../features/evaluations/model";
import { evaluationDetailOptions } from "../../features/evaluations/queryOptions";

const { Paragraph, Title } = Typography;

export function EvaluationComparePage(): ReactElement {
  const [searchParams] = useSearchParams();
  const leftId = searchParams.get("left") ?? "";
  const rightId = searchParams.get("right") ?? "";
  const left = useQuery({ ...evaluationDetailOptions(leftId), enabled: Boolean(leftId) });
  const right = useQuery({ ...evaluationDetailOptions(rightId), enabled: Boolean(rightId) });

  if (!leftId || !rightId) {
    return (
      <Result
        status="info"
        title="请选择两条评测运行"
        subTitle="对比需要 left 和 right 两个运行 ID。"
        extra={<Link to="/evaluations">返回评测列表选择</Link>}
      />
    );
  }
  if (left.isPending || right.isPending) {
    return <Skeleton active className="detail-skeleton" />;
  }
  if (left.isError || right.isError) {
    return (
      <Result
        status="error"
        title="对比运行加载失败"
        subTitle="请确认两个运行 ID 均存在并重试。"
        extra={<Link to="/evaluations">返回评测列表</Link>}
      />
    );
  }

  const compatible = areRunsComparable(left.data.run, right.data.run);

  return (
    <section aria-labelledby="evaluation-compare-title">
      <Link to="/evaluations" className="back-link">
        <ArrowLeftOutlined /> 返回评测列表
      </Link>
      <div className="page-heading detail-page-heading">
        <div>
          <Title level={1} id="evaluation-compare-title">
            评测运行对比
          </Title>
          <Paragraph>右侧相对左侧的变化；只比较相同问题集合。</Paragraph>
        </div>
      </div>
      {!compatible ? (
        <Alert
          type="warning"
          showIcon
          title="两条运行不可比较"
          description="运行必须都已完成，并具有相同的评测集 ID、版本、SHA 和 split。"
        />
      ) : (
        <EvaluationComparison left={left.data} right={right.data} />
      )}
    </section>
  );
}
