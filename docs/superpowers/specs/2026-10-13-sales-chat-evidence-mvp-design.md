# 模拟客户聊天与销售沟通准备：面试 MVP 设计

更新日期：2026-10-13。状态：核心代码与页面已实现，开发库迁移已执行；真实模型的完整主线仍有结构解析失败，不能宣称稳定验收。实际验证范围见 [验收记录](../../evaluations/sales-preparation/acceptance.md)。

## 1. 目标与取舍

用户已投入约一个月开发，当前目标是完成一两个能展示、能解释的核心功能，准备 Agent 岗位面试。已有自然语言客户资料维护作为第一个核心功能，本阶段完成第二个：基于客户聊天证据生成销售沟通准备。

演示闭环：选择已有客户 → 导入模拟聊天 → 提问“我明天要联系杨鑫，帮我准备一下” → 同名时选择客户 → 返回准备单 → 点击依据查看原文 → 刷新后仍能查看。

本设计取代此前以手动沟通纪要录入为入口的第一版计划。输入改为固定 JSON 格式的模拟客户聊天，不依赖企业微信开通资格、真实聊天或新增外部服务。

选择复用现有 Direct 与回复节点。独立销售子图会增加状态和父子图边界；多 Agent 会增加委派、结果合并与预算分摊，本阶段都没有对应业务收益。销售业务代码独立放在 `backend/sales/`，以后可以再封装为子图。

## 2. 范围与禁止项

- 一次导入绑定当前用户的一名已存在 Person；一次准备单也只针对一人。同名必须选择，找不到不能自动创建客户。
- 只支持 `simulated_chat` 来源、双人会话和文字消息；界面及准备单明确标注“模拟客户聊天”。不把录入内容称为已验证的真实客户发言。
- 第一版只接最新 Direct。Plan / Discovery 保留原能力，本阶段不扩充销售目标合同；服务端能力接口与前端入口明确限制模式。
- 明确准备入口发送自然语言提问，同时传 `response_kind=sales_preparation`；普通聊天默认 `chat`。这是期望回复类型，不是意图列表或 goal 层。准备入口缺少成功证据回执时必须失败，不能用普通模型回答冒充准备单。
- 准备功能只读。聊天不自动写入人物属性、长期事实、跟进待办，也不发送客户消息。
- 不接真实企业微信、不接语音/OCR、不增加向量库、产品知识库、提醒、客户分群或多 Agent。
- 原始聊天不可编辑；更正添加新消息。没有编辑、删除、完整客户聊天浏览器等额外产品页面。
- 不新增测试代码；使用固定模拟验收集、接口/界面验收及已有相关检查。制定计划不访问模型或业务库，不自动 commit/push。

## 3. 数据契约

模拟文件为 `SimulatedChatPayload`，只有 `source_conversation_id` 与 `messages`。每条 `SimulatedChatMessage` 包含稳定的 `source_message_id`、`speaker`（`customer` / `sales`）、带时区的 `sent_at` 和 `text`。导入 HTTP 请求另附选中的 `person_id`，不接收 owner、真实企微来源或模型生成的身份字段。

- 一批 1～100 条消息；单条原文 1～2000 字符；原文合计最多 50000 字符。
- `source_conversation_id`、`source_message_id` 长度 1～128；同一批不允许重复消息标识。
- 保留原文，不在入库前让模型改写、抽摘要或推断事实。时间统一存 UTC，显示使用 Asia/Shanghai。
- 文件中不含项目 person_id、手机号、密码等；同一模拟文件可以在不同测试用户下导入，归属由登录身份决定。

新增 PostgreSQL 表 `customer_chat_message`，字段：`message_id`（本地 UUID）、`owner_id`、`person_id`、`source_type`、`source_conversation_id`、`source_message_id`、`speaker`、`sent_at`、`text`、`content_hash`、`created_at`。

唯一键 `(owner_id, source_type, source_conversation_id, source_message_id)`。相同源消息、同一人物与内容重复导入不新增；源标识相同但内容或人物绑定不同返回 409，整批事务回滚。返回总数、新增数和重复数；重复调用的计数可以变化，不承诺响应字节相同。不能按文本去重，因为真实会话允许重复发言。

