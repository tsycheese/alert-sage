# ADR 0012：显式运行 Profile 与组件级密钥隔离

- 状态：已接受
- 日期：2026-08-09

## 背景

历史配置以 `environment` 和两个默认值为 `mock` 的供应商字段决定运行方式。这个设计便于演示，但真实部署漏配密钥时可能启动 Mock，API、Worker 和 Relay 也可能接收到并不需要的密钥。目标是保留离线演示与测试，同时让真实运行绝不静默降级。

## 方案比较

1. 删除 Mock：边界最简单，但会破坏自动化测试、离线求职演示和故障注入。
2. 继续按 `environment` 推断：改动小，但日志环境和供应商边界耦合，漏配仍可能误用 Mock。
3. 无默认 `real/demo/test` Profile，加组件角色校验：配置更严格，需要修改所有入口，但错误会在启动时暴露，并能最小化每个进程接收的密钥。

选择方案 3。

## 决策

- `ALERT_SAGE_RUNTIME_PROFILE` 和 `ALERT_SAGE_COMPONENT_ROLE` 均为必填项。
- `real` 必须声明 `dify + deepseek`；Worker 必须持有两者密钥，API 只持有其知识查询所需的 Dify 密钥，Relay 不持有供应商或飞书密钥。
- `demo/test` 必须声明 `mock + mock`，拒绝云端密钥和飞书开关；受控工具故障注入只允许 `demo`。
- `environment` 继续控制日志/debug 语义，不再选择供应商。
- 飞书只在 `real` 中通过 `ALERT_SAGE_FEISHU_ENABLED=true` 显式开启，并按 API/Worker 角色校验各自所需字段。
- readiness 只检查本地配置、PostgreSQL 和 Redis，不同步探测 DeepSeek、Dify 或飞书。

## 结果

漏配 Profile、真实模式使用 Mock/混合供应商、离线模式携带云密钥都会启动失败。错误只列缺失的环境变量名，不回显值。Mock 仍是测试和离线演示的一等适配器，但不能出现在真实 Profile。
