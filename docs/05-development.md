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

`.env` 不得提交。变量使用 `ALERT_SAGE_` 前缀映射后端配置；容器内数据库地址由 Compose 注入，仓库示例在宿主机使用 PostgreSQL `15432`、Redis `16379`、API `18000`、Web `15173`、Worker 指标 `19101`、Prometheus `19090` 和 Grafana `13000`，避免与常用默认端口冲突。

### 2.1 Dify Cloud

离线开发保持 `ALERT_SAGE_KNOWLEDGE_PROVIDER=mock`。连接 Dify Cloud 时，在根目录 `.env` 中设置：

```dotenv
ALERT_SAGE_KNOWLEDGE_PROVIDER=dify
ALERT_SAGE_DIFY_BASE_URL=https://api.dify.ai/v1
ALERT_SAGE_DIFY_DATASET_ID=<知识库 UUID>
ALERT_SAGE_DIFY_EVALUATION_DATASET_ID=<独立评测知识库 UUID>
ALERT_SAGE_DIFY_API_KEY=<Knowledge Service API Key>
ALERT_SAGE_DIFY_HTTP_TIMEOUT_SECONDS=15
ALERT_SAGE_DIFY_POLL_INTERVAL_SECONDS=2
ALERT_SAGE_DIFY_MAX_RETRIES=2
```

API Key 只放在本地 `.env` 或部署平台的密钥系统中，不写入命令、日志、前端变量或 Git。Dify 空数据集不需要预先配置自定义元数据；需要先在 Dify 控制台为该数据集确认高质量索引使用的 Embedding 模型。API 与 Worker 都必须加载相同的 provider、dataset 和 key：前者用于页面检索，后者用于案例发布和诊断上下文采集。

全空数据集在首个文档创建前可能因 Dify 尚未建立底层 Collection 而暂时无法检索，Alert Sage 会把该情况映射为脱敏 `503`，工作流则按单工具失败降级继续。批准第一条报告并完成案例索引后，检索恢复正常。

业务知识库和评测知识库必须使用不同 Dataset。固定评测语料只允许发布到 `ALERT_SAGE_DIFY_EVALUATION_DATASET_ID`，评测检索也只读取该 Dataset，避免合成案例污染业务问答。两个 Dataset 共用同一 Knowledge Service API Key，但建议使用相同 Embedding 模型和索引配置，确保比较条件一致。

发布或刷新 6 份固定评测文档：

```powershell
Set-Location backend
uv run python -m scripts.publish_evaluation_corpus
```

脚本按稳定案例 UUID 查找文档；已完成索引的同名文档会更新并重新索引，因此评测集内容升级后不需要手工删除旧文档。脚本只输出文档数量和外部文档 ID，不输出 API Key。

### 2.2 RAG 评测运行

评测运行通过 API 创建，并由 Outbox Relay 投递给 Celery Worker：

```powershell
$body = @{
  idempotency_key = "rag-calibration-1.0.0-top3"
  split = "calibration"
  top_k = 3
  score_threshold = $null
} | ConvertTo-Json

$run = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:18000/api/v1/rag/evaluations/runs `
  -ContentType application/json `
  -Body $body

Invoke-RestMethod http://localhost:18000/api/v1/rag/evaluations/runs/$($run.run.id)
```

`test` split 还必须传入 `confirm_test_set=true`。同一幂等键配合同一参数返回已有运行；复用幂等键但修改参数返回 `409`。`ALERT_SAGE_RAG_EVALUATION_LOCK_TTL_SECONDS` 控制同一运行的 Redis 短期互斥锁，PostgreSQL 中的运行状态和唯一约束仍是最终事实来源。

Dify Cloud 评测默认使用 `ALERT_SAGE_RAG_EVALUATION_QUERY_INTERVAL_SECONDS=6.5` 控制相邻查询间隔，避免短时间批量检索触发 Cloud 配额后把限流误计为质量错误。该设置只影响离线评测 Worker，不影响业务知识检索 API 和告警诊断。自托管或更高配额环境可以降低该值，但必须先用 calibration 验证 `error_rate` 仍为 0。

