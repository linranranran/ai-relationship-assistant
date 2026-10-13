# ToolPlan、Direct、Discovery 策略与循环边界设计

日期：2026-10-11。状态：开发方案，尚未实施。

## 目标与范围

在现有 LangGraph 中切换三种工具决策模式，用相同业务任务验证成功率、成本和可靠性。保留多目标调度、三层记忆、用户隔离、确认、中断恢复、PostgreSQL 预算与幂等账本、Neo4j 业务回执、SSE、Langfuse。

本轮只写设计与计划，不改业务代码、数据库或依赖，不 commit/push。后续实施遵循用户已有偏好：注释说明为什么这样做，不新增仓库测试代码，使用已有检查与手工验收。本文中的配置值是初始实验值，不代表实测最优值。

## 核对到的现状

- `services/structured_llm.py` 使用 Function Calling 生成 ToolPlan；它不是与 SDK tools 参数互斥的技术。
- `agent/contracts.py` 的 ToolStep 采用 `tool: str`、`args: dict[str, Any]`，模型侧对单个业务参数约束较弱。
- `agent/goal_policy.py` 的 ALLOWED_TOOLS 按目标筛选；注册表共 11 个业务工具。
- `execution_policy.py` 无失败时返回 COMPLETE；`observe_execution.py` 随后进入 finalize_goal。这只能代表当前批次完成。
- `goal_policy.py:completion_evidence` 部分检查依赖工具名称与成功回执，且出现 FAILED/SKIPPED 即影响完成；不能原封不动作为逐步策略的完成判据。
- `execution_policy.py` 中 MAX_AUTO_REPLANS=1，与 config 中请求级重规划预算默认 3 是两个边界；实施时明确前者为旧策略局部规则，不让三种模式分别维护不同隐含上限。
- 当前默认请求预算：4 目标、12 次模型调用、16 次业务工具尝试、60000 Token、3 次重规划、3 次澄清、300 秒活跃执行；图 recursion_limit=64。环境覆盖值需实施时核对，不能将代码默认值当实际生效值。

## 选择的架构

采用一个 StateGraph、三个决策适配器、一套业务执行器与完成策略。继续使用原生 LangGraph 的 State、Command、interrupt、Checkpointer、流式事件，不另写 Python while True，也不引入新的 Agent 框架、Celery、Redis 或向量检索依赖。

比较维度分开：ToolPlan/Direct 是决策形式；Discovery 是工具可见性与发现协议。对外提供三个预设，内部不建设任意组合矩阵。

| 配置值 | 模型输出 | 工具可见性 |
| --- | --- | --- |
| plan | ToolPlan 的带依赖步骤 | 当前目标允许工具的目录 |
| direct | 原生 OpenAI Chat Completions tools 的业务调用 | 当前目标允许工具的参数 Schema |
| discovery | search / describe / call 的原生 Function Calling | 简要能力目录、三个发现入口 |

默认仍为 plan。Direct/Discovery 首版每轮最多一个调用，使用 parallel_tool_calls=false；不能假定兼容服务执行了此参数，收到多个调用时按协议错误处理，完整记录并对每个 provider call ID 补齐非执行结果，再有界修复，不能静默丢弃一部分后回填残缺消息。

## 共同协议与状态

策略只提出动作，不执行业务、注入身份或宣布成功。决策类型：EXECUTE_BATCH、SEARCH、DESCRIBE、ASK_CLARIFICATION、FINISH_CANDIDATE。ToolPlan 可以提出多步骤批次；Direct/Discovery 的业务调用归一化成单步骤批次。

所有业务步骤保留 step_id、服务端 call_id、tool、args、depends_on。provider_tool_call_id 单独保存，用于工具消息配对，不作为业务幂等唯一标识。标识包含 request_id、goal_id、已持久化 decision 序号与步骤序号；同一个已接受决策重放必须使用相同标识。

策略新增状态：strategy_mode、strategy_version、catalog_schema_hash、decision_seq、pending_decision、strategy_messages、finish_repair_count、progress_snapshot、stop_reason。已有 completed_mutations 与工具事实继续使用，不复制另一个幂等仓储。整个任务的策略随请求与 Checkpoint 固定；重试读取已存模式。旧 Checkpoint 缺少模式时使用 plan。

每个决策节点先保存规范化动作、模型调用消息和业务调用 ID，再进入单独执行节点。崩溃重放复用 PG 模型结果与工具回执。决策逻辑重试不能生成新业务步骤。工具消息使用稳定 message ID 防止恢复时重复追加；上下文裁剪保留完整的 assistant tool_calls 与对应 tool 消息组。

目录哈希描述 Schema 快照，不代表永久授权。每次搜索、描述与真实调用重新校验当前用户权限；新任务可以采用新目录，旧任务遇到不兼容 Schema 不能悄悄迁移。

