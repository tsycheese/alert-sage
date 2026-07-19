# ADR 0003：单进程 LangGraph 与 PostgreSQL Checkpointer

- 状态：已接受
- 日期：2026-07-19

## 背景

V1.3B 需要证明告警诊断能够在人工确认处暂停，并在进程重启后继续。若同时引入 Celery、SSE、真实模型和 Dify，故障面会跨越图编排、队列、网络与供应商协议，难以独立验证恢复语义。

## 备选方案

1. 使用内存 Checkpointer：实现简单，但进程退出后状态丢失，无法满足恢复目标。
2. 一次接入 LangGraph、Celery、SSE 和真实外部服务：演示链路更完整，但集成风险与定位成本高。
3. 先实现单进程 LangGraph、官方 PostgreSQL Checkpointer 和确定性模拟适配器，再在下一增量接入异步调度与 API。

## 决策

选择方案 3：

- 使用 `langgraph>=1.2,<2` 和 `langgraph-checkpoint-postgres>=3.1,<4`，实际版本由 `uv.lock` 固定。
- 图包含 `parse_alert`、`classify_alert`、`collect_context`、`diagnose`、`recommend`、`human_review` 和 `finalize` 七个节点。
- `human_review` 使用动态 `interrupt()`；调用方必须使用同一 `thread_id` 和 `Command(resume=...)` 恢复。
- 指标、日志、CMDB 和知识工具通过协议注入并并发执行。默认超时一秒、最多两次尝试；部分失败保留可用证据。
- 诊断模型通过适配器注入。诊断草稿、建议和最终报告均经过严格 Pydantic Schema 校验。
- 节点本身不写业务数据库。应用服务消费节点更新后，以独立事务幂等写入事件、工具执行和报告；人工决策先写业务表，再恢复图。

## Checkpoint 生命周期与安全

- `workflow_runs.thread_id` 是 checkpoint 的稳定游标；`workflow_run_id` 是业务主键，两者不可互换。
- 官方 Checkpointer 的 `setup()` 管理 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations`，项目 Alembic 显式忽略这些供应商表。
- Checkpointer 使用严格序列化配置，不允许从 checkpoint 数据任意导入 JSON 或 MessagePack 模块。
- Checkpoint 是执行恢复位置的事实来源；告警状态、报告、决策和审计仍以业务表为准。
- `interrupt()` 所在节点在恢复时会从节点开头重新执行，因此中断之前不得放置非幂等副作用。

## 影响

- V1.3B 可在没有 Redis、Celery、Dify 和模型密钥的情况下离线开发和稳定测试。
- 当前入口是后端应用服务与集成测试，尚未暴露工作流 API；Web 页面不会把规划能力描述成已上线能力。
- V1.3C 接入 Celery 时只改变运行位置和触发方式，不改变图状态、业务事实表或恢复协议。