部署或本地构建时应将当前 Git 提交 SHA 写入 `ALERT_SAGE_BUILD_REVISION`。该值会随评测运行持久化；留空不会阻止运行，但会降低代码版本审计能力。

固定顺序是：先发布语料，只查看 calibration 并选择参数，固定候选方案后再执行一次 test。查看 test 后继续调参会污染保留集，需要提升评测集版本并补充新的 test 问题。

### 2.3 DeepSeek 诊断模型

离线开发保持 `ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER=mock`。启用真实诊断时，在根目录 `.env` 中设置：

```dotenv
ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER=deepseek
ALERT_SAGE_DIAGNOSTIC_MODEL_BASE_URL=https://api.deepseek.com
ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY=<DeepSeek API Key>
ALERT_SAGE_DIAGNOSTIC_MODEL_NAME=deepseek-v4-flash
ALERT_SAGE_DIAGNOSTIC_MODEL_TIMEOUT_SECONDS=60
ALERT_SAGE_DIAGNOSTIC_MODEL_MAX_RETRIES=2
ALERT_SAGE_DIAGNOSTIC_MODEL_MAX_TOKENS=3000
ALERT_SAGE_DIAGNOSTIC_MODEL_TEMPERATURE=0.1
ALERT_SAGE_DIAGNOSTIC_MODEL_THINKING_ENABLED=false
```

`.env.example` 与实际模板字段保持一致，但 provider 默认使用 `mock` 且 API Key 留空，避免新环境意外产生云端调用和费用。API 与 Worker 接收相同配置，目前只有 Worker 发起诊断调用。完整诊断通常包含诊断与建议两次模型请求；输出无效时可能额外产生一次修复请求。

启用真实模型意味着选定告警字段、分类结果、上下文证据、Dify 检索片段和可选人工反馈会发送到 DeepSeek。当前适合使用合成演示数据；接入真实生产数据前应完成字段脱敏、数据分级和供应商合规评审。

后端集成测试读取 `ALERT_SAGE_TEST_DATABASE_URL`，缺省时复用开发数据库连接，但只在随机命名的临时 Schema 中建表。每项测试结束后会删除对应 Schema，不会清空开发业务表。

### 2.4 可观测性

`.env.example` 默认启用指标并保留冲突较少的宿主机端口：

```dotenv
WORKER_METRICS_PORT=19101
PROMETHEUS_PORT=19090
GRAFANA_PORT=13000
ALERT_SAGE_METRICS_ENABLED=true
ALERT_SAGE_LOG_LEVEL=INFO
```

API 指标位于 `http://localhost:18000/metrics`，Worker multiprocess 指标位于 `http://localhost:19101/metrics`。Prometheus UI 位于 `http://localhost:19090`，预配置 Grafana Dashboard 位于 `http://localhost:13000/d/alert-sage-overview`，匿名访问仅授予 Viewer。指标系统故障不得改变业务结果；需要按具体告警或运行排障时，应查询 PostgreSQL 审计事件，而不是给 Prometheus 增加高基数 ID 标签。

API 与 Worker 默认输出单行 JSON 日志。每个 API 响应包含服务端生成的 `X-Request-ID`；若需要和调用方日志对齐，可传 `X-Client-Request-ID`，不要尝试覆盖 `X-Request-ID`。排障时可按 `request_id`、`alert_id`、`workflow_run_id`、`thread_id` 或 `case_id` 搜索容器日志。日志不会记录原始告警 payload、Prompt、检索 query、模型响应或供应商响应体。

### 2.5 Outbox Relay

V2.5 默认每五秒唤醒一次 Relay，每批最多发布 50 条消息，发布失败按 2 秒起步、最多 60 秒的指数退避重试：

