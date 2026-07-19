# 本地开发与验证

## 1. 环境要求

- Docker Desktop 与 Docker Compose
- Python 3.12 和 uv
- Node.js 22 与 npm 10+

推荐优先使用 Docker Compose 验证完整环境，使用宿主机进程进行日常开发。

## 2. 环境变量

从示例创建本地环境文件：

```powershell
Copy-Item .env.example .env
```

`.env` 不得提交。变量使用 `ALERT_SAGE_` 前缀映射后端配置；容器内数据库地址由 Compose 注入，宿主机默认连接 `localhost:5432`。

后端集成测试读取 `ALERT_SAGE_TEST_DATABASE_URL`，缺省时复用开发数据库连接，但只在随机命名的临时 Schema 中建表。每项测试结束后会删除对应 Schema，不会清空开发业务表。

## 3. 完整容器环境

构建并启动：

```powershell
docker compose up --build
```

Compose 会依次：

1. 启动 PostgreSQL 和 Redis，并等待健康检查。
2. 构建 API 镜像，执行 Alembic 迁移、初始化 LangGraph checkpoint 表并启动 Uvicorn。
3. 启动 Celery Worker，使用 Redis Broker 调度工作流任务。
4. 构建 React 静态资源，通过 Nginx 提供页面并代理 `/api`，SSE 路由关闭代理缓冲。

检查状态：

```powershell
docker compose ps
docker compose logs api worker
```

删除容器但保留数据卷：

```powershell
docker compose down
```

同时删除本地数据库和 Redis 数据：

```powershell
docker compose down --volumes
```

最后一条命令会删除开发数据，只在确认不需要保留时使用。

## 4. 后端开发

先启动基础设施：

```powershell
docker compose up -d postgres redis
```

安装依赖、迁移并启动 API：

```powershell
Set-Location backend
uv sync --python 3.12
uv run alembic upgrade head
uv run python -m scripts.setup_checkpointer
uv run uvicorn app.main:app --reload
```

