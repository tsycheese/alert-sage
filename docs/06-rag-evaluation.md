# RAG 评测设计

## 1. 目标与边界

V2.6 的目标不是增加另一个“看起来能搜索”的页面，而是用可复现数据回答以下问题：

- 相关案例是否进入 TopK。
- 第一个相关案例排在什么位置。
- 多个相关案例是否被完整召回。
- 知识库没有答案时，阈值能否避免错误命中。
- 检索供应商失败是否被单独统计，而不是伪装成正确拒答。
- 不同 TopK 和分数阈值相对固定基线是提升还是退化。

V2.6A 建立版本化评测契约、固定评测集和确定性指标计算器。V2.6B 增加独立语料发布、异步运行、结果持久化与查询 API；V2.6C 增加 Web 对比报告。核心告警工作流不依赖评测功能。

第一阶段不使用 LLM-as-a-Judge。LLM 评分存在费用、波动和自我偏好，不能替代有明确相关性标签的检索评测。`expected_answer_points` 先作为后续回答与引用评测的事实标签，V2.6A 不对其自动打分。

## 2. 方案选择

评测集采用仓库内严格 JSON，而不是数据库编辑或 YAML：

1. 数据库作为唯一来源便于在线编辑，但内容变化难以随代码审查和复现。
2. YAML 可读性较好，但需要新增解析依赖，并需额外防护隐式类型和不安全构造。
3. 严格 JSON 可由 Pydantic 直接校验，不增加依赖，原始文件可计算 SHA-256 并随 Git 版本化。

选择方案 3。后续 PostgreSQL 只保存评测运行、参数、文件哈希和结果，不取代仓库中的评测集事实来源。

## 3. 版本化评测集

首个评测集位于 `backend/evaluation_sets/rag-v2.6-baseline.json`，Schema 版本为 `1.0`，数据版本为 `1.0.0`，SHA-256 为：

```text
f9ad6fa91c0c231985884081d1427dffeb85f32cb352268c865fa3b3e9ae02a6
```

它包含 6 份确定性运维案例和 20 个中英双语问题：

- checkout 数据库查询回归与高 CPU。
- payment 内存泄漏与 OOM。
- inventory Redis 连接池耗尽。
- order 下游超时放大。
- authentication TLS 证书过期。
- notification Worker 队列积压。

问题覆盖直接描述、同义表达、噪声信息、间接症状、跨服务多文档问题和语料外问题。每个文档使用稳定案例 UUID 作为供应商无关标识；相关性不绑定 Dify 文档 ID、片段 ID 或向量分数。

### 3.1 数据契约

文档字段：

- `key`：便于阅读的稳定逻辑键。
- `case_id`：文档级相关性和未来语料发布的稳定 UUID。
- `title`、`symptom`、`root_cause`、`resolution`：可发布的结构化案例内容。
- `tags`：固定分类标签。

问题字段：

- `id`：稳定问题键。
- `split`：`calibration` 或 `test`。
- `query`：发送给统一 `KnowledgeRetriever` 的原始问题。
- `relevant_case_ids`：文档级标准答案，可包含多个相关案例。
- `expected_answer_points`：后续回答正确率评测使用的事实要点。
- `tags`、`difficulty`：切片分析字段。
- `should_abstain`：语料中是否应当没有答案。

严格 Schema 禁止额外字段、空文本、重复标签、重复文档/问题 ID、未知案例引用，以及“应拒答但又声明相关文档”等矛盾标签。加载器限制文件大小、要求 UTF-8、拒绝重复 JSON key，并返回原始文件哈希。

## 4. Calibration 与 Test 隔离

当前 12 个问题属于 calibration，8 个问题属于 test。TopK、分数阈值和未来 rerank 参数只能依据 calibration 调整；test 只用于候选方案的最终比较。

若查看 test 结果后继续修改参数，本次 test 结果必须视为已污染。需要提升评测集版本并补充新的保留问题，不能继续把同一组结果称为独立泛化验证。

## 5. 指标定义

所有排名指标先按案例 UUID 去重，再截取 TopK，避免同一文档的多个片段占据多个名次。

- `source_hit_rate_at_k`：有答案问题中，TopK 至少命中一份相关文档的比例。
- `recall_at_k`：每个有答案问题召回的相关文档数除以全部相关文档数，再对问题取平均。
- `mean_reciprocal_rank`：第一个相关文档排名倒数的平均值；未命中记为 0。
- `abstention_accuracy`：无答案问题中，成功返回空结果的比例；供应商错误不能算正确拒答。
- `no_answer_false_positive_rate`：检索成功的无答案问题中，错误返回文档的比例。
- `error_rate`：供应商错误问题数占全部问题的比例。
- `latency_p50_ms`、`latency_p95_ms`：包含成功和失败调用的端到端检索耗时。

有答案问题发生供应商错误时按未命中计入 Recall、来源命中率和 MRR，同时进入错误率。无答案问题发生供应商错误时不计为错误命中，但也不计为正确拒答。

## 6. 阶段成功标准

V2.6A 完成标准：

- 固定评测集能通过严格 Schema 和哈希校验。
- 指标计算不依赖 Dify 或 DeepSeek，专项测试可离线确定性运行。
- 覆盖多相关文档、重复片段、无答案、供应商错误、缺失/未知/重复观测。
- calibration 与 test 分别输出聚合结果。

