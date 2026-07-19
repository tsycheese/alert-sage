import { ArrowLeftOutlined } from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, App as AntdApp, Button, Card, Form, Typography } from "antd";
import type { ReactElement } from "react";
import { Link, useNavigate } from "react-router";

import { ApiClientError, createAlert } from "../../api/http";
import { AlertCreateForm } from "../../features/alerts/components/AlertCreateForm";
import type { AlertCreateFormValues } from "../../features/alerts/formValues";
import { toAlertCreate } from "../../features/alerts/formValues";
import { alertKeys } from "../../features/alerts/queryOptions";

const { Paragraph, Title } = Typography;

interface ValidationIssue {
  field: string;
  message: string;
}

function validationIssues(error: unknown): ValidationIssue[] {
  if (!(error instanceof ApiClientError) || error.status !== 422) return [];
  const issues = error.body?.error.context?.issues;
  if (!Array.isArray(issues)) return [];
  return issues.filter(
    (issue): issue is ValidationIssue =>
      typeof issue === "object" &&
      issue !== null &&
      "field" in issue &&
      typeof issue.field === "string" &&
      "message" in issue &&
      typeof issue.message === "string",
  );
}

function conflictAlertId(error: unknown): string | null {
  if (!(error instanceof ApiClientError) || error.status !== 409) return null;
  const alertId = error.body?.error.context?.alert_id;
  return typeof alertId === "string" ? alertId : null;
}

export function AlertCreatePage(): ReactElement {
  const [form] = Form.useForm<AlertCreateFormValues>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { message } = AntdApp.useApp();

  const creation = useMutation({
    mutationFn: createAlert,
    onSuccess: async ({ alert, replayed }) => {
      queryClient.setQueryData(alertKeys.detail(alert.id), alert);
      await queryClient.invalidateQueries({ queryKey: alertKeys.lists() });
      void message.success(replayed ? "已返回原有告警" : "告警创建成功");
      navigate(`/alerts/${alert.id}`);
    },
    onError: (error) => {
      const fieldMap: Record<string, keyof AlertCreateFormValues> = {
        started_at: "started_at_local",
      };
      const fields = validationIssues(error).map((issue) => ({
        name: fieldMap[issue.field] ?? (issue.field as keyof AlertCreateFormValues),
        errors: [issue.message],
      }));
      if (fields.length > 0) form.setFields(fields);
    },
  });

  const existingAlertId = conflictAlertId(creation.error);
  const genericError =
    creation.isError && !(creation.error instanceof ApiClientError && [409, 422].includes(creation.error.status));

  return (
    <section aria-labelledby="create-alert-title" className="narrow-page">
      <Link to="/alerts" className="back-link">
        <ArrowLeftOutlined /> 返回告警中心
      </Link>
      <div className="page-heading create-page-heading">
        <div>
          <Title level={1} id="create-alert-title">
            创建模拟告警
          </Title>
          <Paragraph>提交一条 CPU 使用率过高告警，用于验证后续诊断闭环。</Paragraph>
        </div>
      </div>

      {existingAlertId ? (
        <Alert
          type="warning"
          showIcon
          title="外部告警 ID 已被不同内容使用"
          description="系统未覆盖原记录。你可以查看已有告警，或重新生成一个外部告警 ID。"
          action={
            <Button onClick={() => navigate(`/alerts/${existingAlertId}`)}>查看已有告警</Button>
          }
          className="page-alert"
        />
      ) : null}

      {genericError ? (
        <Alert
          type="error"
          showIcon
          title="告警创建失败"
          description="API 暂时不可用，请检查服务状态后重试。"
          className="page-alert"
        />
      ) : null}

      <Card className="content-card form-card">
        <AlertCreateForm
          form={form}
          submitting={creation.isPending}
          onSubmit={(values) => creation.mutate(toAlertCreate(values))}
          onChange={() => creation.reset()}
        />
      </Card>
    </section>
  );
}