## 目标完成合同

在目标验证/提取后构造经过代码校验的 GoalCompletionSpec；不能让工具决策模型随意改写合同。字段包括目标类型、人物绑定/待消歧目标、必须覆盖的属性或关系、可接受结果类型。自然语言理解仍有误差，合同不是语义正确性的数学保证。

目前 GoalDraft 没有“查询需要哪些字段”的结构化信息，不能靠 description 字符串判断爱好是否已经回答。扩展 GoalDraft 的 query_spec：kind 为 person_detail/relation_path/kinship_title/semantic_matches/relation_list 之一，target_name 可空，requested_fields 采用现有 PersonExtraction 字段的白名单。查询理解在原 classify_intent 中完成并经过代码校验，不每一轮额外做意图识别。没有指明具体属性的资料查询使用完整详情合同；旧 Checkpoint 缺失 query_spec 时按其原始目标与有效查询回执采用兼容合同，不伪造新字段要求。实际人物 ID 仍须来自授权查询/用户消歧。

evaluate_goal 返回 SATISFIED、PENDING 或 BLOCKED，以及证据 call_id、缺失项、停止原因。它从现有工具结果按实体、操作和结果核对，不解析模型的“已完成”文案，也不依据工具数量判断成功。

- 查询人物爱好：唯一绑定的目标 ID + get_person_detail 回执中的对应字段。数据库字段为空可完成查询，但回复为“暂无记录”，不能编造爱好。
- 精确查询未找到：仅在该查询的业务合同允许时，作为查询已完成、结果 NOT_FOUND；它不证明人物已创建，也不能自动新增。
- 新增人物：确认正确人物实体与请求属性已保存；仅 find_person 命中或得到 ID 不足以完成包含新属性的目标。
- 新增关系：核对双方 ID、关系类型及有效成功回执；仅创建其中一个人物不能完成关系目标。
- 更新/删除：核对目标实体及操作后的回执。写回执已充分证明提交时不额外强制读库；回执缺少字段才补查询。
- 历史失败留在审计中；已修复失败不得永久阻止完成。只有未解决的问题阻塞。不得让错误的旧实体回执、已被后续写入失效的快照证明当前目标。

没有支持的完成合同则按不支持处理，不能静默降级成“模型说完成就完成”。首版覆盖项目现有意图，不引入通用语言任务验收模型。

## Direct 循环

图流：select_next_goal → extract_info（按现有意图需要）→ strategy_dispatch → decide_next → validate_decision → execute_tools → observe_execution → decide_next / finalize_goal / ask_clarification / request_confirmation / WAIT / finish_budget。

tool1 执行后，代码先处理错误与目标完成条件。完成则不再调用决策模型；未完成且可继续，则把真实回执、缺失项、已成功写操作和允许工具交给模型，模型提出 tool2，代码再验证工具范围、具体实体、参数与预算。

普通未带 tool_calls 的模型输出只代表 FINISH_CANDIDATE。SATISFIED 才成功；PENDING 时允许一次携带缺失项的修复（也消耗模型/Token 预算）。第二次仍提前结束，当前目标以 MODEL_STOPPED_EARLY 失败收尾。真正缺少用户信息或实体歧义才中断澄清，不能把模型协议错误都交给用户解决。

Direct 注册的业务 tools 之外可提供一个固定 request_clarification 控制函数。它提出问题和缺失项，由图节点检查后调用现有 interrupt；不是数据库工具。FINISH_CANDIDATE 不直接作为用户最终回复，沿用 generate_response 汇总已核验结果。

## Discovery 循环

与 Direct 共用循环，保留固定的 request_clarification 控制入口，仅增加 SEARCH/DESCRIBE 分支：搜索返回允许目录的紧凑候选；描述返回指定工具的 Schema；call 将目标业务工具解包，进入同一个 execute_tools。描述按需使用，不强制每次 search → describe → call。

初版目录来自现有 TOOL_ARGUMENT_MODELS 和工具说明；搜索使用中文/英文别名及明确的关键词匹配，不新增 Embedding/向量库。记录算法版本、召回结果、空结果和截断标记；不得伪称完全复刻 OpenClaw 的英文 BM25。后续只有数据证明词法检索不足才升级。

初始限制：单次返回最多 5 个候选；搜索响应最多 4000 字符并经过现有 Token 预算；描述最多 8000 字符。超限返回 truncated，不静默省略关键必填字段；信息不完整时不能假装已获得完整 Schema。

tool_call 的通用 args 仍须通过目标工具的 Pydantic 校验；模型端 generic wrapper 不等于目标参数严格 Schema。无权限工具在目录、搜索、描述与调用四处均不可见/不可执行。风险确认按内部真实工具判定，例如 tool_call(delete_person) 必須经过 delete_person 确认。

