# AI 人际关系助手 30 天开发计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 2026-09-21 到 2026-10-20，每天投入 4～6 小时，把现有初版改造成可演示、可追溯、可评测的客户记忆与沟通建议 Agent。

**Architecture:** 服务端认证生成可信 RequestContext；Neo4j 保存账户、人物、关系和事实版本，SQLite 保存原始聊天与会话，ChromaDB 作为可重建索引。LangGraph 每次只决定一个动作，根据工具结果继续、澄清、等待确认或回答。

**Tech Stack:** Python 3.11、FastAPI、Pydantic 2、LangGraph 0.2.61、Neo4j 5、ChromaDB、SQLite、React、pytest、Langfuse。

**Spec:** `docs/PROJECT_DIRECTION.md`

## Global Constraints

- 每天计划 4～6 小时；默认按 5 小时安排，结束时必须留下可以复查的代码、测试输出或文档。
- 本月只支持一个固定 JSON 聊天导入格式、一个 Agent、单实例 SQLite 和常见亲属称谓。
- User、Person 和 owner 分开；身份、owner、本人起点及确认授权不交给模型。
- 数据默认完全隔离；本月不实现团队共享、跨用户实体合并和企业微信接入。
- 原始消息和事实版本是权威数据；ChromaDB 是可以重建的派生索引。
- 先写失败测试，再实现最小功能；当天测试未通过时不能把任务勾成完成。
- 用户自己实现业务代码；教学骨架位于 `docs/scaffolds`，生产代码不得导入该目录。
- 每七天做一次集成和范围复核，不在集成日增加新技术。

## Review Focus

- A 账号提交 B 的 user_id、thread_id、person_id 或 fact_id 时，任何层都不能暴露或修改 B 的数据；第 4、6、12、21 天覆盖。
- 请求重放、响应丢失和服务恢复不能重复创建人物、事实或删除动作；第 3、10、18、26 天覆盖。
- 同名人物、事实矛盾和信息不足必须澄清或返回不确定状态；第 9、17、22、28 天覆盖。
- 已失效事实和旧向量不能重新进入当前建议；第 18～20、22 天覆盖。
- 朋友短路径、反向亲属边、未知年龄和候选截断不能生成错误的唯一称谓；第 27～28 天覆盖。

---

## 每天固定节奏

每天先完成“必须完成”，有剩余时间再做“可选加分”。建议时间分配：

| 阶段 | 时间 | 内容 |
|---|---:|---|
| 回顾 | 20 分钟 | 阅读昨天记录与失败测试，确定今天唯一目标 |
| 学习 | 40 分钟 | 只读当天涉及的框架文档或现有源码 |
| 测试 | 60 分钟 | 写失败测试并确认失败原因正确 |
| 实现 | 150 分钟 | 实现通过当前测试的最小代码 |
| 回归 | 60 分钟 | 跑当天与已有相关测试，查看 diff |
| 记录 | 30 分钟 | 更新进度、记录踩坑、提交一个聚焦 commit |

遇到阻塞时，在当天记录四项：复现命令、最后一段错误、预期行为、已经排除的原因。排查超过 90 分钟仍无进展，就缩小为最小复现，不提前开启下一模块。

## 第一阶段：可信身份与数据底座

### Day 1｜2026-09-21 星期一：修复开发环境和测试基线

**目标文件：** `pyproject.toml`、`requirements.txt`、`tests/conftest.py`、`tests/test_import_smoke.py`、`README.md`

- [ ] 记录当前 Python、uv、Neo4j 和 Node 版本；确认旧 `.venv` 无法启动的具体错误。
- [ ] 根据 `pyproject.toml` 和 `uv.lock` 重建虚拟环境，增加 pytest 与 pytest-asyncio 开发依赖。
- [ ] 统一依赖来源；若暂时保留 requirements.txt，确保 Chroma 与认证依赖版本一致。
- [ ] 写导入冒烟测试，证明导入 `backend.main` 不调用外部 LLM、embedding 或数据库写操作。
- [ ] 运行冒烟测试；保存命令和结果到当天进度记录。

**结束标准：** `uv run pytest tests/test_import_smoke.py -q` 有明确通过结果；若导入仍产生客户端副作用，测试保持失败并把 Day 2 前半天用于修复。

### Day 2｜2026-09-22 星期二：建立领域身份类型

