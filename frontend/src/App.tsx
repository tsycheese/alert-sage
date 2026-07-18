import {
  AlertOutlined,
  CheckCircleFilled,
  DatabaseOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Card, Col, Layout, Row, Space, Spin, Tag, Typography } from "antd";
import type { ReactElement } from "react";

import { getHealth } from "./api/http";

const { Header, Content } = Layout;
const { Paragraph, Text, Title } = Typography;

export function App(): ReactElement {
  const health = useQuery({
    queryKey: ["system", "health"],
    queryFn: ({ signal }) => getHealth(signal),
  });

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <Space size="middle">
          <div className="brand-mark">
            <RobotOutlined />
          </div>
          <div>
            <Text className="brand-name">Alert Sage</Text>
            <Text className="brand-subtitle">AI 告警诊断助手</Text>
          </div>
        </Space>
      </Header>

      <Content className="app-content">
        <section className="hero">
          <Tag color="blue">V0 工程骨架</Tag>
          <Title>让每一条告警都有可追溯的诊断过程</Title>
          <Paragraph>
            当前阶段已建立前后端连通、数据库迁移和健康检查。下一阶段将实现告警接入与
            LangGraph 工作流。
          </Paragraph>
        </section>

        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <Card className="status-card" title="API 状态">
              {health.isPending ? (
                <Space>
                  <Spin size="small" />正在检查服务
                </Space>
              ) : health.isError ? (
                <Tag color="error">API 暂不可用</Tag>
              ) : (
                <Space orientation="vertical">
                  <Tag icon={<CheckCircleFilled />} color="success">
                    API 服务正常
                  </Tag>
                  <Text type="secondary">
                    {health.data.service} · {health.data.version}
                  </Text>
                </Space>
              )}
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card className="status-card" title="告警工作流">
              <Space orientation="vertical">
                <AlertOutlined className="feature-icon" />
                <Text>等待 V1 接入 LangGraph</Text>
              </Space>
            </Card>
          </Col>
          <Col xs={24} md={8}>
            <Card className="status-card" title="知识检索">
              <Space orientation="vertical">
                <DatabaseOutlined className="feature-icon" />
                <Text>等待 V2 接入 Dify</Text>
              </Space>
            </Card>
          </Col>
        </Row>
      </Content>
    </Layout>
  );
}
