# ToolPlan、Direct、Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户已于 2026-10-11 授权实施；实际证据与待验证项见 [执行记录](../../evaluations/tool-strategies/progress.md)。

**Goal:** 在相同 LangGraph 业务执行与可靠性机制下切换三种工具决策模式，明确循环继续、暂停和终止条件，并支持可复现比较。

**Architecture:** 保留目标调度与公共执行器，增加策略适配器、确定性目标完成合同和循环策略。Direct/Discovery 使用原生 OpenAI SDK 产生下一步动作，由 LangGraph 节点保存、验证、执行和观察；ToolPlan 保留批量依赖步骤。

**Tech Stack:** 现有 LangGraph、LangChain/Pydantic、OpenAI SDK、PostgreSQL Checkpointer/预算/幂等仓储、Neo4j、Langfuse、SSE；不安装新依赖。

**Spec:** [设计合同](../specs/2026-10-11-tool-strategies-design.md)。以下为阶段计划，接口已随实施落地；checkbox 仅表示该项已完成相应验证。

## Global Constraints

- 不新增仓库测试代码；验证使用已有检查与手工场景，临时实验结果明确标注。
- 不自动 commit/push、不操作生产数据、不打印 .env 密钥；实施时重新读取仓库约束。
- 默认 plan；旧 Checkpoint 兼容 plan；已有请求模式固定，配置只改变新任务默认策略。
- 沿用请求级默认预算：4 目标、12 模型调用、16 业务尝试、60000 Token、3 重规划、3 澄清、300 秒活跃执行；部署环境覆盖优先。
- 新实验默认：发现操作最多 8 次/请求，连续无进展 3 轮/目标；正常下一步不是失败重规划。
- Direct/Discovery 首版每轮最多一个调用，不引入并行业务写入。
- 每个真实业务工具经过现有参数、权限、确认、幂等、业务回执、预算、审计路径。
- 每个节点写清职责、输入、输出，以及为何模型输出不能决定完成/权限。

## Review Focus

- 单个查询成功但目标字段未取得：继续，不误报完成。
- 失败后修复成功：保留失败审计，不永久阻塞完成；错误实体旧回执不能混入。
- 重试/中断/配置变更：原动作、原业务 ID、原模式恢复，不重新生成写操作。
- 跨用户目录和通用调用：搜索不能泄露能力，tool_call 不能绕过确认或身份隔离。
- 缓存重复/发现空转/模型提前结束：有界停止，不因未消耗 API 次数形成无限循环。

## Task 1：先统一目标完成与未解决问题判据

**Files:** Create `backend/agent/goal_completion.py`; Modify `backend/agent/contracts.py`, `backend/agent/nodes/classify_intent.py`, `backend/agent/goal_policy.py`, `backend/agent/nodes/goal_nodes.py`, `backend/agent/execution_policy.py`。

**Interfaces:** `build_completion_spec(goal: dict, extracted_info: dict) -> GoalCompletionSpec`; `evaluate_goal(spec: GoalCompletionSpec, records: list[dict], bindings: dict) -> GoalEvaluation`。结果为 SATISFIED/PENDING/BLOCKED，带 evidence_call_ids、missing_requirements、outcome、reason_code；只接受规范化并校验的工具事实。

- [x] 从现有意图、人物/关系类型与 Tool 回执构造完成合同；目标理解之后固定，决策模型不可改写。
- [x] GoalDraft 新增可空 query_spec，定义 kind/target_name/requested_fields。kind 与意图匹配，属性仅允许现有 PersonExtraction 白名单；查询爱好必须保留 hobbies 需求。修改现有分类提示词、build_goals 和 refine_goal 字段传递；旧 Checkpoint 走兼容合同，不每轮重新识别查询需求。
- [x] 分离“批次执行完毕”与“目标已满足”。plan 分支仍执行完整依赖批次，结束时统一核验业务合同。
- [x] 增加实体/属性/关系覆盖与有效回执检查；区分查询无记录、字段无记录与写操作未完成。
- [x] 调整失败汇总：历史失败审计不删除，但完成判据只受未解决失败影响；保留部分成功写入。
- [x] 手工验证：find_person 命中不能完成查询爱好；创建仅保存姓名不能完成含职业/出生年份的目标；NOT_FOUND 后允许的修复最终成功；错误实体回执不能证明目标。

**交付:** plan 的完成判断更准确，尚未切换任何调用模式。

## Task 2：目录、共同决策协议与策略状态

**Files:** Create `backend/agent/strategies/__init__.py`, `backend/agent/strategies/contracts.py`, `backend/agent/strategies/catalog.py`, `backend/agent/strategies/factory.py`, `backend/agent/strategies/tool_plan.py`, `backend/db/migrations/010_agent_request_strategy.sql`; Modify `backend/config.py`, `backend/agent/state.py`, `backend/agent/nodes/plan_tasks.py`, `backend/agent/nodes/goal_nodes.py`, `backend/routers/chat.py`, `backend/agent/request_idempotency.py`, `backend/db/agent_request_store.py`, `backend/db/init_postgres.py`。

