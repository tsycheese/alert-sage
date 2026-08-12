# 核心数据模型

> 实现状态：V2.6B 已将告警、工作流、人工确认、结构化案例、知识同步状态、可靠投递意图和 RAG 评测运行接入 PostgreSQL、API、Celery Worker、Relay、SSE 与 Web。

## 1. 建模目标

核心数据模型需要同时支持：

- 告警幂等接入。
- 多次执行、重试和重新分析。
- 节点及工具调用可观测。
- 人工决策可审计。
- 业务事实与异步投递意图原子提交。
- 诊断报告版本化。
- 已确认案例沉淀。
- LangGraph checkpoint 与业务状态分离。

所有主键使用 UUID，时间统一以带时区的 UTC 时间存储。结构化但可能演进的输入、证据和模型输出使用 PostgreSQL `JSONB`。

## 2. 实体关系

```mermaid
erDiagram
    ALERTS ||--o{ WORKFLOW_RUNS : starts
    WORKFLOW_RUNS ||--o{ WORKFLOW_EVENTS : emits
    WORKFLOW_RUNS ||--o{ TOOL_EXECUTIONS : invokes
    WORKFLOW_RUNS ||--o{ DIAGNOSIS_REPORTS : produces
    DIAGNOSIS_REPORTS ||--o| HUMAN_DECISIONS : receives
    DIAGNOSIS_REPORTS ||--o| CASES : becomes
    ALERTS ||--o| FEISHU_CARD_BINDINGS : displays
    FEISHU_CARD_BINDINGS ||--o{ FEISHU_CALLBACK_EVENTS : audits
    FEISHU_CARD_BINDINGS ||--o{ FEISHU_DELIVERIES : delivers

    ALERTS {
        uuid id PK
        varchar source
        varchar external_alert_id
        varchar fingerprint
        varchar alert_name
        varchar service
        varchar instance
        varchar severity
        varchar status
        jsonb payload
        timestamptz started_at
        timestamptz created_at
        timestamptz updated_at
    }

    WORKFLOW_RUNS {
        uuid id PK
        uuid alert_id FK
        varchar thread_id UK
        varchar idempotency_key UK
        varchar workflow_version
        varchar status
        varchar current_node
        int attempt
        text error_message
        timestamptz started_at
        timestamptz finished_at
        timestamptz created_at
        timestamptz updated_at
    }

    WORKFLOW_EVENTS {
        uuid id PK
        uuid workflow_run_id FK
        int sequence
        varchar idempotency_key
        varchar event_type
        varchar node_name
        varchar status
        jsonb payload
        timestamptz occurred_at
    }

    TOOL_EXECUTIONS {
        uuid id PK
        uuid workflow_run_id FK
        varchar node_name
        varchar tool_name
        varchar idempotency_key UK
        varchar status
        int attempt
        jsonb input
        jsonb output
        text error_message
        int duration_ms
        timestamptz started_at
        timestamptz finished_at
    }

    DIAGNOSIS_REPORTS {
        uuid id PK
        uuid workflow_run_id FK
        int version
        varchar schema_version
        text summary
        jsonb root_causes
        jsonb evidence
        jsonb recommendations
        decimal confidence
        varchar model_name
        varchar prompt_version
        timestamptz created_at
    }

    HUMAN_DECISIONS {
        uuid id PK
        uuid diagnosis_report_id FK
        varchar idempotency_key UK
        varchar action
        text comment
        varchar actor
        varchar actor_source
        varchar actor_subject
        varchar actor_display_name
        timestamptz created_at
    }

    CASES {
        uuid id PK
        uuid diagnosis_report_id FK
        varchar title
        text symptom
        text root_cause
        text resolution
        jsonb evidence
        jsonb tags
        varchar knowledge_sync_status
        int knowledge_sync_attempt
        varchar external_document_id
        varchar sync_error_code
        text sync_error_message
        timestamptz synced_at
        timestamptz created_at
        timestamptz updated_at
    }
```

## 3. 表定义

### 3.1 `alerts`

