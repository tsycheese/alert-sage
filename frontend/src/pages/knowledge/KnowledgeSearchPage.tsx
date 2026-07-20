import { DatabaseOutlined, SearchOutlined } from "@ant-design/icons";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Card, Empty, Form, Input, List, Space, Tag, Typography } from "antd";
import type { ReactElement } from "react";

import type { KnowledgeSearchRequest } from "../../api/generated";
import { searchKnowledge } from "../../api/http";

const { Paragraph, Text, Title } = Typography;

export function KnowledgeSearchPage(): ReactElement {
  const search = useMutation({ mutationFn: searchKnowledge });

  function submit(values: KnowledgeSearchRequest): void {
    search.mutate({ query: values.query.trim(), top_k: 5 });
  }

  return (
    <section aria-labelledby="knowledge-page-title" className="knowledge-page">
      <div className="page-heading">
        <div>
          <Title level={1} id="knowledge-page-title">
            知识检索
          </Title>
          <Paragraph>检索已沉淀的处置案例，并保留命中文档和片段来源。</Paragraph>
        </div>
        {search.data ? <Tag color="blue">{search.data.provider.toUpperCase()}</Tag> : null}
      </div>

      <Card className="content-card knowledge-search-card">
        <Form<KnowledgeSearchRequest> layout="vertical" onFinish={submit}>
          <Form.Item
            name="query"
            label="检索问题"
            rules={[
              { required: true, whitespace: true, message: "请输入要检索的问题" },
              { max: 250, message: "问题不能超过 250 个字符" },
            ]}
          >
            <Input
              size="large"
              placeholder="例如：订单服务 CPU 持续升高应该如何排查？"
              maxLength={250}
              suffix={
                <Button
                  type="primary"
                  htmlType="submit"
                  icon={<SearchOutlined />}
                  loading={search.isPending}
                >
                  检索
                </Button>
              }
            />
          </Form.Item>
        </Form>
        <Text type="secondary">结果由后端知识适配器统一返回，Dify 凭据不会发送到浏览器。</Text>
      </Card>

      {search.isError ? (
        <Alert
          className="page-alert knowledge-result-state"
          type="error"
          showIcon
          title="知识检索暂时不可用"
          description="请检查 Dify 配置、网络连接或数据集索引状态后重试。"
        />
      ) : null}

      <Card className="content-card knowledge-results-card" title="检索结果">
        {!search.data ? (
          <Empty
            image={<DatabaseOutlined className="knowledge-empty-icon" />}
            description="输入问题后，这里会展示可追溯的知识片段"
          />
        ) : search.data.items.length === 0 ? (
          <Empty description="没有命中相关知识，请尝试调整关键词" />
        ) : (
          <List
            dataSource={search.data.items}
            renderItem={(item) => (
              <List.Item key={item.id} className="knowledge-result-item">
                <article aria-labelledby={`knowledge-result-${item.id}`}>
                  <Space wrap className="knowledge-result-meta">
                    <Text strong id={`knowledge-result-${item.id}`}>
                      {item.document_name}
                    </Text>
                    <Tag color="geekblue">相关度 {(item.score * 100).toFixed(1)}%</Tag>
                  </Space>
                  <Paragraph className="knowledge-result-content">{item.content}</Paragraph>
                  <Text type="secondary" className="knowledge-source" copyable>
                    {item.source}
                  </Text>
                </article>
              </List.Item>
            )}
          />
        )}
      </Card>
    </section>
  );
}