```dotenv
ALERT_SAGE_CELERY_LOG_SERVICE=alert-sage-worker
ALERT_SAGE_OUTBOX_POLL_INTERVAL_SECONDS=5
ALERT_SAGE_OUTBOX_BATCH_SIZE=50
ALERT_SAGE_OUTBOX_RETRY_BASE_SECONDS=2
ALERT_SAGE_OUTBOX_RETRY_MAX_SECONDS=60
```

这些值同时存在于 `.env.example` 和本地 `.env` 模板。Relay 不需要新的凭据；API、Worker 和 Relay 必须连接同一 PostgreSQL 与 Redis。

## 3. 完整容器环境

构建并启动：

```powershell
docker compose up --build
```

Compose 会依次：

1. 启动 PostgreSQL 和 Redis，并等待健康检查。
2. 构建 API 镜像，执行 Alembic 迁移、初始化 LangGraph checkpoint 表并启动 Uvicorn。
3. 启动 Celery Worker 和 Celery Beat Relay；Relay 周期触发 Outbox 发布，Worker 使用 Redis Broker 执行业务任务。
4. 构建 React 静态资源，通过 Nginx 提供页面并代理 `/api`，SSE 路由关闭代理缓冲。
5. 启动 Prometheus，分别抓取 API 与 Worker 指标端点并保留七天数据。
6. 启动 Grafana，通过仓库内 provisioning 自动加载 Prometheus 数据源和 V2.5 Dashboard。

检查状态：

```powershell
docker compose ps
docker compose logs api worker relay prometheus grafana
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
- Docker Compose 八个服务均处于运行或健康状态。
- `workflow_runs`、`workflow_events`、`tool_executions`、`diagnosis_reports`、`human_decisions`、`cases`、`outbox_messages` 已通过迁移创建。
- 数据库能阻止同一告警存在多个活动运行，以及重复事件序号、工具执行和人工决策。
- 工作流 State、诊断报告和人工决策输入通过严格 Pydantic Schema 校验。
- 七节点 LangGraph 可运行至 `human_review` 并持久化中断；关闭并重新创建运行时后可使用相同 `thread_id` 恢复。
- 指标、日志、CMDB 和知识四个模拟工具并发执行；单个工具超时会记录失败和告警，但可使用剩余证据继续诊断。
- 批准和驳回进入对应终态；重新分析保留旧报告与人工反馈，并生成下一版本报告。
- LangGraph checkpoint 表由官方 Checkpointer 管理，Alembic 只管理业务表且不会误删供应商表。
- 工作流 API 返回 `202` 并由 Celery Worker 异步执行，不在 HTTP 请求中等待完整诊断。
- Web 可启动诊断、查看报告与事件，并批准、驳回或携带反馈重新分析。
- SSE 能通过 `Last-Event-ID` 从 PostgreSQL 补发事件，Redis 仅负责通知唤醒。
- API 将业务事实与 Outbox 投递意图原子提交；Broker 不可用时保持 `pending` 并自动退避，恢复后无需人工操作即可继续。
- 人工批准会原子生成结构化案例；独立任务同步知识库，状态、尝试次数和外部文档 ID 可查询。
- 案例同步失败不回滚工作流终态，Web 可重新投递，重复同步已成功案例不会重复发布。
- 知识检索接口返回统一片段结构和可追溯来源，供应商超时、鉴权失败或异常响应统一映射为脱敏的 `503`。
- Web `/knowledge` 支持加载、未检索、空结果、失败和命中结果状态，不直接持有 Dify 凭据。

## 7. 已知边界

- DeepSeek 与 Dify 已完成真实 Cloud 验收，离线测试仍默认使用 Mock；真实 Prometheus/Grafana 监控栈已接入，但工作流上下文中的指标、日志和 CMDB 数据源目前仍为确定性模拟适配器。
- 当前只记录报告级模型名与 Prompt 版本，尚未持久化 token 用量、供应商 request ID、单次调用延迟和费用。
- 真实模型调用尚未实现熔断与全局预算；当前只有超时、有限重试、输出修复和输入长度预算。
- Outbox/Celery 为至少一次投递，不承诺严格一次；当前不设死信终态，持续失败的内部消息保留为 `pending` 并依赖指标、日志和人工排障。
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
  "postgresql+psycopg://alert_sage:alert_sage@localhost:15432/alert_sage"

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

当前预期迁移版本为 `0004`，并能看到 `alerts`、`workflow_runs`、`workflow_events`、`tool_executions`、`diagnosis_reports`、`human_decisions`、`cases` 和 `outbox_messages`。

## 9. V1.3B 专项验证

V1.3B 新增 LangGraph 和官方 PostgreSQL Checkpointer。安装锁定依赖后运行：

```powershell
Set-Location backend
uv sync --python 3.12