保存外部告警和当前对外状态。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `source` | 告警来源，第一版固定为 `web` |
| `external_alert_id` | 来源系统中的告警 ID |
| `fingerprint` | 根据服务、实例、告警名等生成的稳定指纹 |
| `status` | 页面展示的业务状态 |
| `payload` | 未丢失信息的原始告警 JSON |

约束与索引：

- 唯一约束：`UNIQUE(source, external_alert_id)`。
- 普通索引：`status`、`service`、`severity`、`created_at DESC`。
- `external_alert_id` 负责请求幂等；`fingerprint` 为后续相似告警聚合预留。

### 3.2 `workflow_runs`

一次告警可以有多次运行，例如人工触发重试；一次运行内部可以产生多版诊断报告。

关键字段：

- `thread_id`：传给 LangGraph Checkpointer 的稳定游标，全局唯一。
- `idempotency_key`：工作流启动命令的全局幂等键，防止 API 或任务重复投递创建第二次运行。
- `workflow_version`：记录图结构版本，避免升级后无法解释旧状态。
- `current_node`：供页面快速展示，不替代 checkpoint。
- `attempt`：运行级重试次数。

约束与索引：

- `UNIQUE(thread_id)` 和 `UNIQUE(idempotency_key)`。
- 索引：`alert_id`、`status`、`updated_at`。
- 同一告警同一时刻最多存在一个活动运行，由覆盖 `queued`、`running`、`waiting_for_approval`、`reanalyzing` 的 PostgreSQL 部分唯一索引保证。

### 3.3 `workflow_events`

追加写入的业务事件流，用于时间线、审计和 SSE 补发。

常见 `event_type`：

```text
workflow_started
workflow_queued
node_started
node_completed
node_failed
tool_started
tool_completed
tool_failed
human_input_required
human_decision_received
workflow_completed
workflow_rejected
workflow_failed
case_created
case_sync_started
case_sync_succeeded
case_sync_failed
```

事件不可原地修改。每个运行内的 `sequence` 从 1 递增，并与 `workflow_run_id` 组成唯一约束；SSE 客户端使用序号作为断点补发断线期间的事件。`(workflow_run_id, idempotency_key)` 防止恢复或重试时追加重复事件。

### 3.4 `tool_executions`

记录日志、指标、CMDB 和知识检索等工具调用。

`idempotency_key` 建议由以下信息生成：

```text
workflow_run_id + node_name + tool_name + normalized_input_hash
```

唯一约束确保 Celery 重试或 LangGraph 恢复时能够复用已完成结果，而不是重复调用有副作用的外部系统。

每次执行还保存 `node_name`、状态、尝试次数、输入输出、错误码、错误消息和耗时；输入输出为 JSONB，但不得写入未脱敏的密钥或凭证。

### 3.5 `diagnosis_reports`

每次初次诊断或重新分析生成一个新版本，不覆盖旧报告。

推荐的 `evidence` 元素结构：

```json
{
  "type": "metric",
  "source": "mock-prometheus",
  "title": "CPU 与请求量同时上升",
  "content": "14:20 后 CPU 从 45% 上升至 92%",
  "reference": "metric://order-service-01/cpu?from=14:20",
  "score": 0.96
}
```

约束：

- `UNIQUE(workflow_run_id, version)`。
- `confidence` 范围为 0 到 1。
- `schema_version` 固定当前结构版本，便于后续兼容旧报告。
- LLM 输出先通过 Pydantic Schema 校验后才能入库。

### 3.6 `human_decisions`

记录对某一版诊断报告的人工反馈。

`action` 仅允许：

```text
approve
reject
reanalyze
```

决策写入与运行状态变更应处于同一数据库事务中。接口需要防止对已结束运行重复审批。

每份报告最多接受一个决策，`idempotency_key` 全局唯一；`reanalyze` 必须提供非空且不超过 1,000 字符的反馈。`actor_source` 区分 `web/feishu`，飞书身份保存 app-scoped `open_id` 和配置标签快照；旧数据回填为 Web 来源。Web 请求体中的 `actor` 仍是不可信演示字段。数据库约束负责最终一致性，Pydantic Schema 负责在进入事务前返回可理解的校验错误。