搜索/描述不是业务完成证据。每次会消耗发现额度，其后的模型请求消耗模型额度，不能让“没有真实业务调用”成为无成本循环。

## 循环边界与预算

业务处理采用确定性优先级：取消/禁止操作/结果不确定先阻止新动作；明确歧义进入澄清；核对已成功事实；SATISFIED 收尾；PENDING 且额度充足才能让模型继续。完成检查不耗费模型，已完成目标不会因缺少下一次模型额度被错误标失败；回复无额度时使用现有确定性收尾。

| 场景 | 处理 |
| --- | --- |
| 目标证据满足 | finalize_goal → 下一个独立目标；全部目标终态后汇总 |
| NOT_FOUND | 根据当前目标语义完成负向查询或继续合法分支，不据此随意新增 |
| AMBIGUOUS / 用户可解决的 CONFLICT | interrupt；暂停不是成功、失败或预算重置 |
| PERMISSION_DENIED / 不可恢复系统错误 | 当前目标失败；依赖目标阻塞，其他独立目标按预算继续 |
| IN_PROGRESS / 暂时不可用 / 写结果不确定 | WAIT 或核对已有回执，原 request_id 恢复；不让模型换 call_id 重写 |
| 参数协议错误 | 有界修复；普通下一步不计作失败重规划 |
| 成功证据不足但模型停止 | 最多一次提前结束修复，仍不足则失败 |
| 请求预算耗尽 / 活跃时间超限 | 请求级确定性收尾，保留部分成功与未知结果 |

沿用全请求 PG 预算默认值，不自动提高预算以让 Discovery 看起来成功。增加持久化的发现操作上限 AGENT_TASK_MAX_DISCOVERY_CALLS=8，每次搜索/描述均记账。增加无进展阈值 AGENT_MAX_NO_PROGRESS_ROUNDS=3；这两个值为实验默认值。

进展由代码产生：新实体绑定、目标证据覆盖增加、一次新的有效 Schema 加载；首次获得有意义的负向查询结果也可算分支进展。仅重写查询、换 tool ID、重复返回旧 ID、重复描述同一 Schema 或模型声称进展都不算。连续 3 个已接受决策轮无进展停止当前目标为 LOOP_NO_PROGRESS；缓存命中也需要纳入逻辑轮计数，不能因未消耗真实 API 额度无限循环。

相同参数指纹只是检测信号，不永久禁用再次调用。合法修改后再读、有限轮询或 A→B→A 操作需要结合实体版本/明确业务语义；首版不做后台自动轮询，IN_PROGRESS 走 WAIT。读/写/发现操作分别记录，不使用“总调用次数”一个混合指标。

图 recursion_limit=64 是单次 invoke/stream 的 super-step 保险，不是请求总工具额度，也不是正常成功条件。一个决策轮可能经过多个节点，64 未必容纳完整 Discovery 预算；在实施阶段测量节点消耗、预留收尾节点，过早撞限按 GRAPH_LIMIT 收尾，并保留恢复事实。不得在预算耗尽时重新调用图以绕开限制。

活跃时间预算需要配合模型和 DB 单次超时：下一节点检查无法中止一个永久阻塞的调用。确认等待不计活跃时间。取消/超时不是业务回滚；正在提交的写操作必须保留可核对回执，不能宣称全部未执行。

## 可观测与公平比较

审计保存 strategy/version、目录版本、goal_id、decision_seq、调用参数与脱敏输出、provider ID 与业务 ID 对应、预算消耗、缺失项/证据/停止原因。记录业务工具名称，不能只看到外层 tool_call。记录搜索/描述与业务操作分层跨度。沿用当前 Langfuse 安全导出，原生 SDK 手动创建 generation，避免重复上报。

SSE 复用现有安全事件格式，补 strategy.deciding、tool.searching、tool.describing、goal.evaluated、loop.stopped。前端只收到可展示的进度和业务结果，不直接推送提示词、原始状态或隐藏推理。

三种策略使用相同业务输入、模型、预算、确认与隔离条件，分别记录提示词版本；策略要求不同，不强求提示词逐字相同。比较实际模型次数、完整输入/输出 Token、端到端延迟、正确任务完成、错误参数、恢复重复写入。Discovery 分小目录与模拟大目录测量，模拟工具不能写成生产集成。多次运行后报告结果分布，不从单次成功外推胜负。

## 资料

- https://docs.langchain.com/oss/python/langgraph/workflows-agents ：标准调用/结果/下一轮循环。
- https://docs.langchain.com/oss/python/langgraph/graph-api ：Command、State、消息和 recursion_limit。
- https://developers.openai.com/api/docs/guides/function-calling ：应用负责执行与回填，strict 的边界。
- https://github.com/openclaw/openclaw/blob/main/docs/tools/tool-search.md ：发现协议与小目录成本。
