# ADR 0009：结构化日志与跨异步边界关联

- 状态：已接受
- 日期：2026-07-22

## 背景

V2.4 需要从一次 HTTP 请求追踪到 Celery Worker、LangGraph 节点、上下文工具、DeepSeek、Dify 和案例同步。Prometheus 不能使用告警或运行 ID 作为标签，PostgreSQL 事件负责业务审计，但两者都不能替代用于即时排障的进程日志。日志方案还必须避免记录告警原文、Prompt、检索问题、模型响应、外部响应体和密钥。

## 方案比较

1. 标准库 `logging + contextvars`：无新增依赖，可覆盖异步上下文，需自行维护 JSON 格式、字段白名单和 Celery 传播。
2. `structlog`：上下文绑定和处理器生态更完善，但增加主要依赖，现阶段的字段和处理链较小，收益有限。
3. OpenTelemetry Trace：跨服务追踪能力最强，但需要 SDK、Exporter 和 Collector，超出当前模块化单体与求职演示版的运行成本。

## 决策

选择方案 1，并保留未来映射到 OpenTelemetry baggage/span attributes 的字段契约。

- API 为每个 HTTP 请求生成可信 UUID `request_id`，忽略调用方提供的 `X-Request-ID`，并在响应中返回 `X-Request-ID`。
- 调用方可通过 `X-Client-Request-ID` 提供自己的关联号；只有符合长度和字符白名单时才记录为 `client_request_id`，不能替代服务端请求 ID。
- API 投递 Celery 时通过自定义 headers 传播白名单关联字段；Worker 若收到缺失或非法请求 ID，会生成新的 UUID。
- Worker 从 PostgreSQL 补齐 `alert_id`、`workflow_run_id`、`thread_id` 和 `case_id`，使节点、工具、模型和知识适配器日志共享同一上下文。
- JSON 日志固定输出时间、级别、服务、环境、logger、事件名和受控上下文字段。仅使用静态事件名，不记录原始业务输入或供应商响应。
- Formatter 对 Bearer Token、常见密钥键值和 URL 密码做兜底脱敏；异常默认只记录类型和受控错误码，不输出可能含敏感数据的异常正文。
- `workflow_events.payload.correlation` 保存产生该业务事实时的请求关联号。幂等重放返回原事件，不用后来请求覆盖原始因果关系。
- 当前日志写入容器标准输出，不增加 Loki、Elasticsearch 或日志数据库。需要集中检索时再引入采集后端。
- Web 时间线继续以 PostgreSQL `workflow_events` 为事实来源。页面展示相邻事件间隔，明确不把它表述为精确节点耗时；节点耗时使用日志与 Prometheus Histogram。

## 影响

收益是无需新增基础设施即可按请求、告警、运行、线程和案例串联完整诊断链路，且关联 ID 不进入 Prometheus 高基数标签。代价是当前没有分布式 Trace 的父子 span、集中检索和日志保留策略；容器销毁后日志可能丢失。未来接入 OpenTelemetry 或 Loki 时应复用字段契约，PostgreSQL 业务事件仍保持事实来源地位。
