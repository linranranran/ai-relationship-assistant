# 模拟客户聊天与销售沟通准备开发计划

> **For agentic workers:** 按 `superpowers:executing-plans` 逐项推进；只有用户明确选择委派时才使用 `superpowers:subagent-driven-development`。2026-10-13 已执行核心实现，实际验收及未完成验证见 [验收记录](../../evaluations/sales-preparation/acceptance.md)。

**Goal:** 用模拟客户聊天完成导入、证据检索、销售准备单和原文展示的闭环，形成可演示的第二个核心功能。

**Architecture:** 最新 Direct 查找人物并调用只读上下文 Tool；现有回复节点调用 `invoke_structured`，经引用校验后确定性渲染。独立销售模块复用现有身份隔离、PostgreSQL、Neo4j、预算、检查点、SSE、Langfuse及回复持久化。

**Tech Stack:** 项目现有 Python >=3.11、FastAPI、LangGraph 1.2.12、Pydantic、PostgreSQL、Neo4j、React、TDesign；不新增 Agent 框架或外部依赖。

**Spec:** [模拟聊天 MVP 设计](../specs/2026-10-13-sales-chat-evidence-mvp-design.md)。执行时同时阅读本计划和设计。

## 全局约束

- 第一版一次只处理一名当前 owner 下已有 Person；同名选择、查无结果澄清，不能自动新增。
- 第一版只支持最新 Direct、`simulated_chat`、双人文字会话；Plan / Discovery 保留原能力但不增加销售准备合同。
- 明确准备入口传 `response_kind=sales_preparation`；普通聊天默认 `chat`。准备类型必须有成功上下文回执且只允许只读工具；不是新增意图列表或 goal 层。
- 一批 1～100 条消息；单条原文 1～2000 字符；原文合计最多 50000 字符。
- `source_conversation_id`、`source_message_id` 长度 1～128；同一批不允许重复消息标识。
- `speaker` 只有 `customer` / `sales`；`sent_at` 必须带时区；后台存 UTC，显示 Asia/Shanghai。
- 源消息唯一键 `(owner_id, source_type, source_conversation_id, source_message_id)`；相同绑定和内容不新增，冲突整批回滚并返回 409。
- 初始配置：`SALES_PREP_CANDIDATE_LIMIT=100`、`SALES_PREP_MESSAGE_LIMIT=20`、`SALES_PREP_EVIDENCE_TOKEN_BUDGET=3000`，同时服从现有整个模型输入预算。
- 只读准备，不自动修改客户、长期事实、待办，不联系客户；聊天不能成为写操作授权。
- `schema_version=1`、`prompt_version=sales-prep.v1`；引用校验进入已有结构化服务，最多两次尝试，不增加外层重试。
- 不新增测试代码、不增加真实企微接入、不修改真实 `.env`、不自动 commit/push。迁移和模型联调在实施阶段进行。

## 审阅重点

1. 同一源消息重传或换人物绑定：前者不重复，后者 409 且整批回滚；第 2 天验收。
2. 客户转述他人、否定需求或修改说法：不能归错人物或忽略否定；第 3、4、6 天验收。
3. 约定没有后续、长聊天未覆盖旧消息：UNKNOWN 与覆盖限制，不能推断“仍未完成”或“从未提过”；第 3、4、6 天验收。
4. 伪造引用、真实引用却推理错误、提示注入：前者拒绝；语义问题人工评估；不能触发写入；第 4、6 天验收。
5. 跨用户、刷新重试、读取后修改、升级恢复：隔离来源与快照，保存卡片和原文定位，不绕过兼容检查；第 2、4、5、6 天验收。

## 时间与执行节奏

按每天 4～6 小时安排 **6 个开发日，另留 1～2 个修复缓冲日**。这是待实施估算，不承诺固定日期；第 2 天导入链路完成后重估。第 4 天任务最多，超时先延长该任务，不加新功能。

| 开发日 | 完成的业务能力 | 当天必须能演示 |
|---|---|---|
| 第 1 天 | 数据格式、输出模型、模拟案例 | 能解释输入、事实、引用和建议的区别 |
| 第 2 天 | 原文入库、去重、归属与导入接口 | 重复导入不新增、冲突不半批成功 |
| 第 3 天 | 按客户检索、证据窗口、Token 控制 | 看到入模的消息 ID 和裁剪原因 |
| 第 4 天 | 结构化生成、引用校验、Direct 接入 | API 生成准备单，假引用被拒绝 |
| 第 5 天 | 导入界面、准备卡片、原文与历史 | 在页面跑通闭环，刷新保留卡片 |
| 第 6 天 | 固定集验收、失败修复与演示 | 五分钟演示和一份真实验收记录 |

