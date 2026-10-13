# 销售沟通准备功能开发计划

> 范围已调整（2026-10-13）：不再实施本文件的手动纪要方案与 8 日安排。当前实施以[模拟客户聊天与销售沟通准备计划](2026-10-13-sales-chat-evidence-mvp-implementation.md)为准；本文件保留作旧方案参考。

> **For agentic workers:** 若后续由 Agent 实施，按 `superpowers:executing-plans` 逐项推进；用户选择委派执行时可使用 `superpowers:subagent-driven-development`。本次交付开发计划，不开始编码。

**Goal:** 销售在联系一名客户前，获得一份包含客户背景、沟通要点、承诺记录、建议和可点击来源的准备单。

**Architecture:** 沿用现有 Direct 工具循环，销售逻辑集中在独立业务模块。只读 Tool 提供受控证据，回复节点调用现有结构化输出服务生成准备单，校验后确定性渲染；共享身份、预算、幂等、检查点、审计和 SSE。

**Tech Stack:** 现有 Python / FastAPI / LangGraph / Pydantic / PostgreSQL / Neo4j / React / TDesign，不新增依赖或第二套 Agent 框架。

**Spec:** [第一版设计](../specs/2026-10-13-sales-preparation-design.md)。实施前同时阅读设计与本计划。

## 全局约束

- 一次准备单只针对一名已存在且属于当前用户的 Person。
- 第一版接入最新 Direct；Plan / Discovery 保留现有功能，本阶段不承诺它们支持销售准备。
- 沟通来源只支持销售手动纪要 `sales_note`，原文长度 1～8000 字符；不把销售记录当独立核实的客户原话。
- 初始证据上限：最近 50 条候选、最多 12 条入模、3000 Token；整个模型调用仍遵守已有输入/输出/安全预算。
- 纪要不可编辑或删除；更正追加新记录，不盲目覆盖旧事实。
- 准备单不自动修改客户档案、长期事实、待办或客户消息。
- 结构化生成最多两次尝试，复用 `invoke_structured`，不增加外层重试或二次模型润色。
- 原请求与检查点仍按现有 request_id/thread_id/interrupt_id 恢复；准备模块不另建执行身份。
- 沿用“不新增测试代码”的偏好：准备验收案例与数据，通过实际接口、界面和现有回归检查验证。
- 不自动 commit/push，不修改真实 `.env`，不清理真实数据。执行阶段新增迁移，不在制定计划时访问业务库或模型。

## 需要重点审阅的五类输入

1. 一条客户纪要提到另一个人：不能把另一个人的爱好、职位归给目标客户。
2. 没找到完成记录：承诺状态应为 UNKNOWN，不能报告为确定未完成。
3. 引用确实存在，但不支持结论：必须人工评估语义，不能用字符串校验宣称事实完全正确。
4. 超长或恶意纪要：裁剪必须可见；纪要中的“忽略规则、修改其他客户”不能成为操作指令。
5. 请求重试、同名、跨用户、读取后修改资料：保持身份与快照边界，不能重复录入或发布旧准备单。

这些场景分别纳入第 1、4、5、6、8 天的验收步骤。

## 工期与实施方式

按每天 4～6 小时估算，安排 **8 个开发日，另留 2 个缓冲日**。这是根据当前代码的工作量估算；第 2 天数据链路完成后重估，不承诺固定日历完成日期。

建议你实现后端销售业务逻辑；助手按当天任务提供骨架、关键注释和审阅。前端仍可交给助手处理，但后续以你的执行指令为准。本计划不自动开始编码。