**Interfaces:** `ToolCatalog.for_goal(state: AgentState) -> CatalogSnapshot`; `DecisionStrategy.propose(state: AgentState, catalog: CatalogSnapshot) -> StrategyDecision`; `get_strategy(mode: str) -> DecisionStrategy`。目录项包含工具名称、说明、模型可见参数 Schema、Schema hash、写风险；服务端身份参数不暴露。

- [x] 新配置 AGENT_TOOL_STRATEGY 接受 plan/direct/discovery，未知值启动失败。工厂与适配器首版只实现 plan，其余明确报暂未实现。
- [x] 根据 TOOL_ARGUMENT_MODELS 生成参数 Schema；复用已有说明，逐项比对定义中的字段和必填参数，避免维护第二套手写 Schema。
- [x] 新请求保存模式/策略版本/目录 hash，重试从请求与 Checkpoint 恢复；旧请求无模式回落 plan。不兼容目录变化给出明确错误。
- [x] 在 agent_request 增加 strategy_mode、strategy_version、catalog_schema_hash 的兼容迁移；与首次请求插入同一事务固定模式，重抢占不得覆盖。使用现有 init_postgres 迁移入口，不在模块导入时连库。
- [x] 定义规范化业务步骤与 provider ID 映射，decision_seq 重放稳定；接受决策和执行之间安排 Checkpoint 边界。
- [x] 手工核对：默认 plan 行为与原计划一致；QUERY_PERSON 目录无写工具；模型不得输入 owner_id/confirmed；运行中改变默认值不改变已有任务。

**交付:** 可插拔边界成立，plan 为唯一可执行适配器。

## Task 3：持久化循环保护与正常继续路由

**Files:** Create `backend/agent/strategies/loop_policy.py`, `backend/agent/nodes/strategy_nodes.py`, `backend/db/migrations/011_tool_strategy_loop_budget.sql`; Modify `backend/config.py`, `backend/agent/budget.py`, `backend/db/agent_budget_store.py`, `backend/db/init_postgres.py`, `backend/agent/nodes/observe_execution.py`, `backend/agent/nodes/ask_clarification.py`, `backend/agent/nodes/request_confirmation.py`, `backend/agent/graph.py`, `backend/agent/streaming.py`。当前最大 SQL 编号为 009，010/011 用于这两步；执行前如有新迁移须整体顺延。

**Interfaces:** `evaluate_loop(state: AgentState, goal: GoalEvaluation, observation: ExecutionObservation | None) -> LoopDecision`; `compute_progress(previous: dict, current: dict) -> ProgressUpdate`。结果包括 CONTINUE/SUCCESS/ASK/CONFIRM/WAIT/FAIL/BUDGET_STOP，与现有 GoalStatus 分离；新状态无进展计数须可恢复。

- [x] 持久化发现操作预占与结算，防重复记账且每个真正新尝试计费；沿用请求累计预算，恢复不能刷新额度。
- [x] 为普通下一步增加 CONTINUE 路由，不递增 replan_count；失败修复才消耗已有重规划额度，区分协议修复和网络 RetryPolicy。
- [x] 按设计合同实现目标满足、未解决错误、取消、WAIT、澄清、预算、提前结束修复与无进展边界。
- [x] 缓存命中也推进已接受决策的逻辑进度评估；指纹结合实体版本和目标覆盖，不能永久禁止合法重复读取。
- [x] 确认/澄清恢复返回原策略合适节点；WAIT 恢复原 pending_decision 执行位置，不重新规划。
- [x] 测量各分支 graph super-step，确保在 recursion_limit 前有收尾余量；GraphRecursionError 不能伪装成功或清空已完成操作。
- [ ] 核对模型、PG、Neo4j 单次超时与取消行为，300 秒累计预算不能取代实际调用超时。
- [x] 手工验证：重复缓存 3 轮无进展停止；模型连续提前结束失败；用户中断等待不消耗活跃时间；额度耗尽保留部分成功；多个独立目标正常继续。

**交付:** 新策略即使错误也有明确有限边界，不依赖模型自行停止。

## Task 4：原生 SDK Direct 适配器

**Files:** Create `backend/services/tool_calling_llm.py`, `backend/agent/strategies/direct.py`; Modify `backend/agent/nodes/strategy_nodes.py`, `backend/agent/nodes/execute_tools.py`（只改归一化输入与回填接口）, `backend/agent/audit.py`, `backend/agent/memory/context_selector.py`。

**Interfaces:** `invoke_tool_decision(messages: list[dict], tools: list[dict], operation_key: str) -> ModelToolResponse`; `normalize_direct_response(response: ModelToolResponse, state: AgentState) -> StrategyDecision`; `append_tool_result_messages(state: AgentState, decision: StrategyDecision, records: list[dict]) -> list[dict]`。

