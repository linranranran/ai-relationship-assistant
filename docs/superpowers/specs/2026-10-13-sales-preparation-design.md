# 销售沟通准备：第一版设计

> 范围已调整（2026-10-13）：用户选择模拟客户聊天导入，并收敛为面试 MVP。本文件保留作旧方案参考；当前实施以[模拟聊天 MVP 设计](2026-10-13-sales-chat-evidence-mvp-design.md)及其计划为准。

更新日期：2026-10-13。本文描述待开发功能，不代表已经实现。

## 1. 目的与成功标准

销售联系一名客户前，能够用一份准备单回顾客户背景、近期沟通、过去的承诺、需要确认的问题和建议开场，并能查看事实的来源。

演示主线：选择已有客户 → 录入沟通纪要 → 问“明天联系杨鑫，帮我准备一下” → 同名时澄清 → 生成准备单 → 点击引用查看原文 → 刷新后仍可查看。

业务收益先验证“准备是否省时、是否遗漏重要信息、来源是否支持结论”。不宣称提升成交率或让新手达到成熟销售水平。

## 2. 第一版范围

- 每次为一名客户生成准备单；复用现有 owner 下的 Person，不创建新的账户或跨用户人物合并机制。
- 第一版接入最新 Direct；Plan / Discovery 保留现有功能，本阶段不承诺它们支持销售准备。实施演示时明确选择 Direct 配置；其他模式不开放前端沟通准备入口。
- 沟通数据先支持一种来源：销售手动录入的纪要 `sales_note`。它代表销售的记录，不代表独立核实过的客户原话。
- 日期使用带时区的时间戳；“周五”等相对时间在纪要中保留原文，不能猜测精确到期日。
- 准备模块只读客户业务数据并生成建议；纪要保存来自用户明确提交的表单。
- 第一版不生成跟进任务、不定时提醒、不发送客户消息、不接企业微信、不建产品知识库、不增加 Supervisor 或独立子 Agent。
- 记录不可编辑或删除；更正通过新增说明记录保留原始证据。旧、新记录冲突时展示不确定性，不能仅因新记录时间较晚就覆盖旧事实。
- 查询不到客户时交给现有澄清/查询处理，不能为“准备沟通”自动新增人物。

## 3. 实现边界

采用独立 `backend/sales/` 业务模块，复用现有 PostgreSQL、Neo4j、Pydantic、`invoke_structured`、请求预算、检查点、SSE 和 Langfuse。

```text
Direct 查找并确认 person_id
  → get_sales_preparation_context（只读业务 Tool）
  → Direct 根据工具反馈继续或结束
  → generate_response 的销售分支
  → invoke_structured 生成准备单
  → 引用与人物范围校验
  → 确定性渲染可阅读文本与结构化卡片
  → finalize_memory 统一保存整轮问答
```

Tool 只取数据，不在业务 Tool 里调用模型。模型生成放在回复节点内，以沿用图的预算停止、结构化修复和审计边界；避免普通 Tool 异常包装吞掉 `BudgetExceeded` 等停止信号。

生成完结构化准备单后直接渲染，不再调用另一个模型润色。旧的非销售回复分支继续使用现有流式文本逻辑。

Sales 模块可以在以后封装为子图，但本阶段没有独立 Agent 记忆或第二套执行器。

## 4. 数据与接口契约

### 4.1 客户沟通原始记录

新增 PostgreSQL 表 `customer_communication_record`：

| 字段 | 用途 |
|---|---|
| `owner_id` | 服务端鉴权用户，不能由浏览器指定 |
| `record_id` | UUID；来自录入接口的 Idempotency-Key |
| `person_id` | 已验证属于当前 owner 的 Person |
| `source_type` | 第一版固定 `sales_note` |
| `occurred_at` | 沟通发生时间，保存为 TIMESTAMPTZ |
| `content` | 原始纪要，1～8000 字符，不能让模型先改写 |
| `content_hash` | 人物、来源、时间和原文组成的规范化请求摘要 |
| `created_at` | 实际录入时间 |