$env:ALERT_SAGE_TEST_DATABASE_URL = `
  "postgresql+psycopg://alert_sage:alert_sage@localhost:15432/alert_sage"

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

专项自动化验证覆盖异步准备、人工决策、投递失败和重试；V2.2 完整质量门禁为后端 58 项测试、前端 9 项测试、Ruff、TypeScript 类型检查、Alembic 差异检查及生产构建。容器级验收还应确认 Worker 注册三个 `alert_sage.workflow.*` 任务和一个 `alert_sage.case.sync` 任务，并通过 Nginx SSE 路由按事件序号补发。

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

## 12. V2.1 Dify 专项验证

确认 `.env` 已启用 Dify 后重新构建 API 和 Worker，使运行时依赖与环境变量生效：

```powershell
docker compose up -d --build api worker web
docker compose logs --tail=100 api worker
```

验收链路：

1. 创建一条包含唯一关键词的告警，启动诊断并批准报告。
2. 等待案例 `knowledge_sync_status` 变为 `synced`，确认 `external_document_id` 不再是 `mock-doc-*`。
3. 在 `/knowledge` 使用唯一关键词检索，确认命中文档、相关度和 `dify://` 来源。
4. 创建同服务的第二条告警，确认诊断报告证据中出现 Dify 来源引用。
5. 重试已同步案例，确认不创建同名重复文档。

自动化合约测试使用 `httpx.MockTransport`，不会访问云端或读取真实密钥：

```powershell
Set-Location backend
uv run pytest -q tests/test_dify_knowledge.py
```

2026-07-19 已使用 Dify Cloud 完成真实验收：空数据集首次只读检索按预期返回脱敏 `503`；批准首条报告后，案例一次同步为 `synced` 并获得真实文档 UUID。使用唯一关键词检索命中 4 个片段，首条结果指向同一文档并保留 `dify://` 来源；第二条告警的诊断报告生成 1 条 Dify 知识证据，之后以拒绝结束且未创建额外案例。对已同步案例调用重试接口返回 `dispatched=false`。浏览器实测页面无错误覆盖层和控制台错误，长文档内容不会造成横向溢出。

## 13. V2.2 DeepSeek 专项验证

确认 `.env` 已同时启用 DeepSeek 与 Dify 后重建运行服务：

```powershell
docker compose config --quiet
docker compose up -d --build api worker web
docker compose ps
```

自动化合约测试使用 `httpx.MockTransport`，不访问云端或读取真实密钥：

```powershell
Set-Location backend
uv run pytest tests/test_deepseek_diagnostic_model.py
```

专项覆盖 JSON Mode 请求、严格输出 Schema、未知证据引用修复、超长不可信输入截断、`429` 重试、鉴权脱敏、密钥 `SecretStr` 和 Mock/DeepSeek 工厂切换。完整验收应创建合成告警，确认报告模型与 Prompt 版本，检查知识证据的 `dify://` 来源，在 Web 批准报告，等待案例同步后按案例 UUID 回检。

