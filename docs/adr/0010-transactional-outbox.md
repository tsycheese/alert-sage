# ADR 0010：使用事务性 Outbox 保证任务投递

- 状态：已接受
- 日期：2026-07-26

## 背景

V2.4 及以前由 API 或工作流任务在 PostgreSQL 事务提交后直接调用 Celery。若数据库提交成功而 Redis/Celery 暂时不可用，业务状态已经变为 `queued`、已保存人工决策或已创建案例，但对应任务可能永久丢失。先投递任务再提交数据库同样不安全，Worker 可能读取不到尚未提交的业务事实。

V2.5 需要在不引入 Kafka、额外数据库或微服务的前提下消除这个双写窗口，并保留现有 Celery 执行模型。

## 方案比较

1. 保留直接投递并提供人工重试：开发成本最低，但无法证明投递意图不会丢失。
2. PostgreSQL Outbox + Celery Beat Relay：业务事实与投递意图原子提交，复用现有 PostgreSQL、Celery 和 Redis；需要新增表、周期任务和重复投递处理。
3. PostgreSQL Outbox + 独立常驻 Relay：与 Broker 解耦更清晰，但增加独立进程的生命周期、健康检查和部署成本。
4. CDC/Kafka：吞吐和解耦能力最强，但明显超出当前单机求职演示的数据规模与运维边界。

选择方案 2。Compose 中增加 `relay` 进程运行 Celery Beat，周期性触发 Worker 内的 Outbox 发布任务，不增加 Python 依赖或新中间件。

## 决策

1. 工作流启动、人工决策恢复、失败重试和案例同步都在修改业务事实的同一 PostgreSQL 事务中写入 `outbox_messages`。
2. Outbox 只保存内部定义的四类消息，载荷在投递前通过严格 Pydantic Schema 校验，不接受任意任务名或参数。
3. Relay 使用 `FOR UPDATE SKIP LOCKED` 分批声明到期消息，允许多个 Relay 任务并发而不重复领取同一行。
4. 发布成功后将消息标记为 `published`。失败时保持 `pending`，记录脱敏错误类型并使用有上限的指数退避；不依赖 Celery Result Backend 保存状态。
5. 发布成功但数据库提交前进程崩溃时允许重复投递，因此系统语义是至少一次。Celery `task_id` 使用 Outbox UUID，业务消费者继续依赖数据库约束、状态锁和供应商幂等键实现效果幂等。
6. 请求、告警、运行、线程、案例和 Outbox 消息 ID 通过 Outbox `correlation` 与 Celery headers 传播；不保存密钥、异常正文或外部响应。
7. API 响应字段 `dispatched=true` 为兼容保留，V2.5 起表示“本次请求创建了新的持久化投递意图”，不表示 Broker 已同步确认。
8. `0004` 迁移为部署时仍活跃的工作流、已保存决策和待同步案例补建投递意图。重复投递由消费者幂等吸收。

## 结果与边界

- Redis 暂时不可用不会丢失已提交任务；恢复后 Beat 会再次触发 Relay。
- PostgreSQL 仍是业务事实和投递意图的最终来源，Redis 保持可重建。
- 周期轮询增加少量数据库查询和最长一个轮询周期的调度延迟，当前规模可接受。
- Outbox 不提供严格一次投递，也不替代消费者幂等。
- 当前不引入死信状态；内部消息若持续不合法会按有上限的退避保留为 `pending`，通过日志和指标暴露，修复代码后可自动恢复。