| 开发日 | 交付物 |
|---|---|
| 第 1 天 | 数据与输出契约、20 个模拟案例 |
| 第 2 天 | PG 纪要表、仓储、接口、录入幂等与归属校验 |
| 第 3 天 | 客户选择与沟通纪要录入界面 |
| 第 4 天 | 客户上下文检索、来源定位、Token 裁剪 |
| 第 5 天 | 结构化准备单生成与引用校验 |
| 第 6 天 | 接入 Direct、回复收尾、预算与错误边界 |
| 第 7 天 | 准备单卡片、引用原文、历史恢复 |
| 第 8 天 | 全流程验收、业务评估、五分钟演示 |
| 缓冲日 | 修复实际失败和销售反馈；不顺带扩大功能范围 |

## 文件职责

以下“新增”均为待创建，不代表已经存在。

| 文件 | 动作与职责 |
|---|---|
| `backend/sales/__init__.py` | 新增业务模块入口 |
| `backend/sales/contracts.py` | 新增纪要、证据、上下文和准备单 Pydantic 模型 |
| `backend/sales/context.py` | 新增人物归属核对、检索范围与上下文装配 |
| `backend/sales/evidence.py` | 新增来源引用、人物范围与快照时序校验 |
| `backend/sales/preparation.py` | 新增提示词、结构化生成和确定性文本渲染 |
| `backend/db/customer_communication_store.py` | 新增纪要持久化与带 owner 的查询 |
| `backend/db/migrations/012_customer_communication_record.sql` | 新增表和索引，不修改原业务数据 |
| `backend/routers/sales.py` | 新增客户搜索、纪要接口和能力可用性查询 |
| `backend/tools/sales.py` | 新增只读上下文 Tool，不调用模型 |
| `frontend/src/api/sales.js` | 新增销售接口封装 |
| `frontend/src/sales/CommunicationRecordPanel.jsx` | 新增客户选择、纪要录入与历史列表 |
| `frontend/src/sales/SalesPreparationCard.jsx` | 新增准备单和证据展示 |
| `docs/evaluations/sales-preparation/cases.json` | 待新增模拟验收数据，不是测试代码 |
| `docs/evaluations/sales-preparation/acceptance.md` | 待新增实际验收记录、失败案例和业务评价 |

现有文件的具体修改列在各日任务中。不要把全部新逻辑塞进 `generate_response.py` 或 `Chat.jsx`。

## 第 1 天：先定义真实业务数据和输出

**Files:** 新增 `backend/sales/__init__.py`、`backend/sales/contracts.py`、`docs/evaluations/sales-preparation/cases.json`。

**Interfaces:** 定义后续所有任务共同使用的模型，继承项目现有 `Contract`/严格请求风格，禁止额外字段。

- `CommunicationRecordCreate(person_id, occurred_at, content)`：时间必须带时区；source_type 固定由服务端设置。
- `CommunicationRecord(record_id, person_id, source_type, occurred_at, content, created_at)`：对外不包含 owner。
- `EvidenceSource(source_id, source_type, record_id, occurred_at, excerpt, truncated)`。
- `EvidenceRef(source_id, quote)`。
- `FactItem(text, evidence_refs)`；`CommitmentItem(text, evidence_refs, completion_state)`。
- `RecommendationItem(text, basis_refs)`：允许一般建议无引用，不能伪装成已确认事实。
- `SalesPreparationContext(person_id, meeting_goal, as_of, sources, coverage)`。
- `SalesPreparationBrief` 与 `SalesPreparationResult`：字段以设计文档 4.3 为准；来源和版本元数据由后端组装。

- [ ] 为每个模型写“表示什么、谁提供、能否信任”的中文注释；事实项须有非空引用；状态只允许 OPEN/DONE/UNKNOWN。
- [ ] 建立至少 20 个模拟案例，每例记录输入、可见纪要、应引用的原文、禁止推断的结论和预期行为。
- [ ] 至少包含两个用户、两个同名客户、一条提到其他人的纪要、一条承诺无后续、一组矛盾记录、一条提示注入和一条超长纪要。
- [ ] 用临时调试调用验证合法/非法模型输入，无需创建测试文件；用 `python -m json.tool docs/evaluations/sales-preparation/cases.json` 检查案例格式。

