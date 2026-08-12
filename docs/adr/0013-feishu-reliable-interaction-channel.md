# ADR 0013：飞书作为可靠交互渠道而非业务事实来源

- 状态：已接受
- 日期：2026-08-09

## 背景

Web 已能启动诊断和提交人工决策，但值班人员常在告警群中协作。飞书需要提供通知、状态和操作入口，同时不能绕过 PostgreSQL 业务事实、LangGraph 状态机、Outbox 或现有 Web 兜底。当前系统也尚未具备统一认证/RBAC。

## 方案比较

1. 群消息解析并在 Bot 内维护状态：交互直观，但文本歧义大，会形成第二事实来源并要求群消息读取权限。
2. 飞书直接调用工作流/供应商：实现短，但绕过事务、幂等、权限和恢复边界。
3. 外部结构化告警仍进入 Alert Sage，飞书只承载版本化共享卡片与人工命令：权限最小、可复用现有事务核心，失败也不改变告警事实。

选择方案 3，使用企业自建应用、卡片 JSON 2.0 和 `card.action.trigger`。

## 决策

- 只向配置的单一群推送 `warning/critical` 且来源命中白名单的新告警；不解析群消息、不补发历史告警。
- 业务回调先将 `X-Lark-Request-Timestamp` 作为不透明字符串按原始请求体验证 `X-Lark-Signature`，再以 AES-256-CBC 解密；限制 256 KiB，并用已签名、已加密正文的 `header.create_time` 执行默认 ±5 分钟时间窗（兼容秒、毫秒、微秒和纳秒纪元格式），同时校验 App ID、Verification Token、tenant、chat 和严格 Schema。飞书 URL challenge 是协议例外：平台可能不发送签名头，此时仅允许解密后的 `url_verification` 严格 Schema 通过，并以常量时间校验 Verification Token；它不能产生业务副作用。若 challenge 携带签名头则仍校验签名。
- `(app_id, event_id)` 唯一；审计只保存标准化动作、open_id、群/消息绑定、原文 SHA-256、结果和脱敏错误，不保存解密正文。
- 新版回调的临时 `event.token` 只在适配器边界完成 Schema 校验，不持久化或记录；卡片异步同步继续通过应用身份 API 完成。
- 卡片动作只携带 binding、action、revision 和随机 nonce。数据库保存当前 nonce 哈希；群回调精确匹配 binding 的 chat/message，私聊回调精确匹配成功投递的 message 与 recipient open_id；旧 revision、旧消息或旧 nonce 均返回卡片过期。
- 群成员可启动/重试。批准、驳回和重新分析只允许配置的 `open_id → 审计标签`；这是可审计白名单，不冒充完整 RBAC。
- 批准/驳回二次确认；重新分析反馈必填，API 和数据库限制 1,000 字符，每次运行最多三次。
- 共享卡片只在主要状态和案例同步状态变更时增加 revision。`feishu.card.sync` 与 `feishu.reminder.send` 通过事务性 Outbox 至少一次投递；旧 revision 消费者不覆盖新状态。
- Worker 每次从 PostgreSQL 读取最新告警、报告、决策和案例状态渲染卡片，不信任 Outbox 中的报告正文。
- 飞书 API 失败只更新渠道状态和脱敏错误码，不改变告警/工作流状态；共享卡片不可更新时发送替代卡片并废止旧 nonce。
- waiting 状态为白名单审批人发送幂等私有提醒；决策被接收后，以可空 `decision_id` 将终态更新 delivery 固定到对应 HumanDecision，更新原私聊消息为已处理并移除业务按钮。原消息不可更新时发送替代终态提醒；私聊失败不污染共享 binding。Web 保留完整详情、人工操作和故障兜底。

## 结果与边界

新增三张渠道表和两个 Outbox 主题；后续以加法迁移为 `feishu_deliveries` 增加可空 `decision_id`，无需回填旧行。第一阶段不申请群消息或通讯录权限，不实现多群/多租户、统一认证/RBAC、真实 Loki/Prometheus/CMDB，也不自动执行任何运维动作。
