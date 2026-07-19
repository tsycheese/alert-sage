import { Button, Form, Input, Select, Space } from "antd";
import type { ReactElement } from "react";

import type { AlertSeverity, AlertStatus } from "../../../api/generated";
import type { AlertListParams } from "../../../api/http";
import {
  ALERT_SEVERITIES,
  ALERT_STATUSES,
  SEVERITY_LABELS,
  STATUS_LABELS,
} from "../constants";

interface FilterValues {
  status?: AlertStatus;
  severity?: AlertSeverity;
  service?: string;
}

interface AlertFiltersProps {
  values: AlertListParams;
  onApply: (values: FilterValues) => void;
  onReset: () => void;
}

const STATUS_OPTIONS = ALERT_STATUSES.map((value) => ({
  value,
  label: STATUS_LABELS[value],
}));
const SEVERITY_OPTIONS = ALERT_SEVERITIES.map((value) => ({
  value,
  label: SEVERITY_LABELS[value],
}));

export function AlertFilters({ values, onApply, onReset }: AlertFiltersProps): ReactElement {
  const [form] = Form.useForm<FilterValues>();

  function reset(): void {
    form.setFieldsValue({ status: undefined, severity: undefined, service: undefined });
    onReset();
  }

  return (
    <Form
      form={form}
      layout="inline"
      className="alert-filters"
      aria-label="告警筛选"
      initialValues={{
        status: values.status,
        severity: values.severity,
        service: values.service,
      }}
      onFinish={(formValues) =>
        onApply({
          ...formValues,
          service: formValues.service?.trim() || undefined,
        })
      }
    >
      <Form.Item name="status" label="状态">
        <Select
          allowClear
          placeholder="全部状态"
          options={STATUS_OPTIONS}
          className="filter-select"
        />
      </Form.Item>
      <Form.Item name="severity" label="等级">
        <Select
          allowClear
          placeholder="全部等级"
          options={SEVERITY_OPTIONS}
          className="filter-select"
        />
      </Form.Item>
      <Form.Item name="service" label="服务">
        <Input allowClear placeholder="例如 order-service" className="filter-service" />
      </Form.Item>
      <Form.Item className="filter-actions">
        <Space>
          <Button type="primary" htmlType="submit">
            筛选
          </Button>
          <Button onClick={reset}>重置</Button>
        </Space>
      </Form.Item>
    </Form>
  );
}