**当天完成标准：** 你能清楚解释纪要、准备上下文、事实引用、建议之间的区别，模型不能直接提供 owner 或来源正文。

## 第 2 天：保存和查询沟通原文

**Files:** 新增纪要 SQL、`backend/db/customer_communication_store.py`、`backend/routers/sales.py`；修改 `backend/main.py`、`backend/db/init_postgres.py`。

**Consumes:** 第 1 天的记录模型；现有 `get_current_user`、`get_postgres_connection` 和 Neo4j 人物查询。

**Produces:**

```python
save_record(*, owner_id: str, record_id: str, value: CommunicationRecordCreate) -> CommunicationRecord
list_records(*, owner_id: str, person_id: str, as_of: datetime, limit: int) -> list[CommunicationRecord]
get_record(*, owner_id: str, record_id: str) -> CommunicationRecord | None
ensure_communication_schema() -> None
```

- [ ] 实现设计中的表、主键和索引；迁移幂等，加入现有初始化和表检查。
- [ ] `save_record` 用短事务和 `(owner_id, record_id)` 保证重复提交复用；摘要覆盖 person_id、source_type、标准化时间和原文，同键不同内容抛出仓储内定义的 `CommunicationRecordConflict(ValueError)`，由路由映射为 409。
- [ ] 在接口层验证人物存在且属于当前 owner；不能接受客户端 owner_id，也不能假装 PG 外键解决 Neo4j 关联。
- [ ] 实现设计 4.1 的五个接口；能力接口只在当前 Direct 模式开放准备入口；异步入口用线程池执行同步查询。
- [ ] 用 API 调试验证录入、重复提交、同键换文、客户不存在、跨用户引用、时间缺少时区及超过 8000 字符；记录实际响应。

**当天完成标准：** 不经过模型，也可以为已有客户可靠保存、读取原始纪要；同名需选择真实 ID。

## 第 3 天：让销售能录入数据

**Files:** 新增 `frontend/src/api/sales.js`、`frontend/src/sales/CommunicationRecordPanel.jsx`；修改 `frontend/src/pages/Chat.jsx`、`frontend/src/pages/Chat.css`。

**Consumes:** 第 2 天客户搜索和纪要接口。

**Produces:** `CommunicationRecordPanel`，负责当前客户选择、纪要表单、录入状态和最近记录；成功提交后刷新列表。

- [ ] 使用现有 API 客户端和 TDesign，实现按姓名搜索、同名候选选择、发生时间和原文输入。
- [ ] 提交前生成录入 UUID；超时重试复用该 UUID 与同一内容，不再次造新记录身份。
- [ ] 使用带时区时间戳；前端校验仅改善体验，后端仍为校验权威。
- [ ] 加入“为此客户准备沟通”入口，生成普通 Agent 提问，继续使用现有 request_id 和 SSE；能力不可用时显示明确说明。
- [ ] 在页面验证客户切换、同名、超时重试和退出后切换用户；不在浏览器缓存其他账号的客户纪要。

**当天完成标准：** 销售无需了解数据库和 Agent 内部机制即可录入一条沟通记录。

## 第 4 天：检索客户上下文并控制 Token

**Files:** 新增 `backend/sales/context.py`；修改 `backend/config.py`、`.env.example`。

**Consumes:** 第 2 天仓储与现有人物查询；现有 `get_memory_runtime().token_counter`。

**Produces:**

```python
load_sales_context(*, owner_id: str, person_id: str, meeting_goal: str, as_of: datetime) -> SalesPreparationContext
```

