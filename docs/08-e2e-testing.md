# 端到端测试

## 1. 目标与覆盖范围

V2.7B 使用 Playwright 从真实浏览器验证旗舰告警链路，而不是重复后端集成测试。单个场景覆盖：

1. 通过 Web 表单创建唯一告警。
2. 启动异步诊断并等待 LangGraph 到达人工确认节点。
3. 验证结构化报告仍能在 `logs` 工具连续超时后生成。
4. 刷新详情页，验证 PostgreSQL 业务事实和 checkpoint 可恢复展示。
5. 在 Web 批准建议，等待工作流完成和 Mock 案例同步。
6. 再次提交相同告警，验证 API 返回幂等重放标记且页面回到原告警。

测试使用语义化角色、表单标签和区域名称定位元素。工具失败断言限定在同一条 `logs` 时间线事件内，避免依赖列表序号或全局文本偶然命中。

## 2. 隔离设计

`scripts/run-e2e.ps1` 组合 `docker-compose.yml` 与 `docker-compose.demo.yml`，但使用固定且受保护的 `alert-sage-e2e` Compose project。它设置专用主机端口：

| 服务 | 端口 |
| --- | --- |
| Web | 25173 |
| API | 28000 |
| PostgreSQL | 25432 |
| Redis | 26379 |
| Worker metrics | 29101 |
| Prometheus | 29090 |
| Grafana | 23000 |

PostgreSQL 和 Redis 使用该 project 独有的临时 Volume。每次运行前后只对名称严格等于 `alert-sage-e2e` 的环境执行 `down --volumes --remove-orphans`，不会停止普通开发环境，也不会删除日常数据库 Volume。脚本结束时恢复调用进程原有的环境变量。

API 与 Worker 继承 V2.7A 的离线边界：知识和模型供应商均为 Mock，云端密钥在容器内清空，只有 Worker 注入 `logs` 工具超时。因此 E2E 不依赖 Dify Cloud、DeepSeek 或外部网络。

## 3. 安装与运行

根目录 Playwright 当前固定为 `1.60.0`，传递依赖由 `package-lock.json` 锁定；升级时必须同步安装对应 Chromium 并重新执行两轮隔离验收。首次运行需要：

```powershell
npm ci
npm run e2e:install
```

完整运行：

```powershell
.\scripts\run-e2e.ps1
```

已有当前镜像时可以跳过构建：

```powershell
.\scripts\run-e2e.ps1 -SkipBuild
```

排查容器时可以临时保留环境：

```powershell
.\scripts\run-e2e.ps1 -KeepEnvironment
```

保留环境意味着调用者负责在排查后执行同一 Compose project 的清理；不要将其用于共享或长期环境。

## 4. 等待与失败取证

测试不使用固定业务等待时间：页面断言持续等待状态变更，API 启动使用 readiness 轮询。超时代表可见业务状态在预算内未完成，而不是通过任意延长 `sleep` 掩盖竞态。

Playwright 失败时在 `test-results/` 保留截图、视频、trace 和页面上下文，运行器同时保存 `test-results/compose.log`。HTML 报告输出到 `playwright-report/`。这些均为本地产物并已加入 `.gitignore`。

`trace.zip` 可通过以下命令查看：

```powershell
npx playwright show-trace <trace.zip 的路径>
```

无论成功或失败，默认都会清理隔离容器、网络和临时数据卷；取证文件不随 Compose 环境删除。
