# 真实运行 Profile 与飞书渠道

## 1. 运行 Profile

Alert Sage 不提供默认运行 Profile。每个进程启动前必须显式配置：

| Profile | 供应商 | 飞书 | 用途 |
| --- | --- | --- | --- |
| `real` | 强制 DeepSeek + Dify | 显式开关 | 开发验收和真实运行 |
| `demo` | 强制 Mock | 禁止 | 确定性离线演示，可注入受控工具故障 |
| `test` | 强制 Mock | 禁止 | 自动化测试，不访问云端 |

组件角色为 `api`、`worker`、`relay`、`migration` 或 `test`。Compose 已分别给 API、Worker、Relay 注入角色；直接运行 Python 命令时必须自行设置。Relay 只接收 Broker 和日志配置，不接收数据库、DeepSeek、Dify 或飞书凭据。

真实模式的最小供应商配置：

```dotenv
ALERT_SAGE_RUNTIME_PROFILE=real
ALERT_SAGE_KNOWLEDGE_PROVIDER=dify
ALERT_SAGE_DIFY_API_KEY=<仅在本地或服务器环境注入>
ALERT_SAGE_DIFY_DATASET_ID=<UUID>
ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER=deepseek
ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY=<仅 Worker 注入>
```

不要在聊天、Git、Compose 文件或截图中传递真实值。`.env.example` 只保存占位符。

## 2. 飞书应用准备

使用企业自建应用并启用机器人和新版卡片回传。第一阶段最小权限为：

- `im:message:send_as_bot`
- `im:message:update`

不申请群消息读取或通讯录读取权限。配置回调：

```text
POST https://<公开域名>/api/v1/integrations/feishu/card-actions
```

API 组件需要 App ID、Verification Token、Encrypt Key、预期 tenant、目标 chat、来源白名单、审批人映射和 HTTPS Web Base URL。Worker 组件需要 App ID、App Secret、目标 chat、审批人映射和 Web Base URL。具体变量见 `.env.example`。

审批 open_id 通过专用测试群内已签名卡片回调采集，由管理员人工配置审计标签。配置只表示本阶段允许提交人工决策，不等同于用户目录、登录身份或完整 RBAC。

## 3. 回调与投递安全

- 业务回调将 `X-Lark-Request-Timestamp` 作为不透明字符串参与原始请求体签名校验，再解密；±5 分钟窗口使用已签名、已加密正文中的 `header.create_time` 校验，并兼容秒、毫秒、微秒和纳秒纪元格式。默认请求体限制 256 KiB。
- 飞书的 URL challenge 按官方协议可能不携带签名头：该请求仍必须通过 AES 解密、严格 Schema 和 Verification Token 校验，且不得产生业务副作用；若携带了签名头则同样校验签名。所有非 challenge 回调都必须携带有效签名。
- 新版回调中的临时 `event.token` 仅用于严格 Schema 兼容性校验；当前异步更新使用应用身份 API，因此该 token 不持久化、不写日志，也不作为业务事实。
- 重复 event ID 返回第一次保存的响应；同一 event ID 携带不同正文会被拒绝。
- 群共享卡片回调必须精确匹配 binding 的 chat/message；私聊提醒回调必须精确匹配已成功投递的 message、recipient open_id、binding、revision 和 nonce。私聊消息不能被其他 open_id 操作，旧私聊提醒仍按过期卡片处理。
- 未授权、过期卡片和业务状态冲突返回私有 Toast 并写审计，不更新共享卡片。
- 飞书 token 以供应商 `expire - 5 分钟` 缓存在 Redis；缓存丢失时重新获取，鉴权失败只强制刷新一次。
- 超时、`429` 和 `5xx` 有有限重试；错误正文不写日志或数据库。

`waiting_for_approval` 为每个白名单审批人发送一次私聊提醒。群共享卡片仍是持续状态投影；私聊提醒不跟随 queued/running/案例同步等中间状态。任一批准、驳回或重新分析决策被 PostgreSQL 接收后，同一事务创建私聊终态更新意图：原提醒更新为已处理、移除所有业务按钮，并仅保留 Web 详情入口。若原消息已删除或过期则发送替代终态提醒；失败只记录在对应 delivery，不改变共享卡片或业务事实。

渠道查询与重试接口：

```text
GET  /api/v1/alerts/{alert_id}/feishu
POST /api/v1/alerts/{alert_id}/feishu/retry
```

查询只返回 eligibility、共享卡片状态、revision、脱敏错误码和重试能力，不返回 token、密钥或完整身份映射。

## 4. Staging 部署

Linux VM 使用基础 Compose 与 staging 覆盖：

```bash
sudo install -d -m 700 -o alert-sage -g alert-sage /etc/alert-sage
sudo install -m 600 -o alert-sage -g alert-sage staging.env /etc/alert-sage/staging.env
sudo -u alert-sage test "$(stat -c %a /etc/alert-sage/staging.env)" = 600
docker compose --env-file /etc/alert-sage/staging.env \
  -f docker-compose.yml -f docker-compose.staging.yml up -d --build
```

固定版本 Caddy 自动申请 TLS；公网只开放 80/443。PostgreSQL、Redis、Prometheus、API、Web 和 Worker 指标不发布主机端口；Grafana 只绑定 `127.0.0.1`，通过 SSH 隧道访问：

```bash
ssh -L 13000:127.0.0.1:13000 <deploy-user>@<vm>
```

上线顺序：迁移数据库；部署 `FEISHU_ENABLED=false` 的新版本；创建应用和专用群并注入密钥；开启 staging 飞书；完成 live 验收。轮换 App Secret、Encrypt Key、Verification Token 或供应商密钥后重启相应组件，并在审计中保存轮换时间，不保存旧值。未来迁移到云密钥系统时保持环境变量注入契约。

## 5. 验证矩阵

默认质量门完全离线：Profile 配置、签名/AES、Schema、客户端 token/限流/错误脱敏、数据库迁移、回调防重放、群/私聊消息与收件人绑定、决策与私聊终态更新共享事务、私聊失败隔离、Outbox 乱序保护、Web API 和既有 Demo/E2E。

供应商 live smoke 必须显式开启并只使用合成数据：

```bash
ALERT_SAGE_RUN_VENDOR_LIVE_TESTS=1 uv run pytest -q tests/test_vendor_live.py
```

飞书 live 验收在专用群人工完成：新告警通知、手动启动、刷新/Worker 重启恢复、批准、驳回、重新分析、私聊提醒终态收敛、旧私聊按钮失效、第四次重新分析拒绝、失败重试、案例同步和最终通知。只记录构建 revision、脱敏运行 ID、时间与结果，不保存供应商正文或凭据。

本阶段实际 live 结果见 [真实运行与飞书阶段验收记录](11-real-profile-feishu-acceptance.md)。其中人工 live 验证与自动化契约覆盖分别记录，未将仅由自动化测试覆盖的边界误报为人工验证。

相关协议：[飞书回调安全](https://open.feishu.cn/document/event-subscription-guide/callback-subscription/receive-and-handle-callbacks?lang=zh-CN)、[卡片 JSON 2.0 回调字段](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/input)、[tenant token 选择](https://open.feishu.cn/document/faq/trouble-shooting/how-to-choose-which-type-of-token-to-use?lang=zh-CN)、[消息卡片 API](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/introduction)。
