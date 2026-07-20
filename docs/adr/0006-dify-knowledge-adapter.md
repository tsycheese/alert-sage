# ADR 0006：Dify 作为可替换知识适配器

- 状态：Accepted
- 日期：2026-07-19

## 背景

V1.4 已在 PostgreSQL 中保存结构化案例，并通过独立 Celery 任务调用 `CasePublisher`。V2.1 需要把批准案例发布到 Dify Knowledge Base，同时让工作流和 Web 能检索这些案例。Dify 创建文档后异步索引，外部响应和可用性不能成为业务事实来源，也不能把供应商格式泄漏到领域层。

## 方案比较

1. 由 Dify Workflow 编排告警流程：接入快，但会复制 LangGraph 状态机、人工中断和恢复语义，核心状态边界不清晰。
2. 领域代码直接调用 Dify SDK/API：文件少，但供应商响应、鉴权和错误会扩散到工作流、任务和路由。
3. 用统一协议封装 Dify：增加少量映射代码，但 Mock、Dify 和未来 pgvector 能共享调用方，便于合约测试和替换。

## 决策

选择方案 3。Dify 只负责知识文档管理、索引和检索，不编排告警工作流。

- `DifyKnowledgeAdapter` 同时实现 `CasePublisher` 与 `KnowledgeRetriever`。
- API Key 由 `SecretStr` 环境配置注入，只在后端工厂中解包。
- 案例以 `alert-sage-case-{case_id}.md` 作为稳定名称；任务重放时先精确查询，完成索引的文档直接复用，失败文档更新后重新索引。
- 创建或更新后轮询文档状态；外层任务超时负责限制总等待时间。
- 网络错误、限流和服务端错误做有限重试；鉴权、异常响应和索引失败转换为脱敏领域异常。
- 检索结果转换为统一 `DocumentChunk`，来源使用 `dify://datasets/{dataset}/documents/{document}/segments/{segment}`。
- Mock 是默认 provider，保证离线开发与测试不依赖云端。

## 影响

收益是核心流程不依赖 Dify 响应格式，案例业务事实和外部索引状态继续分离，并可用同一接口增加 pgvector 对照实现。代价是需要维护 Dify HTTP 合约测试、轮询和错误映射。当前“事务提交后投递 Celery”的崩溃窗口仍存在，后续使用事务性 Outbox 解决；本 ADR 不扩大到该问题。

空数据集不要求自定义元数据。若后续评测证明需要按服务、严重级别或时间过滤，再新增元数据 Schema、存量文档回填和兼容策略。
