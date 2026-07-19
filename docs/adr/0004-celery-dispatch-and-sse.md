# ADR 0004：Celery 异步派发与可补发 SSE

- 状态：已接受
- 日期：2026-07-19

## 背景

V1.3C 需要让 HTTP 请求不等待完整诊断、让人工等待期间不占用 Worker，并让页面实时看到可审计的工作流状态。数据库事务与消息队列之间还存在双写一致性问题；若本阶段同时实现事务性 Outbox，会引入额外表、投递器和清理策略，扩大增量范围。

## 备选方案

1. FastAPI `BackgroundTasks`：依赖 API 进程，重启会丢任务，无法作为可恢复调度方案。
2. 本阶段直接实现事务性 Outbox：一致性最强，但需要额外调度和运维面，超出 V1 核心闭环的最小验证范围。
3. PostgreSQL 先提交业务事实，再投递 Celery；投递失败显式返回并支持幂等补偿，V2 再加入 Outbox。

## 决策

选择方案 3：

- API 只准备 `queued` 运行、持久化人工决策或准备重试，然后返回 `202`；LangGraph 在独立 Celery Worker 中执行。
- Celery 使用 Redis Broker，不启用 Result Backend。业务状态、错误和结果只从 PostgreSQL 查询。
- Worker 使用 late acknowledgement、`worker_prefetch_multiplier=1` 和确定性任务参数。每个运行使用 Redis 短期锁阻止并发重复执行；数据库唯一约束和事件幂等键仍是最终保护。
- Worker 重投时检查 PostgreSQL 状态与 LangGraph checkpoint：已有 checkpoint 的运行从当前位置继续，不重新创建业务运行。
- 工作流暂停后任务结束；人工决策先写数据库，再投递独立恢复任务。
- SSE 使用 `workflow_events.sequence` 作为事件 ID。连接时从 PostgreSQL 补发，Redis Pub/Sub 只负责低延迟唤醒，并以周期性数据库查询兜底。
- Nginx 对 SSE 路由关闭缓冲和缓存，普通 API 仍使用默认代理行为。

## 失败语义

- 启动任务首次投递失败时，运行与告警进入 `failed`，错误可查询，`retry` 将同一运行的 `attempt` 加一后重新投递。
- 决策已经持久化但恢复任务投递失败时返回 `503`；调用方必须使用相同决策幂等键重放请求。
- Redis Pub/Sub 消息或短期锁丢失不会删除数据库事实；页面最迟在下一次数据库轮询时看到事件。
- 仍存在“数据库提交成功、进程在消息投递前退出”的小窗口。该限制必须保持可见，V2 使用事务性 Outbox 消除。

## 影响

- API、Worker 与 Web 可以独立重启，人工中断仍由 PostgreSQL checkpoint 恢复。
- Redis 可清空并重建，但清空 Broker 可能需要调用补偿接口重新投递已排队运行。
- 当前容器内 Worker 运行 Linux prefork；Celery 不作为 Windows 宿主机的生产运行方式，本地完整验收以 Docker Compose 为准。
