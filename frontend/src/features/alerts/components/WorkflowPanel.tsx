import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Descriptions, Empty, Input, Space, Spin, Tag, Timeline, Typography } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";

import type { HumanDecisionAction, WorkflowRunStatus } from "../../../api/generated";
import {
  ApiClientError,
  retryWorkflow,
  startWorkflow,
  submitWorkflowDecision,
  workflowStreamUrl,
} from "../../../api/http";
import {
  alertKeys,
  alertWorkflowEventsOptions,
  alertWorkflowOptions,
} from "../queryOptions";

const { Paragraph, Text, Title } = Typography;
const TERMINAL_STATUSES = new Set<WorkflowRunStatus>(["completed", "rejected"]);
const RUN_STATUS_LABELS: Record<WorkflowRunStatus, string> = {
  queued: "等待调度",
  running: "诊断中",
  waiting_for_approval: "等待人工确认",
  reanalyzing: "重新分析中",
  completed: "已完成",
  rejected: "已拒绝",
  failed: "执行失败",
};

interface WorkflowPanelProps {
  alertId: string;
}

export function WorkflowPanel({ alertId }: WorkflowPanelProps): ReactElement {
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const pendingDecision = useRef<{ action: HumanDecisionAction; key: string } | null>(null);
  const workflowQuery = useQuery({
    ...alertWorkflowOptions(alertId),
    enabled: Boolean(alertId),
  });
  const eventsQuery = useQuery({
    ...alertWorkflowEventsOptions(alertId),
    enabled: Boolean(workflowQuery.data),
  });

  const refreshWorkflow = useCallback(async (): Promise<void> => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: alertKeys.detail(alertId) }),
      queryClient.invalidateQueries({ queryKey: alertKeys.workflow(alertId) }),
      queryClient.invalidateQueries({ queryKey: alertKeys.workflowEvents(alertId) }),
    ]);
  }, [alertId, queryClient]);

  useEffect(() => {
    const status = workflowQuery.data?.run.status;
    if (!status || TERMINAL_STATUSES.has(status)) return undefined;
    const source = new EventSource(workflowStreamUrl(alertId));
    source.onmessage = () => void refreshWorkflow();
    const eventNames = [
      "workflow_queued",
      "workflow_started",
      "node_completed",
      "human_input_required",
      "human_decision_received",
      "workflow_completed",
      "workflow_rejected",
      "workflow_failed",
    ];
    const onWorkflowEvent = (): void => {
      void refreshWorkflow();
    };
    eventNames.forEach((name) => source.addEventListener(name, onWorkflowEvent));
    return () => {
      eventNames.forEach((name) => source.removeEventListener(name, onWorkflowEvent));
      source.close();
    };
  }, [alertId, refreshWorkflow, workflowQuery.data?.run.status]);

  const startMutation = useMutation({
    mutationFn: () =>
      startWorkflow(alertId, { idempotency_key: `web-start:${alertId}` }),
    onSuccess: refreshWorkflow,
  });
  const retryMutation = useMutation({
    mutationFn: () => retryWorkflow(alertId),
    onSuccess: refreshWorkflow,
  });
  const decisionMutation = useMutation({
    mutationFn: (action: HumanDecisionAction) => {
      if (pendingDecision.current?.action !== action) {
        pendingDecision.current = {
          action,
          key: `web-decision:${crypto.randomUUID()}`,
        };
      }
      return submitWorkflowDecision(alertId, {
        idempotency_key: pendingDecision.current.key,
        action,
        actor: "demo-user",
        comment: comment.trim() || null,
      });
    },
    onSuccess: async () => {
      pendingDecision.current = null;
      setComment("");
      await refreshWorkflow();
    },
    onError: (error) => {
      if (!(error instanceof ApiClientError) || error.status !== 503) {
        pendingDecision.current = null;
      }
    },
  });

  if (workflowQuery.isPending) {
    return <Spin description="加载工作流" />;
  }

  if (workflowQuery.error instanceof ApiClientError && workflowQuery.error.status === 404) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div>
            <Text strong>诊断尚未启动</Text>
            <Paragraph type="secondary">启动后由 Celery Worker 异步执行，并在此等待人工确认。</Paragraph>
          </div>
        }
      >
        <Button type="primary" loading={startMutation.isPending} onClick={() => startMutation.mutate()}>
          启动诊断
        </Button>
        {startMutation.isError ? <Alert type="error" title="任务投递失败，请重试" showIcon /> : null}
      </Empty>
    );
  }

  if (workflowQuery.isError || !workflowQuery.data) {
    return (
      <Alert
        type="error"
        showIcon
        title="工作流加载失败"
        action={<Button onClick={() => void workflowQuery.refetch()}>重试</Button>}
      />
    );
  }

  const { run, report } = workflowQuery.data;
  const waiting = run.status === "waiting_for_approval";
  const busy = ["queued", "running", "reanalyzing"].includes(run.status);
  return (
    <Space orientation="vertical" size={16} className="workflow-panel">
      <Space wrap>
        <Tag color={run.status === "failed" ? "error" : waiting ? "warning" : "processing"}>
          {RUN_STATUS_LABELS[run.status]}
        </Tag>
        {busy ? <SyncOutlined spin aria-label="工作流执行中" /> : null}
        <Text type="secondary">第 {run.attempt} 次执行</Text>
      </Space>

      {run.status === "failed" ? (
        <Alert
          type="error"
          showIcon
          title={run.error_code ?? "工作流执行失败"}
          description={run.error_message}
          action={
            <Button
              icon={<ReloadOutlined />}
              loading={retryMutation.isPending}
              onClick={() => retryMutation.mutate()}
            >
              重试
            </Button>
          }
        />
      ) : null}

      {report ? (
        <section aria-labelledby="diagnosis-report-title">
          <Title level={4} id="diagnosis-report-title">诊断报告 v{report.version}</Title>
          <Paragraph>{report.summary}</Paragraph>
          <Descriptions size="small" column={2}>
            <Descriptions.Item label="置信度">
              {(Number(report.confidence) * 100).toFixed(0)}%
            </Descriptions.Item>
            <Descriptions.Item label="模型">{report.model_name}</Descriptions.Item>
          </Descriptions>
          <Text strong>建议操作</Text>
          <ul className="recommendation-list">
            {report.recommendations.map((item) => (
              <li key={String(item.title)}>{String(item.title)}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {waiting ? (
        <section className="human-review" aria-labelledby="human-review-title">
          <Title level={4} id="human-review-title">人工确认</Title>
          <Input.TextArea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="重新分析时必须填写反馈；批准或拒绝时可选"
            rows={3}
          />
          <Space wrap>
            <Button
              type="primary"
              icon={<CheckCircleOutlined />}
              loading={decisionMutation.isPending}
              onClick={() => decisionMutation.mutate("approve")}
            >
              批准建议
            </Button>
            <Button
              danger
              icon={<CloseCircleOutlined />}
              loading={decisionMutation.isPending}
              onClick={() => decisionMutation.mutate("reject")}
            >
              拒绝建议
            </Button>
            <Button
              icon={<ReloadOutlined />}
              disabled={!comment.trim()}
              loading={decisionMutation.isPending}
              onClick={() => decisionMutation.mutate("reanalyze")}
            >
              重新分析
            </Button>
          </Space>
          {decisionMutation.isError ? <Alert type="error" showIcon title="提交决策失败" /> : null}
        </section>
      ) : null}

      {eventsQuery.data?.items.length ? (
        <Timeline
          items={eventsQuery.data.items.map((event) => ({
            key: event.id,
            children: `${event.sequence}. ${event.event_type}${event.node_name ? ` · ${event.node_name}` : ""}`,
          }))}
        />
      ) : null}
    </Space>
  );
}
