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
2. 构建 API 镜像，执行 Alembic 迁移并启动 Uvicorn。
3. 构建 React 静态资源，通过 Nginx 提供页面并代理 `/api`。

检查状态：

```powershell
docker compose ps
docker compose logs api
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
- Web 能展示 API 实时健康状态。
- 后端测试、前端测试、类型检查和构建全部通过。
- Docker Compose 四个服务均处于运行或健康状态。

## 7. 已知边界

- V1.1 尚未实现告警页面、Celery Worker、LangGraph、SSE 和 Dify。
- Redis 在 V0 中仅作为已启动的基础设施，业务代码尚未使用。
- 当前迁移只落地 `alerts` 表，其余核心表将在对应功能实现时逐步加入。