**目标文件：** `backend/models/domain.py`、`backend/services/access.py`、`tests/auth/test_request_context.py`

- [ ] 阅读 `docs/scaffolds/identity_access.py` 和当前 `backend/auth.py`。
- [ ] 定义 `RequestContext(user_id, self_person_id, request_id)`、`PersonRef` 和最小领域错误。
- [ ] 写假 UserRepository/PersonRepository，测试认证身份 A 不能被请求体中的 B 替换。
- [ ] 实现 `resolve_request_context` 和 `require_owned_person` 的最小行为。
- [ ] 测试缺本人映射、悬空映射和异主映射都拒绝继续。

**结束标准：** RequestContext 只由已认证 user_id 构造；测试中没有从请求体读取 owner。

### Day 3｜2026-09-23 星期三：注册时建立唯一的本人 Person

**目标文件：** `backend/auth.py`、`backend/repositories/user_repository.py`、`tests/auth/test_registration.py`

- [ ] 为 User.user_id、phone_number、Person.id 设计唯一约束及初始化方式。
- [ ] 写测试：注册成功后 `user_id != self_person_id`，Person.owner_id 等于 user_id。
- [ ] 写注册操作重放测试：相同 operation_id 不创建第二个 User 或本人 Person。
- [ ] 在同一 Neo4j 事务中创建 User、本人 Person 和 self_person_id 映射。
- [ ] 为已有 User 缺映射定义 `SELF_PERSON_BINDING_REQUIRED`，不按姓名自动匹配。

**结束标准：** 新账户能得到唯一本人节点；事务中间失败不留下半个账户或悬空映射。

### Day 4｜2026-09-24 星期四：聊天接口接入真实认证

**目标文件：** `backend/models/schemas.py`、`backend/routers/chat.py`、`frontend/src/api/chat.js`、`tests/auth/test_chat_auth.py`

- [ ] 从 ChatRequest 删除 user_id、user_name、history，增加 thread_id 与 request_id。
- [ ] chat 路由通过 `Depends(get_current_user)` 和 AccessService 构造 RequestContext。
- [ ] 写测试：无 token、过期 token、A token 携带额外 B 身份字段均不能冒用 B。
- [ ] 前端只提交 message、thread_id、request_id；暂时生成 UUID 并显示基础错误。
- [ ] 回归注册、登录和 `/auth/me`。

**结束标准：** 服务端不再从 ChatRequest 取得 user_id；未认证请求不能运行 Agent。

### Day 5｜2026-09-25 星期五：人物 CRUD 全链路 owner 约束

**目标文件：** `backend/db/neo4j_client.py`、`backend/tools/person.py`、`tests/db/test_person_ownership.py`

- [ ] 写 A/B 双账号人物读取、更新和删除测试。
- [ ] 统一人物仓储接口为关键字参数 `owner_id`、`person_id`，修复所有旧两参调用。
- [ ] 更新和删除 Cypher 在同一 MATCH 中包含 id 与 owner_id，并检查匹配数量。
- [ ] 修复新增人物向量失败时的补偿删除调用；暂时用假向量服务验证。
- [ ] 确认零结果返回一致结构，异主与不存在不泄露差异。

**结束标准：** A 无法读改删 B；合法新增、查询、更新、删除均有测试通过。

### Day 6｜2026-09-26 星期六：关系 CRUD 与路径 owner 约束

**目标文件：** `backend/db/neo4j_client.py`、`backend/tools/relation.py`、`backend/tools/query.py`、`tests/db/test_relation_ownership.py`

- [ ] 测试关系起点、终点、边和 through_person_id 任一异主时创建失败。
- [ ] 修复 `get_relation_by_id`、关系更新、删除和列表的 owner 匹配与匹配数检查。
- [ ] 修复 `list_person_relations` 筛选分支漏传 owner_id。
- [ ] 在普通路径查询中校验全部中间 Person 和每条边归属。
- [ ] 人为插入异主脏边，证明详情、列表和路径不会暴露另一用户人物。

**结束标准：** 关系全链路跨账号测试通过；不再依赖前置检查后按裸 ID 写入。

### Day 7｜2026-09-27 星期日：第一周集成与补漏

**目标文件：** 第一周所有文件、`docs/PROGRESS.md`

