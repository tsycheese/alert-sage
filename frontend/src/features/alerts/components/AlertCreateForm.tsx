import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Col, Form, Input, InputNumber, Row, Select, Space } from "antd";
import type { FormInstance } from "antd";
import type { ReactElement } from "react";

import { ALERT_SEVERITIES, SEVERITY_LABELS } from "../constants";
import type { AlertCreateFormValues } from "../formValues";
import { createDemoFormValues } from "../formValues";

const SEVERITY_OPTIONS = ALERT_SEVERITIES.map((value) => ({
  value,
  label: SEVERITY_LABELS[value],
}));

interface AlertCreateFormProps {
  form: FormInstance<AlertCreateFormValues>;
  submitting: boolean;
  onSubmit: (values: AlertCreateFormValues) => void;
  onChange: () => void;
}

export function AlertCreateForm({
  form,
  submitting,
  onSubmit,
  onChange,
}: AlertCreateFormProps): ReactElement {
  function loadDemo(): void {
    form.setFieldsValue(createDemoFormValues());
    onChange();
  }

  return (
    <Form<AlertCreateFormValues>
      form={form}
      layout="vertical"
      requiredMark="optional"
      initialValues={createDemoFormValues()}
      onFinish={onSubmit}
      onValuesChange={onChange}
      aria-label="创建模拟告警"
    >
      <div className="form-section-heading">
        <div>
          <h2>告警标识</h2>
          <p>外部告警 ID 与来源共同构成幂等键。</p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadDemo}>
          重新生成演示数据
        </Button>
      </div>

      <Row gutter={20}>
        <Col xs={24} md={8}>
          <Form.Item label="来源" name="source" rules={[{ required: true }]}>
            <Input disabled />
          </Form.Item>
        </Col>
        <Col xs={24} md={16}>
          <Form.Item
            label="外部告警 ID"
            name="external_alert_id"
            rules={[
              { required: true, message: "请输入外部告警 ID" },
              { max: 128, message: "不能超过 128 个字符" },
            ]}
          >
            <Input className="monospace" autoComplete="off" />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            label="告警名称"
            name="alert_name"
            rules={[{ required: true, message: "请输入告警名称" }]}
          >
            <Input />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item
            label="告警等级"
            name="severity"
            rules={[{ required: true, message: "请选择告警等级" }]}
          >
            <Select options={SEVERITY_OPTIONS} />
          </Form.Item>
        </Col>
      </Row>

      <div className="form-section-heading compact">
        <div>
          <h2>资源与指标</h2>
          <p>第一版以服务实例 CPU 使用率过高为演示场景。</p>
        </div>
      </div>

      <Row gutter={20}>
        <Col xs={24} md={12}>
          <Form.Item
            label="服务"
            name="service"
            rules={[{ required: true, message: "请输入服务名称" }]}
          >
            <Input />
          </Form.Item>
        </Col>
        <Col xs={24} md={12}>
          <Form.Item label="实例" name="instance">
            <Input />
          </Form.Item>
        </Col>
        <Col xs={24} md={8}>
          <Form.Item
            label="当前值"
            name="value"
            rules={[{ required: true, message: "请输入当前值" }]}
          >
            <InputNumber className="full-width" precision={2} />
          </Form.Item>
        </Col>
        <Col xs={24} md={8}>
          <Form.Item
            label="阈值"
            name="threshold"
            rules={[{ required: true, message: "请输入阈值" }]}
          >
            <InputNumber className="full-width" precision={2} />
          </Form.Item>
        </Col>
        <Col xs={24} md={8}>
          <Form.Item
            label="开始时间"
            name="started_at_local"
            rules={[{ required: true, message: "请选择开始时间" }]}
          >
            <Input type="datetime-local" />
          </Form.Item>
        </Col>
        <Col span={24}>
          <Form.Item label="告警摘要" name="payload_summary">
            <Input.TextArea rows={3} maxLength={500} showCount />
          </Form.Item>
        </Col>
      </Row>

      <Alert
        type="info"
        showIcon
        title="创建只会持久化告警"
        description="诊断工作流将在 V1.3 接入；当前不会自动执行重启、扩缩容或服务器命令。"
        className="form-scope-alert"
      />

      <Space className="form-actions">
        <Button type="primary" htmlType="submit" loading={submitting} size="large">
          创建并查看详情
        </Button>
      </Space>
    </Form>
  );
}