建议你负责后端销售业务逻辑，助手按天提供骨架、关键中文注释和审阅；前端可以继续由助手完成。具体分工以你后续执行指令为准。

## 文件职责

下列新增文件已创建；不能仅凭文件存在认定语义质量、真实集成和稳定性已经通过验收。

| 新增文件 | 职责 |
|---|---|
| `backend/sales/__init__.py` | 业务模块入口 |
| `backend/sales/contracts.py` | 导入、证据、上下文和准备单的严格模型 |
| `backend/sales/importer.py` | 模拟输入标准化、归属核对、调用批量仓储 |
| `backend/sales/context.py` | 客户档案、最近聊天、证据预算与覆盖范围 |
| `backend/sales/evidence.py` | 引用和准备对象校验、快照失效检查 |
| `backend/sales/preparation.py` | 提示词、结构化生成、确定性文本渲染 |
| `backend/db/customer_chat_store.py` | 原文保存和带 owner 的读取 |
| `backend/db/migrations/012_customer_chat_message.sql` | 一张新表、唯一约束与检索索引 |
| `backend/routers/sales.py` | 客户搜索、模拟导入、证据原文、能力接口 |
| `backend/tools/sales.py` | 只读上下文 Tool，不调用模型 |
| `frontend/src/api/sales.js` | 销售接口封装 |
| `frontend/src/sales/ChatImportPanel.jsx` | 选择客户、导入 JSON、展示结果 |
| `frontend/src/sales/SalesPreparationCard.jsx` | 背景、需求、约定与建议 |
| `frontend/src/sales/EvidenceDrawer.jsx` | 来源类型、发言人、时间与原文 |
| `docs/evaluations/sales-preparation/demo-chat.json` | 虚构客户主线聊天 |
| `docs/evaluations/sales-preparation/cases.json` | 至少 12 个固定验收案例 |
| `docs/evaluations/sales-preparation/acceptance.md` | 实施后填写实际结果和失败记录 |

## 第 1 天：把业务与数据契约定义清楚

**Files:** 新增 contracts、模块入口、demo-chat.json、cases.json。

**Produces:**

- `SimulatedChatMessage(source_message_id, speaker, sent_at, text)` 与 `SimulatedChatPayload(source_conversation_id, messages)`，校验全局长度、时间与批内重复。
- `ChatImportRequest(person_id, payload)`、`ChatImportResult(total_count, inserted_count, duplicate_count)`、`StoredChatMessage`（含本地 message_id，但对外不暴露 owner）。
- `EvidenceSource`、`EvidenceRef`、`FactItem`、`CommitmentItem`、`RecommendationItem`、`EvidenceCoverage`、`SalesPreparationContext`、`SalesPreparationBrief`、`SalesPreparationResult`，字段以设计第 5 节为准。

- [ ] 模型使用项目的严格 Pydantic 风格；中文注释说明字段表示什么、谁提供、是否可信。事实引用非空，承诺状态只有 OPEN/DONE/UNKNOWN。
- [ ] 编写虚构主线，包含客户需求、销售答复、一项明确完成约定、一项状态未知约定、一句转述他人的话；人物需在演示前由用户正常创建或选择，不把真实数据库 ID 写入样例。
- [ ] 创建至少 12 个案例；每例包含 case_id、scenario、输入、聊天、期望行为、应引用的 source_message_id、禁止结论。覆盖设计第 7 节。
- [ ] 示例消息形式如下；明确它是模拟文本，不是企微导出格式：

```json
{
  "source_conversation_id": "demo-yangxin-202610",
  "messages": [
    {
      "source_message_id": "demo-yx-001",
      "speaker": "customer",
      "sent_at": "2026-10-08T10:00:00+08:00",
      "text": "我们最关心实施周期，预算还没有确定。"
    },
    {
      "source_message_id": "demo-yx-002",
      "speaker": "sales",
      "sent_at": "2026-10-08T10:01:00+08:00",
      "text": "我明天下午给你发一版实施方案。"
    }
  ]
}
```