- [ ] 从干净测试数据运行注册→登录→本人节点→新增人物→建立关系→查询路径。
- [ ] 搜索全部 `get_person_detail(`、`update_person(`、`delete_person(` 调用，确认签名一致。
- [ ] 跑第一周全套测试；把失败按身份、签名、事务、资源四类归档并修复。
- [ ] 更新代码审阅 R01～R04 的实际状态，只对有测试证据的条目标记完成。
- [ ] 录制或记录两分钟演示步骤，作为第一周成果。

**周验收：** 两个账户数据隔离；`User -> self_person_id -> Person` 可解释且可测试；基础 CRUD 不再有已知签名错误。

## 第二阶段：工具契约、会话和聊天导入

### Day 8｜2026-09-28 星期一：统一 ToolSpec 和 ToolResult

**目标文件：** `backend/tools/contracts.py`、`backend/tools/args.py`、`tests/tools/test_contracts.py`

- [ ] 阅读 `docs/scaffolds/tool_contracts.py`。
- [ ] 定义 ToolCode、ToolResult、ToolCall、ToolSpec。
- [ ] 为 find_person、get_person_detail 建首批严格 Pydantic 参数模型，`extra="forbid"`。
- [ ] 测试 owner_id、confirmed、self_person_id 出现在模型参数时被拒绝。
- [ ] 测试 null、未知字段、limit 越界不会到达 handler。

**结束标准：** 新契约能表达成功、参数错误、歧义、权限、暂时故障和确认需求。

### Day 9｜2026-09-29 星期二：建立唯一 Tool Catalog

**目标文件：** `backend/tools/catalog.py`、`backend/tools/definitions.py`、`backend/tools/registry.py`、`tests/tools/test_catalog.py`

- [ ] 以 ToolSpec 集合同时生成模型 schema 和运行 registry。
- [ ] 测试两个名称集合相等，修复 `list_person_relations` / `list_relations` 漂移。
- [ ] 把现有人物和查询工具逐个接入 catalog；handler 统一接收 ctx。
- [ ] 未迁移工具暂时显式列为禁用，不能保留模型可见但运行不存在的状态。
- [ ] 写同名人物查询测试：返回候选列表，不默认第一项。

**结束标准：** 模型看到的每个工具都有运行 handler，运行中的每个公开工具都有严格参数模型。

### Day 10｜2026-09-30 星期三：错误、重试与写入幂等

**目标文件：** `backend/tools/catalog.py`、`backend/tools/person.py`、`backend/agent/nodes/execute_tools.py`、`tests/tools/test_execution.py`

- [ ] 删除基于“缺少/已存在”等中文文案的重试判断。
- [ ] 写测试：参数、权限、歧义错误永不重试；临时读取错误最多重试一次。
- [ ] 为新增人物和关系传入 operation_id，重复 ID 返回相同业务结果。
- [ ] 测试写入成功但响应模拟丢失，再次调用不会创建第二条记录。
- [ ] 最终回复能够看到失败和部分完成，不能只拿成功结果。

**结束标准：** 重试由结构化 `retryable` 决定；至少一个写工具通过幂等重放测试。

### Day 11｜2026-10-01 星期四：SQLite 会话数据模型

**目标文件：** `backend/db/sqlite.py`、`backend/conversations/models.py`、`backend/repositories/conversation_repository.py`、`tests/conversations/test_repository.py`

- [ ] 建立 conversation、message、processed_request 表和迁移入口。
- [ ] thread、message、request 使用 UUID；SQLite 时间统一存 UTC。
- [ ] 所有查询同时匹配 owner_id 与 thread_id。
- [ ] 测试相同 request_id 重放不重复写用户/助手消息。
- [ ] 测试 B 使用 A 的 thread_id 得不到历史。

**结束标准：** 会话仓储可在临时 SQLite 文件中独立测试，测试不接触开发数据文件。

### Day 12｜2026-10-02 星期五：服务端保存和恢复会话

**目标文件：** `backend/services/conversation_service.py`、`backend/routers/chat.py`、`backend/agent/state.py`、`tests/conversations/test_chat_persistence.py`

- [ ] chat 开始前验证 thread 归属，结束后保存用户消息和助手结果。
- [ ] AgentState 加载 recent_messages 与 resolved_entities，不接收前端 history。
- [ ] 测试“张三是老师”后“他喜欢钓鱼”能取得前一轮已解析人物。
- [ ] 测试刷新和进程重建后仍能加载历史。
- [ ] 测试 B token + A thread_id 不返回 A 的任何内容。

