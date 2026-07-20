# ADR 0007：DeepSeek 作为可替换诊断模型

- 状态：已接受
- 日期：2026-07-20

## 背景

V1.3B 使用确定性 `MockDiagnosticModel` 验证 LangGraph、结构化报告和人工中断恢复。V2.2 需要接入真实模型，但不能让 DeepSeek 的请求格式、错误响应或密钥扩散到工作流领域代码，也不能允许模型自由文本直接决定证据、状态流转或执行有副作用的操作。

## 方案比较

1. 使用供应商 SDK：接入代码较少，但会增加主要依赖，供应商类型容易进入领域层，升级和替换成本较高。
2. 在 LangGraph 节点中直接调用 HTTP API：短期文件少，但鉴权、重试、输出修复和脱敏逻辑会污染节点，难以合约测试。
3. 保留 `DiagnosticModel` 协议，实现通用 OpenAI-compatible HTTP 适配器：需要维护少量协议映射，但 Mock 与真实模型共享调用方，供应商边界清晰，也无需新增运行时依赖。

## 决策

选择方案 3，并在工厂中把 `deepseek` provider 映射到 `OpenAICompatibleDiagnosticModel`。V2.2 默认模型为 `deepseek-v4-flash`，模型名与 Base URL 均通过环境变量配置；离线开发仍默认使用 `mock`。

- 复用现有 `httpx`，不新增 DeepSeek SDK。
- 诊断和建议分为两次 JSON Mode 调用，各自携带明确 JSON Schema 和示例。
- 供应商响应先通过外部响应 Schema，再通过严格业务输出 Schema；无效输出最多进行一次有成本的修复调用。
- 网络超时、传输错误、`429` 和 `5xx` 使用有限指数退避；鉴权和其他 `4xx` 不重试。
- 证据内容由 Alert Sage 根据成功的上下文快照确定，模型只能引用允许的证据 ID，不能创建证据或来源。
- 模型名与 Prompt 版本由可信配置写入，不采信供应商响应中的同名字段。
- 告警 payload、操作员反馈和单条证据设置字符上限，截断时保留显式标记，控制提示词膨胀和意外费用。
- API Key 使用 `SecretStr`，只在工厂创建适配器时解包；错误不包含响应正文、请求正文或密钥。
- 建议只作为人工决策输入。重启、扩缩容、发布、写操作和服务器命令不得由模型自动执行。

## 数据边界

启用 DeepSeek 后，Worker 会把选定的告警字段、分类结果、指标/日志/CMDB 上下文、Dify 检索片段和可选人工反馈发送给模型供应商。不得把密钥、本机文件、数据库连接串或未选择的业务字段加入请求。真实运维数据接入前，需要补充脱敏策略、数据分级和供应商合规评审。

## 影响

收益是核心工作流保持供应商无关，真实模型输出具备可验证的证据引用、版本和失败语义，并可继续离线测试。代价是每次完整诊断通常产生两次付费调用，一次无效输出可能额外产生一次修复调用；JSON Mode 仍不能替代业务 Schema 校验。

官方协议参考：

- <https://api-docs.deepseek.com/api/create-chat-completion/>
- <https://api-docs.deepseek.com/zh-cn/guides/json_mode/>
- <https://api-docs.deepseek.com/quick_start/pricing/>