2026-07-20 已完成真实验收：`deepseek-v4-flash` 的诊断和建议请求均返回 `200`，工作流约 10.5 秒进入人工确认，报告包含指标、日志、CMDB 和 Dify 四类证据，知识来源可追溯到既有 Dify 片段。Web 正确显示模型名、`diagnosis-deepseek-v1`、85% 置信度和人工决策入口。批准后工作流变为 `completed`，案例 `a871f7c0-4910-5c10-92b4-72ab75628746` 首次同步为 `synced`，Dify 文档 ID 为 `6659fa5b-004d-4aba-a07a-f2edbd0e7056`；使用案例 UUID 回检命中同一文档。浏览器无错误覆盖层和控制台错误。

## 14. V2.3 Prometheus/Grafana 专项验证

构建并启动七个服务：

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 api worker prometheus grafana
```

配置与抓取验证：

```powershell
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
Invoke-RestMethod http://localhost:19090/api/v1/targets
Invoke-RestMethod http://localhost:13000/api/health
Invoke-RestMethod http://localhost:13000/api/dashboards/uid/alert-sage-overview
```

验收标准：

1. API 与 Worker 两个 Prometheus target 均为 `up`，API `/metrics` 和 Worker `:9101/metrics` 可抓取。
2. 发起一次完整诊断后，Worker 指标出现工作流、七节点、四工具、LLM 和知识检索序列；批准后出现案例同步序列。
3. HTTP 标签使用 OpenAPI 路由模板，指标输出中不出现具体告警 ID、检索问题、错误正文或文档 ID。
4. Grafana 自动加载 UID 为 `alert-sage-overview` 的九面板 Dashboard，数据源健康且页面无 provisioning 错误。
5. Worker 重启后只清理专用 multiprocess 目录中的 Prometheus `*.db`，不依赖指标保存业务状态。

自动化验证覆盖指标端点自排除、声明路由归一化、节点与知识适配器埋点、LLM 重试/输出修复指标、案例同步状态归一化，以及 Worker 指标目录安全清理。不能仅以配置文件可解析代替运行态验证。

2026-07-20 已完成运行态验收：七个 Compose 服务均为运行/健康状态，`promtool` 校验成功，API 与 Worker 两个 target 均为 `up`；Grafana 13.1.0 的 Prometheus 数据源健康，UID 为 `alert-sage-overview` 的九面板 Dashboard 已自动加载。合成告警 `v2-3-observability-e2e-1784531528626` 完成 Dify 检索、DeepSeek 诊断与建议、人工批准及案例发布，Worker 暴露两次工作流操作、七节点、四工具、两次成功 LLM 请求和知识检索/发布指标，输出中不含告警 ID 或测试标记。首次验收发现案例业务状态已为 `synced`，但任务因字符串状态访问 `.value` 而误报失败；修复为统一状态归一化并补充任务级测试后，对同一案例幂等重投在约 92ms 内成功并产生 `status="synced"` 指标，未重复发布文档。浏览器确认九个面板有内容、无错误覆盖层或控制台错误。

V2.3 质量门为后端 63 项测试、前端 9 项测试、Ruff、Python 编译、Alembic 差异检查、TypeScript 类型检查和生产构建全部通过。

## 15. V2.4 结构化日志与诊断时间线专项验证

重建 API 与 Worker 后，检查运行日志是否为单行 JSON，并使用同一条真实告警贯穿 API、Celery、LangGraph、DeepSeek、Dify 与案例同步链路：

```powershell
docker compose up -d --build api worker
docker compose logs --tail=100 api worker
```

验收标准：

1. API 始终生成可信的 `X-Request-ID` 响应头；合法的调用方标识单独保存在 `client_request_id`，不会覆盖服务端请求 ID。
2. Celery 只传播白名单关联字段，Worker 在没有上游请求时生成新的请求 ID；日志能够按 `request_id`、`alert_id`、`workflow_run_id`、`thread_id` 和 `case_id` 关联。
3. HTTP、任务、节点、工具、LLM 与知识服务日志使用固定 JSON 字段，不记录 Prompt、原始工具载荷、查询正文、模型回复、密钥、异常正文或堆栈。
4. 工作流事件持久化请求来源，重复幂等事件保留第一次写入的来源信息。
5. 告警详情页展示完整的持久化事件时间线、友好名称、状态、相邻事件间隔和请求 ID，并覆盖加载、空数据与错误状态。

2026-07-22 已完成真实端到端验收：告警 `v2-4-correlation-e2e-1784694107777` 经 Dify 检索、DeepSeek 诊断与建议、人工批准和案例发布后进入 `completed`，案例状态为 `synced`，共持久化 19 条事件。启动请求的请求 ID 从 API 投递贯穿首段 Worker 链路，人工决策请求的请求 ID 贯穿恢复、收尾和案例同步链路；日志同时包含相应告警、运行、线程与案例标识。浏览器确认时间线显示 19 项、页面状态为“已完成”，无错误覆盖层、控制台错误或横向溢出。

V2.4 质量门为后端 67 项测试、前端 9 项测试、Ruff、Python 编译、Alembic 差异检查、TypeScript 类型检查、生产构建、Compose 配置检查和真实浏览器验收全部通过。

## 16. V2.5 事务性 Outbox 专项验证

升级业务迁移、构建 V2.5 服务并查看 Relay：

```powershell
Set-Location backend
uv run alembic upgrade head
uv run alembic current
uv run alembic check