- [ ] 本地临时调用校验合法数据、无时区时间、额外 owner 字段、批内重复消息及长度越界；用 JSON 格式检查确认两个数据文件可解析，无需新建测试文件。

**完成标准：** 你能解释“客户说过”“档案里记录了”“模型建议”三个层次；后续模块共用同一组类型，不再另写近似模型。

## 第 2 天：完成模拟聊天导入与原文存储

**Files:** 新增迁移、customer_chat_store、importer、sales 路由；修改 `backend/main.py` 与 `backend/db/init_postgres.py` 的接入和表验证。

**Consumes:** 第 1 天的模型，现有 `get_current_user`、`get_postgres_connection`、Neo4j 人物归属查询。

**Produces:**

- `import_simulated_chat(*, owner_id: str, request: ChatImportRequest) -> ChatImportResult`：校验人物、固定 simulated_chat 来源、UTC 标准化，交给仓储。
- `save_messages(*, owner_id: str, person_id: str, payload: SimulatedChatPayload) -> ChatImportResult`：唯一约束加一个短事务；冲突抛出 `ChatImportConflict`。
- `get_message(*, owner_id: str, message_id: str) -> StoredChatMessage | None`。
- `list_candidates(*, owner_id: str, person_id: str, as_of: datetime, limit: int) -> list[StoredChatMessage]`：同时约束 sent_at/created_at，不暴露跨用户数据。
- 设计第 4 节的四个 HTTP 接口；导入成功固定 200、输入不合法 422、源冲突 409、跨用户/不存在 404。

- [ ] SQL 建表与索引；内容摘要包含人物绑定、来源标识、UTC 时间、speaker 和原文，禁止 INSERT 前按文本去重。
- [ ] 并发争用靠唯一约束裁决；冲突路径重新读取现有行比对。同一批任一条冲突回滚本批新增，不能留下半批数据。
- [ ] 客户搜索只返回当前用户的候选并保留同名选择，浏览器不能指定 owner；同步 DB 路由交给 FastAPI 线程池。
- [ ] 在开发库执行迁移，先读取现有数据与表状态；不清库、不自动重建人物。
- [ ] 接口验收：第一次导入；同文件再次导入；两个并发相同导入；相同源消息换正文；换 person_id；混合一条新增一条冲突；用户 B 读取用户 A 的 message_id。

**完成标准：** 原文可查询；重复导入业务无重复；冲突整批回滚；不存在和跨用户统一 404。新计数随重试变化是正确行为，不冒充 HTTP 响应缓存。

## 第 3 天：实现按客户检索与证据窗口

**Files:** 新增 context；修改 `backend/config.py` 与 `.env.example` 增加三个 SALES_PREP 配置；实际 `.env` 不自动修改。

**Consumes:** `list_candidates`、现有 owner 下人物详情、记忆运行时 Token 计数器、第 1 天证据模型。

**Produces:** `build_sales_preparation_context(*, owner_id: str, person_id: str, meeting_goal: str) -> SalesPreparationContext`。

- [ ] 获取并验证客户档案，固定 as_of；档案字段成为 profile_snapshot 来源，聊天成为 simulated_chat 来源，身份和来源 ID 由后台生成。
- [ ] 候选先 owner/person/time 过滤，最近最多 100 条；按最近连续片段装配，保留相邻发言，最多 20 条、3000 证据 Token。profile 来源也计入该预算。
- [ ] 剪裁优先保留完整消息；单条过长提供 excerpt 与 truncated 标记；最终入模前仍接受现有整次输入预算检查，不能只计算正文而忽略 Schema/提示词。
- [ ] coverage 记录实际选中来源、覆盖时间、Token、裁剪和候选数；普通日志只记录这些元数据，不记录原文。
- [ ] 验收：一个客户两个会话；另一客户同名但不同 ID；时间顺序乱序输入；未来消息；转述前后文；长聊天挤压；完全没有聊天。检查输出 sources 与数据库原文、owner、人物一一对应。

**完成标准：** 能在 PyCharm 看出“哪些消息进入模型、哪些被裁剪、为什么”；没有记录不表示客户没有需求，没有检索到不表示历史不存在。

## 第 4 天：生成准备单并接入 Direct

