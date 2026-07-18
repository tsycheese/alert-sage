# Alert Sage

Alert Sage 是一个面向运维场景的 AI 告警诊断助手，以“告警接入 → 上下文采集 → 根因分析 → 人工确认 → 案例沉淀”为核心链路。

项目定位是用于求职展示的准生产级 MVP：核心流程真实可运行，状态可恢复，异常可观测，同时避免为了堆叠技术栈过早引入微服务和 Kubernetes。

## 当前状态

`V1.1 告警 API`：已建立 FastAPI、React、PostgreSQL、Redis、Alembic、测试与 Docker Compose 基础设施，并实现告警幂等创建、过滤分页和详情查询。告警页面与 LangGraph 工作流将在后续 V1 增量中实现。

## 技术栈

- 后端：Python 3.12、FastAPI、SQLAlchemy、Alembic、uv
- 前端：React、TypeScript、Vite、Ant Design、TanStack Query
- 数据：PostgreSQL、Redis
- 交付：Docker Compose、pytest、Vitest
- 后续：LangGraph、Celery、Dify、Prometheus、Grafana

## Docker 一键启动

```powershell
Copy-Item .env.example .env
docker compose up --build
```

启动后访问：

- Web：http://localhost:5173
- API 文档：http://localhost:8000/docs
- API 健康检查：http://localhost:8000/api/v1/health/live
- API 就绪检查：http://localhost:8000/api/v1/health/ready

停止服务：

```powershell
docker compose down
```

## 本地开发

后端：

```powershell
Set-Location backend
uv sync --python 3.12
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

质量检查：

```powershell
Set-Location backend
uv run ruff check .
uv run ruff format --check .
uv run pytest

Set-Location ../frontend
npm run typecheck
npm test
npm run build
```

## 目录

```text
backend/          FastAPI、SQLAlchemy、Alembic 和后端测试
frontend/         React/Vite Web 应用和前端测试
docs/             项目范围、架构、数据模型和路线图
docker-compose.yml
```

## 文档

- [项目范围与验收标准](docs/01-project-scope.md)
- [技术选型与架构设计](docs/02-architecture.md)
- [核心数据模型](docs/03-data-model.md)
- [开发路线图](docs/04-roadmap.md)
- [本地开发与验证](docs/05-development.md)