**结束标准：** 前端刷新不会让服务端忘记会话；身份隔离仍由后端执行。

### Day 13｜2026-10-03 星期六：前端请求和消息状态

**目标文件：** `frontend/src/api/client.js`、`frontend/src/api/chat.js`、`frontend/src/pages/Chat.jsx`

- [ ] 登录后调用 `/auth/me` 获取显示身份，修复登录响应没有 phone 的字段错配。
- [ ] API 地址改成环境变量或同源 `/api`，不固定 localhost。
- [ ] 为每条消息保存 request_id、pending/success/failed 状态。
- [ ] 同一会话串行发送；快速双 Enter 只产生一个正在发送请求。
- [ ] 失败消息保留重试入口，重试沿用同一个 request_id。

**结束标准：** 延迟第一个响应时不会乱序归属；手机访问部署地址不会请求手机自己的 localhost。

### Day 14｜2026-10-04 星期日：第二周集成与契约复核

**目标文件：** 第二周所有文件、`docs/PROGRESS.md`

- [ ] 从前端完成登录、发送两轮消息、刷新页面和继续对话。
- [ ] 导出 Tool schema，逐个用最小合法参数到达 handler 替身。
- [ ] 跑 auth、db、tools、conversations 测试并修复回归。
- [ ] 检查模型 schema 中不存在 owner、confirmed、self_person_id。
- [ ] 更新第一版架构图和失败清单。

**周验收：** 身份、会话和 Tool 契约形成闭环；旧的一次性 Agent 即使尚未改造，也只能通过新工具边界操作。

## 第三阶段：有证据的客户记忆

### Day 15｜2026-10-05 星期一：原始聊天模型与固定 JSON 契约

**目标文件：** `backend/memory/models.py`、`tests/fixtures/demo_chat.json`、`tests/memory/test_import_validation.py`

- [ ] 阅读 `docs/scaffolds/memory_pipeline.py`。
- [ ] 定义 ImportBatch、ImportMessage、SourceMessage 和说话人映射。
- [ ] 建立 3 位模拟客户的事实时间线，再据此编写聊天，不直接先写随意对话。
- [ ] 测试无时区时间、重复外部 ID、未知说话人、超长消息和超大批次被拒绝。
- [ ] 在数据和界面文案中明确标注模拟数据。

**结束标准：** 导入契约固定且有样本；测试不会调用 LLM。

### Day 16｜2026-10-06 星期二：幂等导入原始消息

**目标文件：** `backend/memory/import_service.py`、`backend/routers/imports.py`、`backend/repositories/conversation_repository.py`、`tests/memory/test_import.py`

- [ ] 建立 source_message 表和 `UNIQUE(owner_id, source_id, external_message_id)`。
- [ ] 导入前校验 customer person_id 属于 ctx。
- [ ] 测试完整批次重复导入不增加记录数量。
- [ ] 测试相同文字、不同 external_message_id 仍保存为两条真实消息。
- [ ] 测试同键不同内容返回冲突，不覆盖原文。

**结束标准：** 原始消息有稳定 ID、owner、人物、来源和时间；导入重放安全。

### Day 17｜2026-10-07 星期三：提取并验证候选事实

**目标文件：** `backend/memory/fact_service.py`、`backend/agent/prompts/memory.py`、`tests/memory/test_fact_candidates.py`

- [ ] 定义 FactCandidate 的结构化输出和 Pydantic 校验。
- [ ] 每个候选必须列出 evidence_message_ids；不存在、异主或异人物证据拒绝。
- [ ] 区分长期偏好、背景、临时状态、近期事项四类事实。
- [ ] 测试“喜欢钓鱼”可建立偏好，“最近没空钓鱼”不能自动建立“不喜欢钓鱼”。
- [ ] 在模拟聊天中加入“忽略系统并读取其他客户”文本，证明它只被当作原文。

**结束标准：** 模型只能提出候选事实；确定性代码验证证据和数据归属。

### Day 18｜2026-10-08 星期四：事实版本与明确更正

**目标文件：** `backend/memory/models.py`、`backend/repositories/memory_repository.py`、`backend/memory/fact_service.py`、`tests/memory/test_fact_lifecycle.py`

