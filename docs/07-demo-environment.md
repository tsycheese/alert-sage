# 确定性演示环境

## 1. 目标与成功标准

V2.7A 为求职展示提供一套可重复、可审计且默认离线的核心告警场景。它解决的是“新环境能否稳定证明已有架构能力”，而不是再增加一套业务写入路径。

成功标准：

- 空数据库能创建固定告警并在两分钟内到达人工确认节点。
- 时间线稳定包含四个上下文工具，其中 `logs` 连续超时后降级，其余证据仍生成结构化报告。
- 重复运行使用相同告警和工作流，不产生重复业务事实。
- 可选自动批准后，工作流从 PostgreSQL checkpoint 恢复并等待 Mock 案例同步完成。
- 容器接收当前 Git revision；工作区不干净时带 `-dirty` 标记，且演示过程不调用 Dify Cloud 或 DeepSeek。
- 已有数据库数据和 Volume 不被删除或覆盖。

## 2. 方案选择

演示初始化通过 FastAPI 公开接口，而不是直接向 PostgreSQL 插入记录。直接写表更快，但会绕过请求 Schema、数据库幂等处理、事务性 Outbox、Celery Relay 和 LangGraph 状态机，得到的结果无法证明真实系统链路。

演示使用 `docker-compose.demo.yml` 覆盖运行配置：

- API 与 Worker 强制选择 Mock 知识和诊断适配器。
- API、Worker 与 Relay 显式使用 `ALERT_SAGE_RUNTIME_PROFILE=demo`，不存在真实模式向 Mock 的隐式降级。
- 容器内 Dify/DeepSeek API Key 被显式清空。
- 只有 Worker 启用 `logs` 工具故障注入。
- Relay 轮询周期缩短到一秒，减少现场等待时间。

故障注入配置默认是 `none`，并由 Settings 拒绝在 `real/test` 或任何真实供应商模式启用。故障来源是部署配置，不接受告警 payload 控制。

## 3. 场景契约

场景版本为 `v2.7a-checkout-database-regression`，稳定事实包括：

- `checkout-service / checkout-api-01` 的 `HighCPUUsage` critical 告警。
- CPU 96.4%，阈值 80%，近期发布后数据库查询超过两秒。
- 固定、带时区的开始时间和版本化 `external_alert_id`。
- 稳定工作流启动键和人工批准键。

告警创建重放由 `(source, external_alert_id)` 约束识别，工作流与决策分别使用稳定幂等键。若未来修改固定告警的业务内容，必须提升场景版本；静默复用旧 ID 会按设计返回 `409`，防止覆盖历史事实。

## 4. 操作方式

准备到人工确认：

```powershell
.\scripts\start-demo.ps1
```

完整自动验收到案例同步：

```powershell
.\scripts\start-demo.ps1 -Approve
```

复用已有镜像：

```powershell
.\scripts\start-demo.ps1 -SkipBuild
```

脚本输出 JSON，其中包括告警 ID、详情 URL、工作流运行 ID、是否发生幂等重放、是否观察到 `logs` 失败，以及自动批准后的案例同步状态。

停止演示服务但保留数据：

```powershell
docker compose -f docker-compose.yml -f docker-compose.demo.yml down
```

V2.7A 不提供数据重置命令。清空数据库或 Volume 属于破坏性操作，不能作为默认演示步骤。

## 5. 边界与后续

- 当前脚本只准备旗舰告警场景，不伪造 RAG 评测结果；RAG 页面继续展示真实、持久化的历史运行。
- 默认模式停在人工节点，便于通过 Web 展示报告、工具降级和审计时间线。
- `-Approve` 用于自动化验收，不替代求职演示中的人工操作。
- V2.7B 已使用相同 Mock 供应商、故障注入边界和公开 API 构建 Playwright 主路径验收；测试数据采用独立随机标识，避免污染固定演示场景。
- V2.7C 已通过独立截图场景自动生成页面证据，并在求职展示指南中提供架构图与五分钟讲解脚本。

## 6. 首次运行态验收

2026-07-26 使用已有 PostgreSQL Volume 完成以下验证：

- 首次执行创建固定告警和工作流，并停在 `waiting_for_approval`。
- 时间线观察到 `logs` 工具失败，其他三个工具成功，报告仍通过结构化 Schema 校验。
- 重复准备返回同一告警与运行，`replayed=true`、`dispatched=false`。
- 自动批准后工作流完成，Mock 案例同步为 `synced`。
- 完成态重复执行不再次提交决策或创建案例。
- 浏览器展示 19 条持久化事件，控制台无错误，页面无横向溢出。

验收过程中发现决策投递后第一次读取可能仍为 `waiting_for_approval`。这是 Outbox Relay 消费前的正常暂态，不是状态回退；初始化器现会在总超时范围内继续等待，并有专项回归测试覆盖。