主键 `(owner_id, record_id)`；索引 `(owner_id, person_id, occurred_at DESC, record_id DESC)`。

同一个 record_id、同一内容重复提交返回原记录；同一个 record_id 换内容返回 409。相同文本使用不同 record_id 允许保存，不能按句子内容删除真实重复沟通。

Person 在 Neo4j 中，不能声称 PG 外键保证跨库关联。创建纪要前及生成准备单前通过已有查询检查 owner 与人物；人物已删除时禁止生成新准备单，历史纪要仍按 owner 保留供引用核对。

`conversation_message` 继续记录用户与 Agent 的交互；不能拿它代替销售与客户的业务沟通记录。

新增接口：

- `GET /api/sales/customers?query=...`：只搜索当前用户的档案，最多 10 个候选，同名由用户选择。
- `POST /api/sales/communications`：提交 `person_id`、`occurred_at`、`content`；Header `Idempotency-Key` 为 UUID。
- `GET /api/sales/communications?person_id=...`：返回该用户该人物最近 20 条记录。
- `GET /api/sales/communications/{record_id}`：返回属于当前用户的原文；跨用户与不存在均返回 404。
- `GET /api/sales/capabilities`：返回 `preparation_available`，第一版只在 Direct 模式为 true；供前端决定是否展示准备入口。

录入接口的 record_id 与 Agent 提问的 request_id 独立，各标识不同业务动作，不能相互替代。

### 4.2 准备上下文

`get_sales_preparation_context(person_id, meeting_goal)` 只接受这两个模型业务参数；owner 从执行器注入。

返回 `SalesPreparationContext`：`person_id`、`meeting_goal`、`as_of`、`sources`、`coverage`。

- 来源包括客户档案快照与该客户的沟通纪要。
- 每个 `EvidenceSource` 有 `source_id`、`source_type`、`record_id`（档案来源为空）、`occurred_at`、`excerpt`、`truncated`。
- 档案字段生成独立来源标识，例如 `profile:<person_id>:occupation`；展示为“客户档案快照”，不能伪装成聊天原话。
- `as_of` 由服务端获取数据时固定，并随 Tool 结果保存。恢复同一步复用该快照，新的请求才能获取更新上下文。
- 先按 owner、person_id、occurred_at ≤ as_of 过滤，再取最近候选。本版不加向量索引。
- 初始配置：`SALES_PREP_CANDIDATE_LIMIT=50`、`SALES_PREP_RECORD_LIMIT=12`、`SALES_PREP_EVIDENCE_TOKEN_BUDGET=3000`。最终还受现有整个模型输入预算限制；这些是初始工程上限，不是经过实测的最优值。
- 超长原文只提供明确标记的 excerpt；保留来源定位，模型不得引用没有提供给它的其他内容。
- `coverage` 记录候选数、选中 source_id、覆盖时间范围、裁剪情况和证据 Token 估算。有限检索不能支持“全部历史中从未发生”的结论。
- 当前请求读取上下文后又成功修改了相关人物，禁止发布旧快照生成的准备单；固定提示重新生成，保留已成功的修改回执。第一版不新增自动重查循环。
- as_of 是检索截点与准备时点，档案是本次读取的快照；不提供历史档案回溯，也不声称跨 Neo4j/PG 的事务级一致快照。

### 4.3 输出模型

`SalesPreparationBrief`：

| 字段 | 内容 |
|---|---|
| `person_id` | 对应已验证客户 |
| `customer_summary` | 客户背景事实 |
| `recent_communications` | 近期沟通要点 |
| `customer_needs` | 记录中的需求 |
| `previous_commitments` | 曾记录的承诺及状态依据 |
| `suggested_questions` | 本次建议询问的问题 |
| `suggested_opening` | 一段建议开场白，可为空 |
| `missing_information` | 当前提供资料中的信息缺口 |
| `limitations` | 没有记录、裁剪、冲突或时间不明确等限制 |