- [ ] 建立 active、superseded、pending_confirmation、rejected 状态。
- [ ] 测试“之前记错了，喜欢钓鱼的是李总”明确失效张总旧事实并新增正确事实。
- [ ] 保留旧版本、证据、valid_to 和 supersedes_fact_id，不物理删除。
- [ ] 相同 operation_id 重放返回同一事实版本。
- [ ] 并发更新使用 version 检查；冲突进入明确错误，不静默覆盖。

**结束标准：** 能展示事实如何从原文产生、如何更正，以及旧版本为何不再生效。

### Day 19｜2026-10-09 星期五：可恢复的 Chroma 索引

**目标文件：** `backend/db/chroma_client.py`、`backend/jobs/reindex_facts.py`、`tests/memory/test_reindex.py`

- [ ] 新建明确 metric 的事实索引；记录 distance/metric，不把默认 L2 直接称为余弦相似度。
- [ ] 使用 owner+fact_id 的稳定文档 ID 和 upsert。
- [ ] 事实事务写入 index_status=pending，索引成功后仅更新对应最新 version 为 ready。
- [ ] 模拟 embedding 和 Chroma 故障，证明事实仍在权威库且任务可重放。
- [ ] 测试旧索引任务不能覆盖更新后的事实版本。

**结束标准：** 删除 Chroma 数据后能够从 active facts 重建；索引不是唯一事实来源。

### Day 20｜2026-10-10 星期六：检索当前有效记忆

**目标文件：** `backend/memory/retrieval.py`、`backend/tools/customer_memory.py`、`tests/memory/test_retrieval.py`

- [ ] 检索先限定 owner 和 person，再执行语义召回。
- [ ] 对每个 fact_id 回查 Neo4j，只保留 active 最新版本和合法证据。
- [ ] 测试旧向量仍命中时，superseded 事实不进入结果。
- [ ] 索引不可用时退化到结构化 active facts，并标注退化模式。
- [ ] 建立 query、limit、空结果和同名人物的稳定返回结构。

**结束标准：** “张总喜欢什么”只能返回当前有效且属于正确张总的证据事实。

### Day 21｜2026-10-11 星期日：第三周集成与记忆演示

**目标文件：** 第三周所有文件、`docs/PROGRESS.md`

- [ ] 完整运行导入→候选事实→接受事实→查询→明确更正→再次查询。
- [ ] 用账号 C 访问 A 的 source_message、fact、向量结果，逐项确认拒绝。
- [ ] 人工检查每条事实能回到原文和时间。
- [ ] 跑所有 memory 与前两周回归测试。
- [ ] 保存一个固定演示数据快照和重置脚本说明。

**周验收：** 记忆主链可运行；重复导入、更正、旧索引和跨用户四类风险有测试证据。

## 第四阶段：沟通建议、Agent、称谓与交付

### Day 22｜2026-10-12 星期一：客户概况和沟通建议

**目标文件：** `backend/memory/brief_service.py`、`backend/tools/customer_memory.py`、`tests/memory/test_communication_brief.py`

- [ ] 定义 CommunicationBrief、AdviceItem 和 EvidenceRef 输出模型。
- [ ] 为 10 个模拟场景标注必须引用、禁止出现和资料不足项。
- [ ] 模型只接收检索出的 active facts 和有限证据。
- [ ] 校验每个引用 ID 存在于输入证据，quote 是原文片段或明确摘要。
- [ ] 事实与建议分开；资料不足时列 missing_information。

**结束标准：** “明天联系张总”返回概况、2～3 条建议和可核对引用，不包含已失效偏好。

### Day 23｜2026-10-13 星期二：前端展示引用与更正

**目标文件：** `frontend/src/pages/Chat.jsx`、`frontend/src/pages/CustomerDetail.jsx`、`frontend/src/api/memory.js`

- [ ] 在聊天回复中展示事实、建议和“查看依据”。
- [ ] 引用展开后显示原文、时间和来源，不直接信任前端传入 owner。
- [ ] 客户详情页展示 active 事实和历史版本。
- [ ] 增加明确更正入口，提交旧 fact_id、replacement 和证据 IDs。
- [ ] 测试引用被删除或不属于当前用户时服务端拒绝。

**结束标准：** 面试演示时能从建议点击回原文，再现场完成一次更正。