- [ ] 使用现有 OpenAI 客户端与 base_url，注册当前目标业务 tools 和 request_clarification 控制函数。先验证配置模型的 tools、parallel_tool_calls、strict 兼容能力；不默默假设兼容服务支持所有特性。
- [x] 保留完整 assistant tool_calls 消息与每个对应 tool_call_id 的工具结果；消息 ID 去重、按协议组裁剪。原始 model final 输出只作为 FINISH_CANDIDATE。
- [x] context_selector 新增 strategy 消费入口；不能把 tool 消息塞进当前仅允许 system/user/assistant 的筛选分支后被静默丢弃。基础会话上下文与当前目标协议消息分别组装，最终整体核算预算。
- [x] raw SDK 调用复用 PG 请求预算、模型结果缓存与安全 Langfuse generation；Token 估算包含 tools Schema、历史工具消息和输出预留。
- [x] 将一个业务调用归一化成现有执行器的一步；结果实体绑定来自已授权回执，代码校验目标范围与参数，不让模型编造人物 ID。
- [x] 把回执、目标缺失项与已成功写入送给下一轮，不能每轮重新分类整条请求或新增用户未请求的目标。
- [x] 手工完整验收：查询爱好；未找到后合法新增；同名澄清；写操作确认；执行成功后崩溃重试；工具参数错误修复；无 tool_calls 时未完成不能报成功。

**交付:** plan/direct 可配置切换，共用业务可靠性机制。

## Task 5：Discovery 适配器

**Files:** Create `backend/agent/strategies/discovery.py`, `backend/agent/strategies/tool_search.py`; Modify `backend/agent/strategies/catalog.py`, `backend/agent/nodes/strategy_nodes.py`, `backend/agent/audit.py`, `backend/agent/streaming.py`。

**Interfaces:** `search_tools(snapshot: CatalogSnapshot, query: str, limit: int = 5) -> SearchResult`; `describe_tool(snapshot: CatalogSnapshot, tool_id: str) -> ToolDescription`; `normalize_catalog_call(tool_id: str, args: dict, state: AgentState) -> StrategyDecision`。tool ID 与注册表真实业务工具对应，不允许任意函数导入或反射执行。

- [x] 提供 tool_search/tool_describe/tool_call 和简要允许能力目录，保留 Direct 的固定澄清控制入口；搜索/描述走控制分支，真实调用解包后进入公共 execute_tools。
- [x] 使用中文/英文别名与关键词匹配，固定排序及版本；上限 5 候选、搜索 4000 字符、描述 8000 字符；记录截断，完整性不足时不可伪装完整参数 Schema。
- [x] 搜索、描述、调用实时权限校验；风险判定、幂等、预算和审计使用内部业务工具名。
- [x] 发现记账、无进展保护和当前上下文 Token 限制全部生效；重复搜索、重复描述不能重置计数。
- [x] 手工验收：中文搜索；空搜索；未知 tool ID；重复 describe；禁止工具目录隔离；嵌套 delete_person 仍需确认；发现预算耗尽；已加载 Schema 后不强制重复描述。

**交付:** 三个模式均可运行；Discovery 保持实验性，默认仍 plan。

## Task 6：三模式评估与交付

**Files:** 复用现有 Langfuse/SSE；记录脱敏评估结果到 `docs/evaluations/tool-strategies/`。不新增自动化测试代码或虚构业务集成。

- [x] 固定一组合法输入与起始数据，三模式各多次运行。通过实际人物/属性/关系及回执核对成功，不以回复“成功”作为验收。最终真实模型仅一个模拟只读场景各 3 次，其余故障场景使用离线替身。
- [x] 对比业务完成率、模型调用次数、Token、延迟分布、参数修复、澄清、预算停止与崩溃恢复重复写入；记录模型/提示词/策略/目录版本。证据分别标明真实模型与模拟故障；生产完成率未评估。
- [ ] 使用 11 个真实工具测小目录；额外的大目录明确标记模拟且使用相同模拟执行条件，不宣称生产接入。
- [ ] 检查 SSE 不泄露原始图状态/提示词，Langfuse 每次调用只上报一次，并能追踪外层发现到内部真实业务工具。
- [x] 执行与改动相关的现有检查；没有现成覆盖的故障场景采用临时手工注入，不运行真实破坏性写入。
- [x] 列出已验证与未验证能力、观察到的取舍及默认策略依据。未实测不写速度/成本提升百分比。

**交付:** 可演示、可解释、可比较的三模式实现，而非三套复制代码。

## 学习顺序与实施顺序

先完成 Task 1 理解“目标成功”和“工具成功”的差别；Task 2 理解策略边界；Task 3 理解确定性循环控制；Task 4 实际走通原生调用/结果回填；Task 5 学习能力发现；Task 6 用证据选择默认方案。

每个任务应分别完成与验证，再进入下一项。编写代码前先按现有未提交改动重新核对路径与接口；这里没有自动提交或推送授权。
