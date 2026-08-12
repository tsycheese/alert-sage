# 真实运行与飞书阶段验收记录

## 1. 验收结论

2026-08-11，Alert Sage 使用合成、非敏感 CPU 告警完成 `real` Profile 的真实 DeepSeek + Dify 诊断/知识闭环，以及企业自建飞书应用在专用测试群中的人工交互闭环。本阶段代码能力与 live 验收完成；固定域名和 Linux VM 长期部署由产品决策延期，因此本记录不把临时 Quick Tunnel 描述为生产部署。

本阶段以 Git revision `1a2df6c` 为开发基线。真实凭据只通过被 Git 忽略的本地环境注入，记录中不保存凭据、供应商正文、完整飞书身份或完整回调载荷。

## 2. Live 验证证据

| 范围 | 结果 | 脱敏证据 |
| --- | --- | --- |
| Runtime Profile | 通过 | `real` 显式选择 `deepseek` + `dify`，缺失或混用 Mock 会启动失败 |
| DeepSeek + Dify smoke | 通过 | `tests/test_vendor_live.py` 使用合成 CPU 数据完成真实检索与结构化诊断 |
| Dify 检索与案例发布 | 通过 | 新 Dataset 完成索引和召回；案例同步失败保持可见，修正 Dataset 后重试为 `synced` |
| 飞书回调安全入口 | 通过 | HTTPS challenge、签名/AES/Token/tenant/chat 校验链路可用 |
| 告警群共享卡片 | 通过 | critical 合成告警通知、版本化状态刷新、Web 详情入口可用 |
| 手动启动与诊断 | 通过 | 卡片启动后依次呈现 queued/running/waiting 状态，真实诊断到达人工确认 |
| 人工批准 | 通过 | 决策进入后端状态机，最终 `completed`，案例同步最终 `synced` |
| 人工驳回 | 通过 | 群卡片最终为 `rejected`，未生成虚假案例同步结果 |
| 反馈重新分析 | 通过 | 空反馈由卡片必填校验拦截；有效反馈生成第二份报告后可再次批准 |
| 私聊审批提醒 | 通过 | 原提醒在决策后转为无业务按钮的终态消息；重新分析和最终批准均有终态通知 |

最终重新分析验收告警使用脱敏 ID `69c7c367…61c0`：报告数为 2，最终决策为批准，案例状态为 `synced`，共享卡片期望/已投递 revision 均为 10，未留下渠道错误。

## 3. 自动化覆盖而非人工 Live 的边界

以下边界由后端测试覆盖，不在本次人工点击记录中冒充 live 结果：重复 event ID 与不同正文重放、Web/飞书并发启动、重复按钮、两个审批人竞争、非等待状态决策、旧 nonce、第四次重新分析、超长反馈、飞书超时/限流/非 JSON、乱序 revision、Redis/Broker 暂时不可用以及供应商错误脱敏。

默认 Demo、Playwright 和普通 pytest 继续使用 `demo/test` Profile；它们拒绝真实供应商与飞书配置，不应读取云端密钥。

## 4. 已知边界与后续条件

- Quick Tunnel 只用于临时 HTTPS 回调验收，关闭后必须在飞书后台禁用或替换回调地址。
- 固定 Linux VM、正式域名、备份恢复、进程自启动、证书与密钥轮换演练尚未上线；仓库仅提供 staging Compose/Caddy 基础。
- 真实 Loki、业务 Prometheus、CMDB、统一认证/RBAC、多群和多 tenant 不在本阶段范围。
- 当前审批人映射是可审计白名单，不等同于完整授权体系。
- 业务生产 Dataset 与真实业务告警需要在明确数据协议、网络和脱敏要求后另行验收。

## 5. 最终工程门禁

2026-08-12 收口门禁结果：

- 后端：Ruff lint 与 format check 通过；pytest `146 passed, 1 skipped`。skipped 项为必须显式开启的供应商 live 测试，普通门禁保持离线。
- 迁移：临时数据库从空库升级至 `0007`；`alembic check` 无待生成操作；`0007 → 0006 → 0007` 回滚/恢复通过，临时数据库随后删除。
- OpenAPI/前端：OpenAPI 与两份生成类型文件重复生成哈希一致；TypeScript typecheck、Vitest `14 passed`、Vite 生产构建通过。
- 依赖：`npm audit --omit=dev` 报告 0 个漏洞。
- Compose：demo 与 staging 两套合并配置均通过解析。
- E2E：隔离 Compose 环境重新构建，Playwright `1 passed`；专用容器、网络和数据卷在结束后自动清理。
- 安全：根 `.env` 未被 Git 跟踪且已被忽略；版本化文件未发现真实密钥、App、用户或群 ID 形态；`.env.example` 的敏感项均为空或示例占位符；`git diff --check` 通过。
- 临时运行环境：收口时已停止指向本地 Web 的 Quick Tunnel，原临时公网地址不可达；本地 readiness 仍为 `ok`，不影响后续离线开发和 Demo。

后端测试仅有一条非功能性警告：当前 Windows 账户无法写入 `backend/.pytest_cache`，不影响测试执行或断言结果。