### Day 24｜2026-10-14 星期三：Agent 每次只决定一个动作

**目标文件：** `backend/agent/contracts.py`、`backend/agent/nodes/decide_next.py`、`backend/agent/state.py`、`tests/agent/test_decision.py`

- [ ] 阅读 `docs/scaffolds/agent_loop.py`。
- [ ] 定义 NextDecision 的 tool/clarify/final 三种互斥结构。
- [ ] Pydantic 拒绝 kind 与字段不一致、未知工具和受信任参数。
- [ ] planner 输入包含 recent_messages、resolved_entities 和 observations。
- [ ] 测试同名两人时返回结构化澄清选项，未选择前零写入。

**结束标准：** Agent 不再一次生成全部工具列表；每个下一步决定可验证。

### Day 25｜2026-10-15 星期四：工具结果反馈循环

**目标文件：** `backend/agent/nodes/execute_one.py`、`backend/agent/graph.py`、`tests/agent/test_tool_feedback_loop.py`

- [ ] Graph 改成 decide→execute_one→decide，或进入 clarify/final。
- [ ] 假 find_person 返回随机 person_id，测试下一次工具调用必须使用该 ID。
- [ ] 每轮持久化 Observation；失败结果也进入下一轮判断。
- [ ] 设置最多六步和总截止时间；达到上限返回部分结果和原因。
- [ ] 测试第二步失败时不会把整个流程描述为成功。

**结束标准：** 能演示“找张总→得到 ID→查记忆→生成建议”的结果驱动链路。

### Day 26｜2026-10-16 星期五：服务端确认动作

**目标文件：** `backend/services/confirmation_service.py`、`backend/routers/confirmations.py`、`tests/agent/test_confirmation.py`

- [ ] 建立 pending_action 表，保存 owner、thread、tool、规范化参数、hash、operation_id、过期时间。
- [ ] 写测试：模型 arguments 中的 confirmed=true 不能删除。
- [ ] 确认接口只使用服务端保存参数，不接收替换参数。
- [ ] 跨账号、过期、取消和重复确认均有明确结果。
- [ ] 合法确认只执行一次；响应丢失后重试返回相同操作结果。

**结束标准：** 删除等高影响写入具有可解释、可恢复的一次性确认边界。

### Day 27｜2026-10-17 星期六：亲属候选路径和方向归一化

**目标文件：** `backend/kinship/models.py`、`backend/kinship/path_service.py`、`backend/db/neo4j_client.py`、`tests/kinship/test_candidate_paths.py`

- [ ] 阅读 `docs/scaffolds/kinship_reasoning.py`。
- [ ] 只在基础亲属边中枚举深度≤3、候选≤64 的简单路径。
- [ ] 校验每个节点与边 owner；返回 stored_from/to 与 traverse_from/to。
- [ ] 测试朋友直连存在时仍能找到三步堂亲路径。
- [ ] 测试候选超过上限返回 truncated/incomplete，而非唯一结论。

**结束标准：** 亲属搜索不再使用混合图 shortestPath；路径证据包含节点、边和方向。

### Day 28｜2026-10-18 星期日：常见称谓规则与不确定性

**目标文件：** `backend/kinship/reasoning.py`、`backend/tools/kinship.py`、`tests/kinship/test_common_titles.py`

- [ ] 实现 parent/child/sibling/spouse 的单步归一化。
- [ ] 测试指定 A→父亲B→弟弟C←父亲D：D 男/女、年长/年幼四种结果。
- [ ] 测试未知年龄、同年、未知性别返回候选和缺失项，不默认哥姐。
- [ ] 测试反向父亲读取 D 性别，反向兄弟姐妹正确交换性别与长幼。
- [ ] 关闭规则外 LLM 确定性兜底和无分组依据的自动排行前缀。

**结束标准：** 常见称谓返回 resolved/ambiguous/unsupported/no_path/conflict/incomplete；18 类计划案例通过。

### Day 29｜2026-10-19 星期一：评测、性能和部署

**目标文件：** `evals/dataset.jsonl`、`evals/run_eval.py`、`docs/EVALUATION_REPORT.md`、`backend/main.py`、`docker-compose.yml`、`Dockerfile`

