import { Button, Result } from "antd";
import type { ReactElement } from "react";
import { Link } from "react-router";

export function NotFoundPage(): ReactElement {
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="你访问的地址不存在，或页面已经移动。"
      extra={
        <Link to="/alerts">
          <Button type="primary">返回告警中心</Button>
        </Link>
      }
    />
  );
}