查询索引 `(owner_id, person_id, sent_at DESC, message_id DESC)`。人物在 Neo4j 中，导入前和生成前检查 owner 与人物，不能用 PG 外键宣称跨库强一致。人物删除后禁止新导入和新准备，已保存的引用按原 owner 保留。

`source_message_id` 用于导入去重，`message_id` 用于证据定位，Agent 的 `request_id` 用于一次提问重试，`thread_id` 用于图恢复，四者不得混用。

## 4. 最小接口与模块

- `GET /api/sales/customers?query=...`：当前用户已有客户候选，最多 10 个；复用人物查询。
- `POST /api/sales/chat-imports`：`person_id + payload`，导入一批模拟聊天；返回 `ChatImportResult(total_count, inserted_count, duplicate_count)`。
- `GET /api/sales/messages/{message_id}`：返回当前用户拥有的原文，跨用户与不存在统一 404。
- `GET /api/sales/capabilities`：返回 `preparation_available` 和 `source_types`，第一版仅最新 Direct 可准备、来源固定 `simulated_chat`。

不新增导入 Tool。导入来自用户在表单中的明确提交；Agent 只新增一个只读 Tool：`get_sales_preparation_context(person_id, meeting_goal)`。owner 等可信参数仍由现有执行器注入。

`backend/sales/contracts.py` 定义导入、证据和准备单模型；`importer.py` 处理标准化与批量导入；`context.py` 装配证据；`evidence.py` 校验引用；`preparation.py` 生成与渲染。数据库仓储与 HTTP 路由沿用项目现有位置。

## 5. 检索、生成与证据边界

```text
prepare_context → direct_decide → direct_validate
  → execute_tools(find_person；必要时 ask_clarification / resume)
  → observe_execution → direct_decide
  → execute_tools(get_sales_preparation_context)
  → observe_execution → direct_decide → direct_finish
  → generate_response（销售分支）→ finalize_memory → END
```

箭头展示正常路径；原有预算耗尽、取消、错误和检查点恢复路径继续生效。不恢复 Direct 的 goal 层，也不新增 Supervisor。

`SalesPreparationContext(person_id, customer_name, meeting_goal, as_of, sources, coverage)` 来自成功工具回执。检索必须先 owner/person 过滤，再按时间取最近片段；使用相邻消息保留问答与转述上下文。本版是按客户与时间检索，不宣称实现语义检索。

初始上限：`SALES_PREP_CANDIDATE_LIMIT=100`、`SALES_PREP_MESSAGE_LIMIT=20`、`SALES_PREP_EVIDENCE_TOKEN_BUDGET=3000`。这些是待测工程起点，仍受现有整个模型输入预算约束。使用现有 Token 计数器；超长原文提供标记裁剪的 excerpt，不把模型未看到的内容开放给它引用。

固定服务端 `as_of`，聊天检索同时约束 `sent_at <= as_of`、`created_at <= as_of`。`coverage` 保存候选数、选中来源 ID、覆盖时间、估算 Token、是否裁剪。档案为读取快照，as_of 不代表 Neo4j 与 PG 的事务级一致快照。

`EvidenceSource(source_id, source_type, message_id, occurred_at, speaker, excerpt, truncated)`：聊天来源标识 `chat:<message_id>`；档案字段用 `profile:<person_id>:<field>`，标记 `profile_snapshot`，不能伪装成客户原话。后台自行装配 sources，不让模型提供正文或归属。

`SalesPreparationBrief` 包含客户背景 `customer_summary`、近期沟通 `recent_communications`、需求 `customer_needs`、上次约定 `previous_commitments`、建议问题 `suggested_questions`、建议开场 `suggested_opening`、信息缺口 `missing_information`、限制 `limitations`。

