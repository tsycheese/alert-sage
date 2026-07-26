import { AlertOutlined, BookOutlined, ExperimentOutlined, RobotOutlined } from "@ant-design/icons";
import { Layout, Space, Typography } from "antd";
import type { ReactElement } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { ApiHealthIndicator } from "./ApiHealthIndicator";

const { Content, Header } = Layout;
const { Text } = Typography;

export function AppLayout(): ReactElement {
  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <Link to="/alerts" className="brand-link" aria-label="Alert Sage 告警中心">
          <span className="brand-mark" aria-hidden="true">
            <RobotOutlined />
          </span>
          <span>
            <Text className="brand-name">Alert Sage</Text>
            <Text className="brand-subtitle">AI 告警诊断助手</Text>
          </span>
        </Link>

        <nav className="main-nav" aria-label="主导航">
          <NavLink
            to="/alerts"
            className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}
          >
            <Space size={8}>
              <AlertOutlined />
              告警中心
            </Space>
          </NavLink>
          <NavLink
            to="/knowledge"
            className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}
          >
            <Space size={8}>
              <BookOutlined />
              知识检索
            </Space>
          </NavLink>
          <NavLink
            to="/evaluations"
            className={({ isActive }) => `nav-link${isActive ? " nav-link-active" : ""}`}
          >
            <Space size={8}>
              <ExperimentOutlined />
              RAG 评测
            </Space>
          </NavLink>
        </nav>

        <div className="header-status">
          <ApiHealthIndicator />
        </div>
      </Header>

      <Content className="app-content">
        <Outlet />
      </Content>
    </Layout>
  );
}