**Files:** 新增 evidence、preparation、只读 sales Tool；修改 `backend/tools/schema/tool_args.py`、`backend/tools/registry.py`、`backend/tools/definitions.py`、`backend/agent/strategies/catalog.py`、`backend/agent/direct_runtime.py`、`backend/agent/nodes/generate_response.py`、`backend/agent/state.py`、`backend/models/schemas.py`、`backend/routers/chat.py`、`backend/routers/stream.py`、`backend/agent/run_execution.py`、`backend/agent/request_idempotency.py`；核对 `backend/db/agent_stream_store.py` 任务输入与幂等摘要。

**Consumes:** 第 3 天上下文、现有 `invoke_structured` / `direct_records` / execute_tools / finalize_memory。

**Produces:**

- `GetSalesPreparationContextArgs(person_id, meeting_goal)`：业务参数，不含 owner、confirmed 或 request_id。
- `get_sales_preparation_context(**kwargs) -> dict`：使用现有 Tool 成功/错误封装，仅查询，不调用模型。
- `validate_brief(brief: SalesPreparationBrief, *, context: SalesPreparationContext) -> None`：业务校验异常使用已有 `ModelBusinessValidationError`，让结构化服务只修复一次。
- `prepare_sales_brief(*, context: SalesPreparationContext, user_input: str) -> SalesPreparationResult`；`render_sales_brief(result: SalesPreparationResult) -> str`。
- 当前图状态及 ChatResponse 的可空 `sales_preparation`；新请求清空，成功结果沿现有响应保存路径进入 agent_request.response。
- `ChatRequest.response_kind: Literal["chat", "sales_preparation"] = "chat"` 与同名图状态字段；完整请求保存在任务 input，执行器据此重建 ChatRequest，不能仍只转发 message。

- [ ] 注册 Tool、参数模型、目录输出说明；person_id 必须来自真实查找/澄清回执，沿用现有 Direct ID 校验与业务归属校验。
- [ ] 保留 response_kind 全链路；请求哈希同时比较回复类型，chat 默认保持原哈希兼容，改类型复用原 request_id 返回 409。核对任务入队的 input/摘要、恢复和模型缓存均使用本次请求类型。
- [ ] 准备类型的目录只开放 find_person/get_person_detail/get_sales_preparation_context 与澄清能力；执行前确定性拒绝写工具。缺少成功上下文回执、非 Direct 配置或多个目标客户返回明确失败，不能静默普通回答。
- [ ] Tool 说明“联系前准备”的用途与返回结构；最新 Direct 调用，Plan / Discovery 白名单不扩展。升级前处理 pending 请求，不能绕过目录哈希检查。
- [ ] `generate_response` 从当前请求成功的该 Tool 回执读取上下文，走独立 preparation 函数；没有上下文走原逻辑。多个客户上下文要求分开请求，不合并。
- [ ] 如果相关人物在上下文读取后被本请求成功修改或删除，拒绝旧快照准备，保留原写回执并提示重新生成；不增加自动重查循环。
- [ ] 生成提示词把聊天作为不可信数据，与系统规则分开；仅使用本次证据，不能拿会话摘要或模型常识填补客户事实。
- [ ] 校验来源范围、客户一致、原文匹配；约定没有后续必须 UNKNOWN。事实、建议、资料不足和模拟来源都明确呈现。
- [ ] 复用已有预算与最多两次结构化尝试；记录 schema/prompt 版本。预算耗尽走现有收尾；结构/引用失败固定说明并映射 FAILED，不能落入 `_to_chat_response` 的默认 COMPLETED。
- [ ] 校验前只推现有进度事件，校验后渲染最终文本并附卡片，不再额外调用模型润色；统一 `finalize_memory` 收尾一次。
- [ ] 先用临时构造输出验收假引用、跨客户引用、空 quote、合法 quote；模型联调只用模拟聊天，实施时明确轮数与费用预算。另验收无上下文却停止调用、改回复类型的重试、只读边界、语义错误、提示注入、同名澄清、无聊天、预算耗尽、普通资料查询回归。

**完成标准：** API 返回有依据的结构化准备单；假引用不能成为最终结果；生成失败不误报完成。明确字符串引用校验尚不能自动证明推理正确。

## 第 5 天：做出能展示的完整页面

**Files:** 新增 sales API、ChatImportPanel、SalesPreparationCard、EvidenceDrawer；修改 `frontend/src/pages/Chat.jsx`、`frontend/src/chat/ChatLayout.jsx`、`frontend/src/chat/ChatMessage.jsx`、`frontend/src/chat/messages.js`、`frontend/src/api/chat.js`、`frontend/src/validation/requests.js`、必要样式；修改 `backend/db/chat_history_store.py` 与 `backend/models/schemas.py` 的历史模型。

