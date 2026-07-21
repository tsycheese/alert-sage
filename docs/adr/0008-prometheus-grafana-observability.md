# ADR 0008：API 与 Worker 分离暴露 Prometheus 指标

- 状态：已接受
- 日期：2026-07-20

## 背景

V2.3 需要让告警 API、LangGraph、上下文工具、DeepSeek、Dify 和案例同步的吞吐、延迟与失败可观察。API 是单进程 Uvicorn，Celery Worker 使用 `prefork` 并发；若只在 API 暴露默认 Python Registry，Worker 子进程产生的指标不可见。指标系统不得成为业务状态来源，也不得因监控组件故障阻塞诊断链路。

## 方案比较

1. API 与 Worker 各自暴露 Pull 端点：符合 Prometheus 抓取模型，供应商状态少，但 Worker 需要处理 Python 多进程聚合。
2. Worker 推送到 Pushgateway：任务代码较直接，但增加有状态组件和过期序列清理问题，更适合短生命周期批处理任务。
3. 从 PostgreSQL 业务事件即时聚合指标：不增加 Worker exporter，但每次抓取需要查询业务库，Counter/Histogram 语义不自然，并把监控负载带入事实库。

## 决策

选择方案 1。API 在 `/metrics` 暴露自己的 Registry；Worker 在独立端口暴露 multiprocess Registry。Prometheus 分别抓取两个 target，Grafana 使用文件化 provisioning 加载数据源和 Dashboard。

- Worker 启动前清理专用 `PROMETHEUS_MULTIPROC_DIR`，环境变量在 Celery fork 前由 Compose 注入。
- V2.3 自定义指标只使用 Counter 和 Histogram，避免 multiprocess 模式对 Gauge、Info、自定义 Collector 和 exemplar 的限制。
- 监控只记录已经发生的调用，不吞掉或改写业务异常。
- 标签只允许固定集合：HTTP 方法/路由模板/状态码、工作流操作/状态、节点、工具、供应商、模型和固定结果状态。
- 禁止使用 `alert_id`、`workflow_run_id`、实例、任意服务名、查询文本、异常消息或文档 ID 作为标签；这些信息留在结构化日志和 PostgreSQL 审计事件中。
- Prometheus 和 Grafana 数据可删除、可重建，不作为告警、工作流、报告或案例的事实来源。

## 影响

收益是 API 与异步诊断链路可分别定位，Dashboard 可版本化并适合演示；代价是 Worker 启动脚本和 multiprocess 生命周期需要专项测试。若未来迁移到 Kubernetes 或 OpenTelemetry Collector，可保留指标契约并替换暴露方式。

官方参考：

- <https://prometheus.github.io/client_python/multiprocess/>
- <https://prometheus.github.io/client_python/exporting/http/asgi/>
- <https://prometheus.io/docs/prometheus/latest/configuration/configuration/>
- <https://grafana.com/docs/grafana/latest/administration/provisioning/>