### 3.7 `cases`

只有批准后的诊断报告才生成案例。案例同时保留结构化内容和外部知识库同步信息。

`knowledge_sync_status`：

```text
pending
syncing
synced
failed
```

`diagnosis_report_id` 唯一，保证一份已批准报告最多生成一条案例。`knowledge_sync_attempt` 记录实际同步尝试次数；`external_document_id` 保存外部知识服务返回的文档 ID，错误代码和脱敏错误消息用于页面诊断。同步成功时间写入 `synced_at`。

案例创建与批准后的终态流转位于同一数据库事务，并追加 `case_created` 事件。同步任务使用稳定幂等键 `case-sync:{case_id}`，依次追加开始、成功或失败事件；重复执行已同步案例时直接返回已有结果。同步失败不影响告警工作流完成，可通过 API 重新投递。

### 3.8 `outbox_messages`

Outbox 保存已经随业务事务提交、但尚未确认发布到 Celery Broker 的内部消息。允许的 `topic` 只有 `workflow.start`、`workflow.resume`、`workflow.retry`、`case.sync`、`rag.evaluation.run`、`feishu.card.sync` 和 `feishu.reminder.send`；`payload` 在发布前必须通过对应的严格 Pydantic Schema。

核心字段：

- `idempotency_key`：全局唯一，防止同一业务动作产生多条投递意图。
- `aggregate_id`：对应工作流运行或案例 UUID，配合 `topic` 用于检索投递历史。
- `status`：`pending` 或 `published`；业务完成状态不从该字段推断。
- `attempts`、`available_at`：记录发布尝试次数与指数退避后的下次可领取时间。
- `correlation`：只保存白名单关联 ID，供 Celery headers 和结构化日志继续传播。
- `published_at`、`last_error_type`：记录发布结果；错误正文、密钥和供应商响应不得入库。

Relay 按 `available_at, created_at` 读取 `pending` 消息，并使用部分索引和 `FOR UPDATE SKIP LOCKED` 支持并发领取。消息发布和标记 `published` 之间仍可能发生进程崩溃，因此该表保证投递意图不丢失，不保证严格一次消费。

### 3.9 `rag_evaluation_runs` 与 `rag_evaluation_results`

`rag_evaluation_runs` 保存一次可复现的检索评测运行。仓库中的严格 JSON 文件仍是评测集事实来源；运行行只固化评测集 ID、版本、SHA-256、可选构建 revision、供应商、独立 Dataset ID、split、TopK、分数阈值、状态和汇总指标。

运行状态为：

```text
queued -> running -> completed
                  -> failed
```

`idempotency_key` 全局唯一；创建运行和 `rag.evaluation.run` Outbox 意图在同一事务提交。Worker 重复收到已完成运行时直接返回，不重复检索或写入结果；失败重试会清理该运行未完成的旧结果并增加 `attempt`。

`rag_evaluation_results` 每个问题一行，以 `UNIQUE(run_id, query_id)` 防止重复结果，保存问题、标准相关案例、归一化检索条目、逐题命中指标、拒答判断、延迟和脱敏错误码。检索条目只保存文档/片段标识、案例 UUID、排名、分数和来源，不保存 Cloud 返回的片段正文。

两张表分离的目的是支持按问题、难度和失败类型查询及后续 Web 对比，避免把全部逐题结果塞入一个不可维护的 JSONB 大对象。详细决策见 ADR 0011。

### 3.10 飞书渠道表

`feishu_card_bindings` 保证一条告警至多一个共享卡片绑定，保存目标 chat、当前 message、期望/已投递 revision、当前 nonce 哈希、渠道状态和脱敏错误码。共享卡片不是业务事实，只是 PostgreSQL 告警状态的版本化投影。

`feishu_callback_events` 以 `UNIQUE(app_id, event_id)` 防重放，保存 app-scoped open_id、标准化动作、消息/群绑定、原始密文请求 SHA-256、处理结果和脱敏错误。不保存解密后的完整正文；同一 event ID 携带不同正文会被拒绝。