Set-Location ..
docker compose up -d --build api worker relay
docker compose logs --tail=100 worker relay
```

数据库可使用以下查询检查积压和投递历史：

```sql
SELECT topic, status, count(*)
FROM outbox_messages
GROUP BY topic, status
ORDER BY topic, status;

SELECT id, topic, aggregate_id, attempts, available_at,
       published_at, last_error_type
FROM outbox_messages
WHERE status = 'pending'
ORDER BY available_at, created_at;
```

验收标准：

1. 工作流启动、人工决策、失败重试和案例同步都在对应业务事务内创建唯一 Outbox 消息；事务回滚时二者同时消失。
2. Redis 停止期间 API 仍返回 `202`，消息保持 `pending`；Redis 恢复后无需重放 HTTP 请求，Relay 自动发布并继续执行。
3. Broker 接受消息但 Outbox 状态提交失败时允许再次发布，稳定 Celery task ID 与消费者数据库幂等共同吸收重复。
4. 多个发布任务通过 `FOR UPDATE SKIP LOCKED` 领取不同消息；批次上限、指数退避和严格消息 Schema 均有自动化覆盖。
5. `outbox_message_id` 随关联上下文进入 JSON 日志，Prometheus 暴露发布结果、投递耗时和退避时长，Grafana 自动加载十一面板 Dashboard。
6. API 的 `dispatched` 只表示本次创建了新投递意图；重复启动、重复决策或已排队重试返回 `false`。

2026-07-26 已完成 Redis 故障恢复验收：停止 Redis 后创建的运行 `609d60ae-90b7-4a0f-8b17-2e64b055aa3f` 保持 `queued`，对应 `workflow.start` 消息持久化为 `pending`；恢复 Redis 后约一个轮询周期自动进入人工确认，批准后工作流完成，案例 `6e491471-4fe3-53b5-8e77-b85d5c16d26d` 自动同步。验收使用临时 Mock 供应商隔离 DeepSeek/Dify 网络波动，完成后恢复 `.env` 中的真实供应商配置。过程中发现下游案例消息继承上游 `outbox_message_id` 时发生关联字段重复绑定；修复为由当前消息 ID 覆盖父级上下文，并加入回归测试，积压案例随后自动恢复，证明消息未丢失。

V2.5 质量门为后端 75 项测试、前端 9 项测试、Ruff、Ruff 格式检查、Python 编译、Alembic `0004` 差异检查、TypeScript 类型检查、生产构建、Compose 配置检查和运行态 Redis 故障恢复全部通过。