`EvidenceRef` 为 `source_id + quote`；事实项必须有至少一个引用。承诺项 `completion_state` 只允许 `OPEN / DONE / UNKNOWN`：OPEN 或 DONE 必须有支持该状态的记录；只记录“我答应发方案”却没有后续状态时，使用 UNKNOWN，不能推断“仍未完成”。

建议项明确是建议，不写成已发生事实；一般性提问可以没有引用，个性化建议应附依据。未知产品能力不能承诺“我方产品可以满足需求”。

`SalesPreparationResult` 由后端组装 `brief`、实际引用的 `sources`、`as_of`、`meeting_goal`、`coverage`、`schema_version` 与 `prompt_version`；模型不能自行提供来源正文或覆盖服务端元数据。

引用校验：人物一致、source_id 属于本次已授权提供的来源、quote 非空且确实出现在对应 excerpt 中。它只能证明引用合法及原文存在，不能证明结论在语义上成立；建议与事实语义仍需人工评测。

## 5. 保存、流式与错误行为

- 不新增准备单仓储；结构化结果进入图状态 `sales_preparation` 及 `ChatResponse.sales_preparation`，复用 `agent_request.response` JSONB 保存。
- 每次新 Agent 请求初始化 `sales_preparation=None`，防止旧准备单出现在下一条回复。
- 历史助手消息从同 owner、同 request_id 的保存响应补充结构化准备单；没有该字段的旧消息正常显示。
- 准备单是派生分析，不自动回写 Neo4j 人物事实、已有 memory_fact 或跟进任务。
- 销售生成仅使用当前用户提问/澄清和已装配的证据；已有会话记忆用于辅助人物解析，不能在没有对应来源时提供准备单事实。
- 复用 `tool.started/finished` 和 `strategy.deciding` 等现有进度；新增中文阶段说明通过现有事件类型传递，不再搭建另一套 SSE。
- 结构化生成期间只展示进度。校验通过后才发布最终文本/卡片，不把未校验 JSON 分块发给用户。
- `invoke_structured` 原有最多两次尝试即为生成/修复上限，消耗同一请求预算；不增加外层无限重试。
- 无沟通记录：展示已有档案、一般性准备问题和“当前检索范围没有沟通记录”，不能编造需求或承诺。
- 引用两次失败：不给用户展示不可信准备单，输出固定失败说明；预算耗尽走现有预算收尾；普通读取故障按现有工具错误机制处理。
- 数据库同步读写进入 FastAPI 线程池；不能在 async/SSE 中直接阻塞等待数据库。
- 新 Tool 会改变目录哈希；升级前完成或停止待处理请求，不能为了恢复旧任务绕过目录兼容校验。

## 6. 验收范围

至少覆盖：正常准备、同名澄清、没有记录、承诺状态未知、记录中的他人信息、相互矛盾的记录、伪造引用、提示注入、长纪要、跨用户访问、HTTP 重试与 SSE 重连、生成期间修改资料、重启恢复、非销售任务回归。

前两天准备不少于 20 个模拟案例，注明每例应引用的原文、不能得出的结论与期望行为。首版用界面/API与现有回归检查验收，遵循用户“不新增测试代码”的偏好。

先记录基线：准备耗时、任务完成情况、引用是否合法及是否支持结论、重要信息遗漏、人工建议可用性、模型调用数和 Token 用量。固定模拟集通过后，再请销售人员用脱敏案例评价；不以模型自报满意度代替业务评测。

## 7. 后续演进

实际验证第一版后，依次评估：Plan/Discovery 销售目标适配、跟进任务闭环、固定聊天导入格式、产品知识检索、销售业务子图与多 Agent。

依据：[原项目方向](../../PROJECT_DIRECTION.md)及 2026-10-13 对话中确认的产品场景。本版主动缩小原路线，不包含常见亲属称谓和自动联系客户。