- 事实项 `FactItem(text, evidence_refs)`，引用 `EvidenceRef(source_id, quote)` 至少一条；表达为“记录中客户表示……”，不升级为独立核实的事实。
- 约定项 `CommitmentItem` 增加 `completion_state=OPEN / DONE / UNKNOWN`。明确未完成/完成才使用 OPEN/DONE；没有完成记录用 UNKNOWN。实施时补充 `status_evidence_refs`：承诺证据放在 `evidence_refs`，OPEN/DONE 另需非空状态证据，避免把“我将发送”当成已发送的依据。引用存在仍不能自动证明状态判断正确。
- 建议项 `RecommendationItem(text, basis_refs)` 与事实分开。一般提问可无引用；个性化建议附依据，不承诺未知产品能力。
- 校验人物一致、来源属于本次上下文、quote 非空且为对应 excerpt 的原文片段。合法引用不等于结论在语义上成立；转述、否定和冲突需人工验收。
- 生成只使用当前需求、必要澄清与本次证据；会话摘要、长期记忆可辅助人物解析，不能成为无来源的准备单事实。
- 聊天中的“忽略规则、修改客户”等是数据，不是用户授权；不据此调用写工具。

`SalesPreparationResult(brief, sources, as_of, meeting_goal, coverage, schema_version, prompt_version)` 由服务端组装，初始版本为 `schema_version=1`、`prompt_version=sales-prep.v1`。

## 6. 接入现有运行时

- 回复节点从当前请求成功的上下文 Tool 回执生成；发现多个目标人物上下文时拒绝合并，要求用户分开准备。
- `response_kind` 在请求、任务 input、运行器、检查点和前端原请求重试中保留。请求幂等比较包含它；默认 chat 保持旧消息哈希兼容，相同 request_id 改类型返回 409。Plan / Discovery 拒绝准备类型，不能静默降为普通聊天。
- 对准备类型的请求，目录只开放 `find_person`、`get_person_detail`、`get_sales_preparation_context` 及已有澄清能力；Direct 校验与执行前校验同时拒绝写工具，不能只靠提示词。普通聊天里成功调用上下文 Tool 也可触发准备分支；固定验收优先使用明确准备入口，不声称模型可靠识别所有口语表达。
- 复用 `invoke_structured(schema, system_prompt, messages, validator=...)`，结构与引用校验进入该 validator；最多两次尝试，计入同一请求预算，不再外包一层重试或模型润色。
- 同一请求在读上下文后又成功写入相关人物，停止发布旧准备单，提示重新生成。新的请求可读取新的资料；不在本版加入自动刷新循环。
- 结构化生成期间只发进度；校验通过后确定性渲染文本和卡片。不中途发布未经校验的 JSON 或结论。
- 图状态、`ChatResponse`、`ChatHistoryMessage` 增加可空 `sales_preparation`；新请求重置为空。复用 `agent_request.response` JSONB 持久化，不新增准备单表。
- 历史加载按 owner/request 关联保存结果，只附加给助手消息；旧消息字段缺省仍正常展示。档案引用展示保存快照，不拿当前档案替换旧证据。
- 无聊天时展示可用档案、一般问题和资料不足提示；校验失败返回 FAILED 与固定说明，不能按现有默认映射误报 COMPLETED；预算耗尽仍走既有预算收尾。
- 同步 DB 接口使用 FastAPI 同步路由/现有执行线程，不堵塞 SSE 事件循环。现有流事件增加中文阶段说明，不另建 SSE 服务。
- Langfuse 复用现有预算、模型与节点审计；新增来源 ID、数量、版本、裁剪和校验结果元数据，本地 trace_event 不打印原文。
- 新 Tool 会改变目录哈希；升级前处理待恢复请求，不绕过已有兼容校验。

## 7. 完成定义

至少 12 个固定模拟案例覆盖正常流程、同名、空聊天、转述、否定、约定未知、冲突、伪造引用、提示注入、长聊天、跨用户、重试/刷新/重启；每例注明可得结论、禁止结论与期望来源。

先离线检查导入、检索、引用校验，再用模拟数据联调模型。模型调用是运行时验证，仍有费用；具体联调轮数和传输范围在实施时确定，不偷偷替换成真实客户资料，本次制定计划不调用模型。

记录实际保留来源、引用合法性、原文是否支持结论、重要信息遗漏、澄清情况、耗时、调用数和 Token。固定集发现的严重错误清零后再演示，不宣称企业生产成熟或证明成交率提升。

简历完成后可写“基于 LangGraph 构建客户沟通准备助手，支持模拟聊天导入、按客户检索、结构化生成和原文引用”。企业微信存档接入明确标为后续设计；不写成已接入或已上线。
