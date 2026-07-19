import { CheckCircleFilled, CloseCircleFilled, SyncOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Tag, Tooltip } from "antd";
import type { ReactElement } from "react";

import { getHealth } from "../api/http";

export function ApiHealthIndicator(): ReactElement {
  const health = useQuery({
    queryKey: ["system", "health"],
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: 60_000,
  });

  if (health.isPending) {
    return (
      <Tag icon={<SyncOutlined spin />} color="processing">
        API 检查中
      </Tag>
    );
  }

  if (health.isError) {
    return (
      <Tooltip title="无法连接后端服务">
        <Tag icon={<CloseCircleFilled />} color="error">
          API 离线
        </Tag>
      </Tooltip>
    );
  }

  return (
    <Tooltip title={`${health.data.service} · ${health.data.version}`}>
      <Tag icon={<CheckCircleFilled />} color="success">
        API 正常
      </Tag>
    </Tooltip>
  );
}