`feishu_deliveries` 保存共享卡片和私有提醒的幂等投递，每行包含 binding、kind、recipient、revision、动作 nonce、尝试次数、状态、结果 message ID 和脱敏错误码。私有提醒首次发送的 `decision_id` 为空；决策被接收后创建的新 delivery 复制原 message ID，并用可空外键 `decision_id` 固定引用对应 HumanDecision，从而在异步乱序下仍能把正确的私聊提醒更新为已处理。旧 revision 即使被至少一次重复消费，也只标记自身完成，不得覆盖 binding 的新 revision；私聊更新失败只影响自身 delivery，不得把共享 binding 标记为失败。

## 4. LangGraph 状态

LangGraph State 是执行期间的数据载体，不直接等同于数据库 ORM 模型。V1.3A 使用 Pydantic 严格模型作为运行时边界，未来节点接收其 JSON 模式输出：

```python
class AlertWorkflowState(BaseModel):
    schema_version: Literal["1.0"]
    alert_id: UUID
    workflow_run_id: UUID
    thread_id: str
    alert: dict[str, JsonValue]
    classification: dict[str, JsonValue] | None
    contexts: dict[str, ContextSnapshot]
    tool_errors: list[WorkflowToolError]
    diagnosis: dict[str, JsonValue] | None
    recommendations: list[dict[str, JsonValue]]
    report_id: UUID | None
    report_version: int
    human_decision: WorkflowHumanDecision | None
    decision_idempotency_key: str | None
    reanalysis_count: int
    final_status: Literal["completed", "rejected"] | None
    warnings: list[str]
```

状态设计约束：

- 只放可序列化数据，不放数据库 Session、HTTP Client 或函数对象。
- 大型日志全文保存在业务表或对象存储，State 仅保留摘要和引用。
- 并行节点写同一字段时使用明确 reducer，避免结果互相覆盖。
- `workflow_run_id` 与 `thread_id` 分工明确：前者是业务运行 ID，后者是 checkpoint 游标。

### 4.1 Checkpoint 表

`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations` 由官方 `langgraph-checkpoint-postgres` 的 `setup()` 管理，不纳入项目 Alembic ORM 元数据。Alembic 自动差异检查显式忽略这四张供应商表，避免生成误删除迁移。

Checkpoint 只保存 JSON 安全的图状态；反序列化器禁用任意 JSON/MessagePack 模块导入。业务查询、审计与权限判断仍然只读取项目业务表。

### 4.2 状态转换护栏

状态修改必须经过 `app.workflows.alert.transitions`，相同状态重复投递视为幂等 no-op。主要路径为：

```text
received -> running -> waiting_for_approval -> completed
                                  |-> rejected
                                  |-> reanalyzing -> waiting_for_approval
running / reanalyzing -> failed -> running
```

工作流运行以 `queued` 开始；失败运行可以重新进入 `queued` 并增加 `attempt`。`completed` 和 `rejected` 是终态，不允许恢复为活动状态。

## 5. 状态来源优先级

| 状态类型 | 事实来源 |
| --- | --- |
| 告警是否存在、当前业务状态 | PostgreSQL 业务表 |
| 工作流恢复位置及节点快照 | LangGraph Checkpointer |
| 页面时间线和审计记录 | `workflow_events` |
| 任务是否仍需投递 | `outbox_messages` |
| 任务是否正在某 Worker 执行 | Celery/Redis 临时状态 |
| 实时页面通知 | Redis Pub/Sub，断线后由事件表补偿 |

Celery 和 Redis 状态不可用于判断告警最终是否完成。

## 6. 后续数据模型

以下模型在第一版闭环稳定后再加入：

- `knowledge_documents`、`knowledge_chunks`：自研 pgvector 检索。
- `rag_evaluation_sets`：如未来需要在线管理评测集，再评估是否从 Git 事实来源迁移。
- `users`、`roles`：真实认证与权限。
- `alert_groups`：告警聚合与抑制。