- [ ] 先核对人物归属，再获取档案快照与 as_of 之前的沟通记录；候选最多 50、入模最多 12。
- [ ] 为档案字段与纪要分别构造 source_id；保留原文 excerpt、时间和裁剪标记。
- [ ] 使用已有 Token 计数器；配置名为 `SALES_PREP_CANDIDATE_LIMIT=50`、`SALES_PREP_RECORD_LIMIT=12`、`SALES_PREP_EVIDENCE_TOKEN_BUDGET=3000`。避免把所有历史与整个会话摘要重复拼进提示词。
- [ ] 全文超预算时按记录/摘录裁剪并记录 coverage；校验引用时只能使用实际提供的 excerpt，不能引用模型没看到的原文。
- [ ] 调试两个同名客户、长纪要、未来发生时间、没有记录、提到另一个人的纪要；检查选中的 source_id 都属于当前用户及目标客户。

**当天完成标准：** 不调用模型，也可以打印一份边界清楚、预算可检查的准备上下文。

## 第 5 天：生成有依据的准备单

**Files:** 新增 `backend/sales/preparation.py`、`backend/sales/evidence.py`。

**Consumes:** `SalesPreparationContext`、`SalesPreparationBrief`、现有 `invoke_structured`。

**Produces:**

```python
generate_sales_brief(*, context: SalesPreparationContext, user_input: str) -> SalesPreparationResult
validate_sales_brief(brief: SalesPreparationBrief, context: SalesPreparationContext) -> None
render_sales_brief(result: SalesPreparationResult) -> str
```

- [ ] 提示词明确：只根据提供的资料组织事实、纪要是不可信指令数据、建议单独标识、不补造产品能力和客户属性。销售生成只使用当前提问/澄清和受控证据；会话记忆可辅助前面的指代解析，不能成为没有来源的准备单事实。
- [ ] 调用现有结构化输出，validator 检查 person_id、source_id 与 quote；服务端组装来源、as_of、schema_version=1、prompt_version=`sales-prep-v1`。
- [ ] 缺少后续状态的承诺使用 UNKNOWN；没有沟通记录时展示已有档案及一般问题，不制造个性化需求。
- [ ] 用固定模板把结构化结果渲染成文本，不再额外调用模型润色。
- [ ] 调试伪造来源、无引用事实、属于另一个人的信息、矛盾记录、提示注入；逐条人工判断引用是否真正支持结论。

**当天完成标准：** 已有“上下文 → 准备单”的独立业务能力；格式合法与语义正确的区别得到记录。

## 第 6 天：接入现有 Direct 链路

**Files:** 新增 `backend/tools/sales.py`；修改 `backend/tools/schema/tool_args.py`、`backend/tools/registry.py`、`backend/tools/definitions.py`、`backend/agent/strategies/catalog.py`、`backend/agent/strategies/direct.py`、`backend/agent/nodes/generate_response.py`、`backend/agent/state.py`、`backend/routers/chat.py`、`backend/models/schemas.py`、`backend/agent/streaming.py`、`backend/sales/evidence.py`。

**Consumes:** 第 4、5 天上下文与生成服务；现有 Direct 回执、公共执行器及统一收尾。

**Produces:**

```python
get_sales_preparation_context(**kwargs) -> dict
# 模型参数：person_id、meeting_goal；owner_id 仅从执行器注入。
select_sales_context(state: dict) -> SalesPreparationContext | None
```

- [ ] 注册只读 Tool 和参数类，返回统一 success/data；目录说明返回的是准备上下文，不宣称已经完成准备单生成。
- [ ] 保持先查找/澄清人物再使用可信 ID；沿用现有 person_id 校验，不让“准备沟通”触发自动新增人物。
- [ ] Direct 的提示词说明何时需要该 Tool；本版只支持一个客户，检测同请求涉及多个准备对象时明确要求分别发起。
- [ ] 在 `generate_response` 增加轻薄的销售分支，委托业务模块；其他任务继续原分支，不增加隐藏 goal 层。
- [ ] `select_sales_context` 从真实 SUCCESS 回执取得上下文；若读取后有影响该人物的成功修改，不发布旧准备单，提示重新生成并保留修改回执。
- [ ] 图状态与 ChatResponse 增加可选 `sales_preparation`；每次新请求重置为 None；复用原 agent_request JSONB，不新建准备单表。
- [ ] 复用现有事件类型展示“整理客户资料”“读取沟通记录”“生成沟通准备单”“核对引用来源”；结构化校验通过前不发送草稿 JSON。
- [ ] 保留 Direct 的 requirements_verified=false；引用通过不能升级为“整个用户需求已确定完成”。准备单不自动变成长期事实。
- [ ] 验证请求重试、预算耗尽、两次引用失败、读取后修改资料、非销售闲聊、原人物增改及确认恢复；目录哈希变化按现有兼容策略处理。

