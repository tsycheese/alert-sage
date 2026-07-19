# ADR 0002：工作流业务事实与执行快照分离

- 状态：已接受
- 日期：2026-07-19

## 背景

V1.3 将引入 LangGraph、Celery 和人工中断。如果只依赖 checkpoint、Celery Result Backend 或 Redis，页面状态、审计记录和重试幂等在服务重启后无法得到稳定保证。

## 备选方案

1. 只保存 LangGraph checkpoint：实现最少，但不适合作为查询、审计和业务约束的事实来源。
2. 将运行状态保存在 Redis：读写快，但数据可被清理，无法承担不可丢失的人工决策和报告。
3. PostgreSQL 保存业务事实，checkpoint 保存恢复位置，Redis/Celery 保存可重建的调度状态。

## 决策

选择方案 3：

- `workflow_runs` 保存每次业务运行及稳定 `thread_id`，并用启动幂等键和活动运行部分唯一索引防止重复启动。
- `workflow_events` 是追加写审计流，以运行内递增序号支持 SSE 断点续传。
- `tool_executions` 记录工具调用的输入、结果、尝试次数和幂等键。采用“execution”命名，因为一条记录描述的是可重试、有状态的执行，而不只是一次函数调用。
- `diagnosis_reports` 按运行和版本保存，旧报告不可覆盖；结构化内容先通过 Pydantic 校验。
- `human_decisions` 每个报告最多一条，使用全局幂等键；重新分析必须携带反馈。
- 所有审计外键使用 `RESTRICT`，防止误删上游记录时级联清除证据。

## 状态边界

- PostgreSQL 是告警、运行、事件、工具结果、报告和人工决策的事实来源。
- LangGraph checkpoint 是节点恢复位置和执行快照的事实来源。
- Redis、Celery Result Backend 和进程内存只保存可重建状态。
- 状态转换由显式白名单守卫；相同状态重复投递允许作为幂等 no-op，终态不能重新进入活动状态。

## 影响

- V1.3B 必须复用这些模型和状态守卫，不得直接依赖 LangGraph 或供应商对象作为页面数据。
- 事件序号分配和业务表状态变更需要处于同一事务，避免时间线与当前状态不一致。
- 后续若需要数据库与 Celery 投递的原子一致性，再增加事务性 Outbox，不在 V1.3A 提前引入。