验证：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic check
```

告警 API 示例：

```powershell
$body = @{
  source = "web"
  external_alert_id = "alert-20260718-001"
  alert_name = "HighCPUUsage"
  service = "order-service"
  instance = "order-service-01"
  severity = "critical"
  value = 92.5
  threshold = 80
  started_at = "2026-07-18T14:30:00+08:00"
  payload = @{ summary = "CPU usage remained high for five minutes" }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/alerts `
  -ContentType "application/json" `
  -Body $body

Invoke-RestMethod http://localhost:8000/api/v1/alerts
```

## 5. 前端开发

后端 Schema 或路由响应发生变化后，先更新 OpenAPI 快照并重新生成前端类型：

```powershell
Set-Location backend
uv run python -m scripts.export_openapi

Set-Location ../frontend
npm run api:types
```

`openapi/openapi.json` 和 `frontend/src/api/generated` 均提交到仓库，使 CI 和代码审查可以发现契约漂移。生成文件不得手工修改。

```powershell
Set-Location frontend
npm ci
npm run dev
```

Vite 将 `/api` 代理到 `http://localhost:8000`。验证命令：

```powershell
npm run typecheck
npm test
npm run build
```

## 6. 当前验收范围

- `GET /api/v1/health/live` 不依赖数据库并返回 API 版本。
- `GET /api/v1/health/ready` 仅在数据库可连接时返回成功。
- Alembic 能创建 `alerts` 表，且模型元数据不存在未迁移差异。
- 告警支持创建、详情、过滤和分页查询。
- `source + external_alert_id` 在顺序和并发请求下均保持幂等，冲突内容返回 `409`。
- 请求字段、时区和枚举通过 Pydantic 校验，非法请求返回 `422`。
- API 的 `404`、`409` 和 `422` 使用统一错误信封，且 OpenAPI 能生成可用的前端类型。
- Web 能展示 API 实时健康状态，以及告警列表、创建、详情、空数据、加载、错误、幂等冲突和 404 状态。
- 列表筛选和分页状态写入 URL，可刷新和分享；创建成功后进入对应详情页。
- 后端测试、前端测试、类型检查和构建全部通过。
- Docker Compose 五个服务均处于运行或健康状态。
- `workflow_runs`、`workflow_events`、`tool_executions`、`diagnosis_reports`、`human_decisions`、`cases` 已通过迁移创建。
- 数据库能阻止同一告警存在多个活动运行，以及重复事件序号、工具执行和人工决策。
- 工作流 State、诊断报告和人工决策输入通过严格 Pydantic Schema 校验。
- 七节点 LangGraph 可运行至 `human_review` 并持久化中断；关闭并重新创建运行时后可使用相同 `thread_id` 恢复。
- 指标、日志、CMDB 和知识四个模拟工具并发执行；单个工具超时会记录失败和告警，但可使用剩余证据继续诊断。
- 批准和驳回进入对应终态；重新分析保留旧报告与人工反馈，并生成下一版本报告。
- LangGraph checkpoint 表由官方 Checkpointer 管理，Alembic 只管理业务表且不会误删供应商表。
- 工作流 API 返回 `202` 并由 Celery Worker 异步执行，不在 HTTP 请求中等待完整诊断。
- Web 可启动诊断、查看报告与事件，并批准、驳回或携带反馈重新分析。
- SSE 能通过 `Last-Event-ID` 从 PostgreSQL 补发事件，Redis 仅负责通知唤醒。
- Broker 首次投递失败会持久化失败状态并支持重试。
- 人工批准会原子生成结构化案例；独立任务同步知识库，状态、尝试次数和外部文档 ID 可查询。
- 案例同步失败不回滚工作流终态，Web 可重新投递，重复同步已成功案例不会重复发布。

## 7. 已知边界

- Dify、真实模型和真实运维工具尚未接入，当前使用确定性模拟适配器。
- Dify 发布与检索尚未接入；案例同步当前使用确定性 `MockCasePublisher`，真实供应商通过同一适配器协议替换。
- V1.3C 使用数据库事务提交后投递 Celery；首次投递失败可见且可重试，但尚未使用事务性 Outbox 消除进程在提交与投递之间退出的窗口，Outbox 计划在 V2 落地。
- 尚未接入认证和 RBAC；`actor` 当前为演示审计字段。

## 8. V1.3A 专项验证

启动 PostgreSQL 并升级迁移：

```powershell
docker compose up -d postgres

Set-Location backend
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

运行工作流契约和数据库约束测试：

```powershell
$env:ALERT_SAGE_TEST_DATABASE_URL = `
  "postgresql+psycopg://alert_sage:alert_sage@localhost:5432/alert_sage"

uv run pytest -q tests/test_workflow_foundation.py
uv run pytest -q
```

专项测试应包含 14 条用例，覆盖严格 State、诊断报告引用、人工反馈、状态转换、活动运行唯一性，以及启动、事件、工具、报告和人工决策的幂等、版本与唯一约束。测试使用临时 Schema，结束后自动删除，不会修改开发业务表。

可选地通过 PostgreSQL 客户端查看迁移结果：

```sql
SELECT version_num FROM alembic_version;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

当前预期迁移版本为 `0003`，并能看到 `alerts`、`workflow_runs`、`workflow_events`、`tool_executions`、`diagnosis_reports`、`human_decisions` 和 `cases`。

## 9. V1.3B 专项验证

V1.3B 新增 LangGraph 和官方 PostgreSQL Checkpointer。安装锁定依赖后运行：

```powershell
Set-Location backend
uv sync --python 3.12

$env:ALERT_SAGE_TEST_DATABASE_URL = `
  "postgresql+psycopg://alert_sage:alert_sage@localhost:5432/alert_sage"

uv run pytest -q tests/test_alert_workflow_runtime.py
uv run pytest -q
uv run python -m scripts.setup_checkpointer
uv run alembic check
```

运行时专项测试使用随机临时 Schema，同时创建业务表和四张 Checkpointer 供应商表；测试结束后整体删除 Schema。测试覆盖：

1. 初次运行依次完成解析、分类、上下文采集、诊断和建议，并停在人工确认。
2. 第一个独立 Python 进程在中断后退出，第二个独立进程从 PostgreSQL 读取同一 `thread_id` 的 checkpoint 并批准完成剩余节点。
3. 重新分析生成第二版报告，重复决策幂等且不会错误作用于新报告。
4. 单个上下文工具连续失败两次时记录失败，其他证据仍能生成报告。
5. 不符合 Schema 的模型输出不会写入报告，运行与告警进入失败状态。

也可以使用开发 CLI 手工演示两个独立进程。先创建一条告警并记录其 UUID，然后分别运行：

```powershell
uv run python -m scripts.run_workflow start `
  --alert-id "<alert-id>" `
  --idempotency-key "demo-workflow-start-001"

uv run python -m scripts.run_workflow resume `
  --workflow-run-id "<workflow-run-id>" `
  --action approve `
  --decision-key "demo-workflow-decision-001" `
  --actor "demo-user"
```

第一条命令返回 `waiting_for_approval` 后进程已经退出；第二条命令会创建新的运行时并从数据库恢复。该 CLI 是 V1.3B 的开发验收入口，不是对外 API。

## 10. V1.3C 专项验证

启动完整环境并检查 API、Worker 与 Web：

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 api worker
```

在 Web 详情页点击“启动诊断”，等待状态变为“等待人工确认”，查看报告和事件后选择批准、拒绝或填写反馈重新分析。API 对应入口为：

```text
POST /api/v1/alerts/{id}/workflow
GET  /api/v1/alerts/{id}/workflow
GET  /api/v1/alerts/{id}/events
GET  /api/v1/alerts/{id}/stream
POST /api/v1/alerts/{id}/decisions
POST /api/v1/alerts/{id}/retry
```

专项自动化验证覆盖异步准备、人工决策、投递失败和重试；V1.4 完整质量门禁为后端 39 项测试、前端 8 项测试、Ruff、TypeScript 类型检查及生产构建。容器级验收还应确认 Worker 注册三个 `alert_sage.workflow.*` 任务和一个 `alert_sage.case.sync` 任务，并通过 Nginx SSE 路由按事件序号补发。

## 11. V1.4 专项验证

批准报告后，Worker 会生成案例并异步调用知识库发布适配器。页面应先后展示 `pending`/`syncing` 与 `synced`；成功时存在以 `mock-doc-` 开头的外部文档 ID。对应接口为：

```text
GET  /api/v1/alerts/{id}/case
POST /api/v1/alerts/{id}/case/retry
```

数据库事件时间线应追加 `case_created`、`case_sync_started`、`case_sync_succeeded`；失败时追加 `case_sync_failed`，案例保留错误信息并允许重试。拒绝报告不得生成案例。自动化测试还覆盖同步幂等和失败后重试。

2026-07-19 已完成 Docker Compose 容器验收：Web 经 Nginx 返回 `200`，API 健康状态为 `ok`；真实创建的告警运行至 `completed`，报告版本为 1，案例以 1 次尝试同步为 `synced` 并生成 `mock-doc-*` 外部文档 ID。该链路共写入 19 条事件，SSE 使用 `Last-Event-ID: 15` 能补发 `case_created`、`workflow_completed`、`case_sync_started` 和 `case_sync_succeeded` 后正常关闭。再次请求同步已成功案例返回 `dispatched=false`，尝试次数和事件数量均未增加。

运行专项测试：

```powershell
Set-Location backend
uv run pytest -q tests/test_cases.py

Set-Location ../frontend
npm test -- --run
npm run build
```