V2.6B 首次 Dify 基线的暂定 test 目标：

- Top3 来源命中率不低于 80%。
- MRR@3 不低于 0.65。
- 供应商错误率为 0。
- 无答案正确率不低于 50%，同时报告误命中率。
- P50/P95 延迟必须记录，但第一次 Cloud 基线不设硬阈值。

阈值在首次 test 运行前固定。当前样本较小，结果只能用于工程回归和方案对比，不能宣称具有统计代表性。

## 7. V2.6B 运行设计

V2.6B 采用以下实现：

- 使用固定案例 UUID 将 6 份评测文档发布到独立 Dify 评测数据集，避免污染业务知识库。
- 新增 `rag_evaluation_runs` 和 `rag_evaluation_results`，保存数据版本、Git revision、文件哈希、检索参数、逐题结果和汇总指标。
- 为 Outbox 增加严格的 `rag.evaluation.run` 主题，通过 Celery 异步执行，不让 HTTP 请求等待 Cloud 检索。
- calibration 可直接创建；test 运行需要 `confirm_test_set=true`，避免无意反复查看保留集。
- `POST /api/v1/rag/evaluations/runs` 创建或幂等返回运行，列表和详情 API 提供汇总与逐题结果。
- 评测集文件在排队和执行之间发生变化时运行失败，避免把新语料结果错误归到旧哈希。
- 单题超时或 Dify 错误记录为脱敏错误码并计入 `error_rate`，不会伪装为正确拒答。
- Cloud 评测查询按可配置间隔顺序执行，避免批量调用配额污染质量指标；业务检索不节流。
- Worker 只保存归一化文档标识、排名、分数和来源，不持久化检索片段正文。

两表方案、独立 Dataset 和异步边界的选择记录在 ADR 0011。Web 第一版仍只读展示运行列表、核心指标、参数差异和失败问题，不建设在线评测集编辑器。

## 8. API 与状态语义

```text
POST /api/v1/rag/evaluations/runs
GET  /api/v1/rag/evaluations/runs
GET  /api/v1/rag/evaluations/runs/{run_id}
```

创建响应的 `dispatched=true` 只表示本次事务新建了 Outbox 投递意图，不表示 Worker 已开始或完成。运行状态以 PostgreSQL 为准：`queued`、`running`、`completed`、`failed`。同一运行重复消费不会重复执行已完成结果；失败任务可由 Celery 重试，`attempt` 记录实际执行次数。

真实基线验收顺序：

1. 向独立评测 Dataset 发布/刷新 6 份固定文档并等待索引完成。
2. 运行 calibration Top3 基线，检查错误率、命中、拒答和延迟。
3. 只依据 calibration 决定是否调整分数阈值或 TopK。
4. 固定参数和本节成功阈值后，显式确认并运行一次 test。
5. 将运行 ID、评测集哈希、构建 revision 和结果写入本节验收记录。

## 9. 2026-07-26 Dify Cloud 基线

固定评测集 `1.0.0` 的 6 份文档已发布到独立 Dataset，文件 SHA-256 为 `f9ad6fa91c0c231985884081d1427dffeb85f32cb352268c865fa3b3e9ae02a6`。

首次无节流 calibration 运行 `15aa801d-d613-4b24-8219-8f95d08f6fbc` 的前 10 题成功，第 11、12 题被 Cloud 以 403 拒绝；稍后单独重试同一问题成功，运行证据表明短时批量配额而非凭据失效。评测 Worker 因此增加 6.5 秒查询间隔。节流后无阈值运行 `5ebf1360-0d53-4244-ae82-250c04cee4e4` 的错误率降为 0，但两条无答案问题均误命中。

calibration 的无答案 Top1 最高分为 0.2537，有答案最低分为 0.4727。选择阈值 0.40，在两组之间保留双向余量。确认运行 `75f4fff5-9e4f-46df-8e86-1d679e318da2` 的结果：

| 参数/指标 | 结果 |
| --- | ---: |
| split / TopK / threshold | calibration / 3 / 0.40 |
| source hit rate / Recall@3 / MRR@3 | 1.0000 / 1.0000 / 1.0000 |
| abstention accuracy / false positive rate | 1.0000 / 0.0000 |
| provider error rate | 0.0000 |
| P50 / P95 | 673 ms / 2028 ms |

固定参数和本页目标后，只执行一次 test。运行 `3685a5d7-656d-4c28-8cd7-d1e275a839ad` 的结果：

| 参数/指标 | 结果 |
| --- | ---: |
| split / TopK / threshold | test / 3 / 0.40 |
| source hit rate / Recall@3 / MRR@3 | 1.0000 / 1.0000 / 0.9167 |
| abstention accuracy / false positive rate | 1.0000 / 0.0000 |
| provider error rate | 0.0000 |
| P50 / P95 | 938 ms / 2128 ms |

6 个有答案 test 问题中 5 个相关案例排名第 1，`test-checkout-vague` 排名第 2；两条无答案问题均返回空结果。结果超过预先固定的 80% Top3 命中、0.65 MRR、0 错误和 50% 拒答准确率目标，但样本规模仍只适合工程回归。

本次运行未注入 `ALERT_SAGE_BUILD_REVISION`，数据库中的构建 revision 为空；运行 ID、参数和评测集哈希完整保存。后续基线必须注入提交 SHA，且本次 test 结果不得继续用于调参。