**Consumes:** sales 四个接口、ChatResponse.sales_preparation、第 1 天结果模型与现有聊天历史/SSE。

**Produces:** 客户选择和 JSON 导入面板，准备卡片，证据抽屉，以及刷新后恢复卡片的历史投影。

- [ ] 面板先查询并选择当前客户，再解析 JSON，提交 person_id + payload；前端预校验友好提示，后台仍执行完整校验。显示导入新增/重复数量与“模拟客户聊天”。
- [ ] 提供“生成沟通准备”按钮，发送自然语言提问、response_kind=sales_preparation 和正常 request_id；普通输入默认 chat。pending 原请求保留回复类型，重试不能丢失；旧 pending 缺省 chat。能力接口不支持准备时禁用入口并解释模式限制。
- [ ] 完成响应携带 sales_preparation 写入消息展示状态；重试/重连按现有 request_id + role 去重，不再追加第二张同请求卡片。
- [ ] 卡片分区展示背景、需求、近期沟通、上次约定、建议问题和开场；UNKNOWN 明确显示“状态未确认”，不显示成待办。
- [ ] 点击引用：聊天获取 owner 下 immutable 原文并显示 speaker/时间/引用句；档案显示结果里保存的快照。React 按文本显示，不把聊天作为 HTML 执行。
- [ ] 历史查询以 owner + request_id LEFT JOIN 保存响应，只把结构化字段附给助手消息；避免逐条查询。前端 historyMessages 保留该字段，旧消息为空正常显示。
- [ ] 原文不存在显示“来源暂不可用”，不能用同名客户或当前档案替换；普通回复的复制、中断、取消、历史加载继续工作。
- [ ] 界面验收：导入 → 提问 → 澄清 → 准备 → 原文 → 刷新 → 加载更早消息；切换两个用户；SSE 重连；非销售回复；在 frontend 执行 `npm run build` 确认打包。

**完成标准：** 用户能从界面完成闭环，无需打开数据库；来源标记始终诚实，刷新和重连不丢卡片、不重复。

## 第 6 天：固定案例验收与面试演示

**Files:** 完成 acceptance.md，修复前五天暴露的必要问题；复用现有相关检查，不新增测试文件。

- [ ] 逐例跑固定集，记录实际 source_id、保留消息、澄清、引用合法性、原文是否支持结论、重要信息遗漏、耗时、模型调用数和 Token。统计分母与失败，不写空泛的“准确率很高”。
- [ ] 跨用户引用/查询、伪造引用被接受、未授权写入、导入半批成功属于阻断问题；固定集中的这些问题清零后再演示。语义错误记录样例并修复，不用原文匹配率掩盖。
- [ ] 服务重启后查询原 request_id 与历史，确认已保存结果可展示；同请求重放保留既有上下文快照，不将新导入消息偷偷混入旧结果。
- [ ] 运行项目既有的相关 Python 检查、前端验证与构建；只报告实际执行的结果。记录未跑的真实企微、真实客户、负载验证等限制。
- [ ] 准备五分钟演示：30 秒介绍用途；1 分钟导入和去重；1 分钟生成准备；1 分钟点击证据与解释 UNKNOWN；1 分钟同名/隔离/失败边界；30 秒说明后续企微接入。
- [ ] 请熟悉销售的人评价建议是否可用、遗漏了什么。可记录人工准备与助手辅助的耗时对比，但没有实测不写节省百分比，不推断成交率提升。

**完成标准：** 有稳定可复现的演示、真实验收记录、可说明的限制，才把第二个功能列为“已实现”。

## 简历与后续范围

完成后可描述：基于 LangGraph 实现客户沟通准备，支持模拟聊天导入与幂等去重、按客户检索证据、结构化输出和原文引用，并复用请求预算、恢复与审计。

不可描述：已集成企业微信、已自动读取真实客户聊天、已在企业生产上线、显著提升销售业绩。企业微信是明确标注的后续数据源，届时再处理权限、SDK、员工与 external_userid 映射、增量游标和存档同意状态。

缓冲日只修复验收问题；不加入新的 Agent、向量检索、自动事实回写、待办、提醒或 CRM 页面。先完成本闭环，再依据使用反馈决定下一步。