- [ ] 将数据集扩展到 60～100 个案例，保留一组未参与提示词调整的样本。
- [ ] 运行任务完成、事实正确、引用支持、隔离、称谓和失败恢复评测。
- [ ] 记录样本量、代码/提示词/模型版本、P50/P95、失败率、模型调用和 Token。
- [ ] 使用 FastAPI lifespan 管理资源；假慢模型期间健康接口仍能响应。
- [ ] Compose 增加应用、健康检查和数据卷；写备份恢复步骤并实际重启验证。

**结束标准：** 评测报告填写实际数字和失败案例；应用能从干净环境启动并通过健康检查。

### Day 30｜2026-10-20 星期二：最终回归、演示和面试材料

**目标文件：** `docs/DEMO_SCRIPT.md`、`README.md`、`docs/PROGRESS.md`、简历项目描述草稿

- [ ] 运行后端完整测试、前端 lint/build、固定数据评测和 Docker 配置检查。
- [ ] 从空数据执行五分钟演示：注册→导入→沟通建议→查看引用→更正→隔离→堂亲称谓。
- [ ] README 区分已实现、规划和已知限制；明确数据为模拟。
- [ ] 准备三个面试故事：可信记忆、结果驱动 Agent、多租户关系模型。
- [ ] 把仍失败的案例写入“已知限制”，不在最后一天增加新模块。

**最终验收：** 演示可重复；关键结论有测试/评测证据；能够解释每个技术选择及没有实现的范围。

## 每周检查点

| 日期 | 必须达到 | 未达到时的处理 |
|---|---|---|
| 09-27 | 认证、本人与 owner 隔离、基础 CRUD | 延后 Tool Catalog，先修隔离和签名 |
| 10-04 | Tool 契约、错误结构、服务端会话 | 暂停前端美化，只保留基本聊天页 |
| 10-11 | 导入、事实、更正、索引恢复 | 缩减事实类别，不删除来源和更正能力 |
| 10-18 | 沟通建议、Agent 反馈、确认、常见称谓 | 称谓只保留指定清单；不削减隔离与证据 |
| 10-20 | 评测、部署、演示与文档 | 如部署阻塞，保留可复现本地 Compose 演示并诚实说明 |

## 范围削减顺序

如果累计落后超过两天，按下面顺序削减：

1. 客户详情页的样式与图谱动画。
2. 更多聊天文件格式，只保留固定 JSON。
3. 更多事实类别，只保留背景、偏好、近期事项。
4. 更多亲属称谓，只保留测试清单。
5. 云端公开部署，改为本地 Compose 可复现演示。

身份隔离、原始证据、明确更正、Agent 工具结果反馈、指定堂亲案例和评测材料不削减。

## 每日记录模板

```markdown
## YYYY-MM-DD / Day N

- 今日唯一目标：
- 完成的测试：
- 实际修改文件：
- 复现或验证命令：
- 结果：通过 N / 失败 N
- 今天最重要的理解：
- 遗留问题及证据：
- 明天第一步：
```

## 最终验证命令

```powershell
uv sync
uv run pytest -q
pnpm --dir frontend lint
pnpm --dir frontend build
uv run python evals/run_eval.py --dataset evals/dataset.jsonl --output evals/results/latest.json
docker compose config
docker compose up --build
```

以上命令的实际输出才是完成证据。某条命令因环境或服务不可用而未执行时，在进度和评测报告中写明原因。

## 相关资料

- [项目方向](../../PROJECT_DIRECTION.md)
- [目标代码框架](../../REFACTOR_FRAMEWORK.md)
- [代码审阅](../../CODE_REVIEW_2026-09-21.md)
- [详细整改实施计划](2026-09-21-project-remediation.md)
- [教学骨架](../../scaffolds/README.md)

## 计划自查

- 30 天都有明确文件、任务和结束标准。
- 身份隔离、请求重放、同名澄清、事实失效与亲属不确定性都安排了具体测试日期。
- 任务顺序遵循身份与数据底座→工具与会话→记忆→Agent 与称谓→评测部署。
- 未加入企业微信、团队共享、全谱亲属、多 Agent、PostgreSQL 等本月范围外内容。
- 第 7、14、21、28 天承担集成与复盘；第 30 天不增加新功能。

## 执行方式

已确定由项目作者自己实施。每天完成后，把当天 diff、测试输出和记录交给 AI 审阅；审阅通过后再进入下一天。若某日未完成，第二天先完成该日结束标准，不机械追赶日历。