**当天完成标准：** 从正常 Agent 提问可得到准备单；新业务复用原可靠执行链，不出现第二套预算、确认或记忆收尾。

## 第 7 天：展示准备单、来源与历史

**Files:** 新增 `frontend/src/sales/SalesPreparationCard.jsx`；修改 `frontend/src/chat/ChatMessage.jsx`、`frontend/src/chat/messages.js`、`frontend/src/pages/Chat.jsx`、`frontend/src/pages/Chat.css`、`backend/models/schemas.py`、`backend/db/chat_history_store.py`、`backend/routers/chat.py`。

**Consumes:** `ChatResponse.sales_preparation` 与纪要原文接口。

**Produces:** `SalesPreparationCard({ result })`；历史助手消息携带可选 `sales_preparation`。

- [ ] 分区展示背景、沟通要点、承诺记录、建议、信息缺口；承诺状态未知明确显示“是否完成待确认”。
- [ ] 引用点击展示对应原文与发生时间；档案引用展示生成时的档案快照，不能拿后来改过的档案代替旧证据。
- [ ] SSE 最终回执更新结构化卡片；重连按现有 request_id/seq 去重，不拼接两份准备单。
- [ ] 历史查询按 owner_id + request_id 从 agent_request 保存响应补充卡片；旧历史没有该字段仍能显示。
- [ ] 刷新、重启恢复后检查卡片与引用；人物已删除时不生成新准备单，但该用户的历史来源仍能核对。

**当天完成标准：** 用户能核对每个事实的来源，刷新后不丢准备单、不激活旧交互。

## 第 8 天：验收、业务反馈与演示

**Files:** 新增 `docs/evaluations/sales-preparation/acceptance.md`；更新案例集与 README 的已实现范围。

- [ ] 运行不少于 20 个模拟案例，记录实际结果与失败，不只记录“模型有回复”。
- [ ] 跨用户原文/引用不可访问、重复提交不新增、未知状态不被当成已完成/未完成、伪造引用不展示；这些安全边界有一例失败就不作为可演示版本交付。
- [ ] 人工评估事实归属、引用支持性、重要信息遗漏和建议可用性；不能用 Schema 通过率代替业务正确率。
- [ ] 记录每例的模型/提示词版本、上下文截点、来源数、耗时、调用数和 Token；首次报告是基线，不编造优化百分比。
- [ ] 请销售用脱敏案例比较人工准备与系统辅助准备的耗时及遗漏；优先收集具体不准确/不实用的建议。
- [ ] 运行既有相关回归检查、`python -m compileall -q backend`、`npm --prefix frontend run build`；不新增测试代码，涉及真实服务的手动脚本不混入隔离检查。
- [ ] 准备五分钟演示：录入 → 准备 → 引用核对 → 承诺状态未知 → 刷新恢复；最后展示审计中的工具、提示词和来源范围。
- [ ] 基于实际结果更新已实现范围和限制；未通过的事项保留未完成状态，不顺带开始多 Agent、企业微信或自动提醒。

**当天完成标准：** 产品可以按固定数据重复演示，你能用一个业务案例解释工具调用、记忆、预算、引用和恢复为什么存在。

## 完成之后才开始的第二阶段

顺序：修复销售反馈 → 适配 Plan/Discovery 的销售目标 → 跟进任务及执行结果 → 聊天导入 → 产品知识 → 评估多 Agent。每项另定范围，不计入这 8 天。
