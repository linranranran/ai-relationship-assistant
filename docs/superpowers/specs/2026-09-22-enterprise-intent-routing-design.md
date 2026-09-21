# 企业级多意图路由与运行时规划设计

- 日期：2026-09-22
- 状态：设计已确认，等待书面审阅
- 项目：AI 人际关系助手
- 目标周期：1 个月，每天 4–6 小时

## 1. 背景与目标

当前 Agent 使用单标签意图分类：一次请求只得到一个 `intent`，随后根据硬编码列表进入信息提取或任务规划。该方式无法可靠处理以下场景：

- 一句话包含多个相互依赖的目标。
- 后续轮次使用“他”“那个客户”等省略表达。
- 用户粘贴客户聊天记录，其中出现“删除”“修改”等词，但这些词不是系统操作指令。
- 后续 Tool 需要使用前一步查询得到的 `person_id` 或 `relation_id`。
- 查询出现零个、一个或多个候选时，需要走不同分支。
- 删除、覆盖、批量操作需要暂停并等待用户确认。
- 服务中断后需要恢复未完成计划。
- 意图、槽位、计划和最终执行效果需要独立评测与追踪。

本设计将当前单标签分类器改造成一套分层路由系统：

```text
输入边界识别
    → 上下文组装
    → 多意图结构化分析
    → 指代与实体解析
    → 槽位和策略校验
    → 任务 DAG 编译
    → 运行时结果绑定
    → 自动执行 / 澄清 / 人工确认
    → 记忆写入与可观测性
```

成功标准：系统能把自然语言可靠转换成可解释、可校验、可恢复的任务计划，并通过离线评测证明质量。

## 2. 范围

### 2.1 第一版覆盖

- 人物增删改查和语义搜索。
- 人物关系增删改查。
- 三代以内常见亲属称谓查询。
- 手动粘贴客户聊天记录。
- 聊天总结、客户画像提取、需求分析。
- 跟进建议、沟通要点和销售风险提醒。
- 单轮多意图和跨轮指代。
- 分级自动执行、澄清和人工确认。
- 会话 checkpoint、长期记忆、离线评测和 Trace。

### 2.2 第一版不覆盖

- 企业微信自动同步。
- 任意第三方 Skill 动态安装。
- 跨租户人物自动合并。
- 自动发送消息给客户。
- 模型训练或微调。
- 无限制自主规划。

## 3. 核心设计原则

1. **业务意图与 Tool 分离**：意图表达用户目标，一个意图可以编排多个 Tool。
2. **模型负责理解，代码负责约束**：风险、权限、槽位、结果绑定和最终路由由确定性代码控制。
3. **前一步结果驱动下一步**：所有资源 ID 必须来自可信上下文或已执行 Tool，不能由模型编造。
4. **低置信度不盲猜**：缺少槽位、多候选和指代不明确时，只询问一个最关键问题。
5. **危险操作可恢复确认**：通过 checkpoint 和 `interrupt()` 暂停，使用同一 `thread_id` 恢复。
6. **先执行成功，再写长期记忆**：分类器和提取器不能直接污染客户画像。
7. **所有判断可解释、可评测、可追踪**：保留证据、版本、决策原因和执行结果。

## 4. 技术版本边界

项目当前固定使用 `langgraph==0.2.61`，而 2026-09-21 的 PyPI 稳定版为 `1.2.12`。当前官方文档围绕 1.x 的持久化、Store、`interrupt()` 和事件流 API 编写。

实施前必须单独完成依赖升级验证：

1. 建立最小 LangGraph 1.x 测试图。
2. 验证 `Command` 路由、checkpointer、Store、`interrupt()` 和恢复执行。
3. 验证现有 DeepSeek/OpenAI 兼容客户端的结构化输出。
4. 升级后运行现有 Agent 场景测试。
5. 通过后锁定精确版本，禁止使用浮动版本。

如果升级验证失败，则保留 0.2.61，并只使用该版本实际具备的 API；不能把 1.x 示例直接复制到旧版本代码中。

参考资料：

- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangChain Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output)
- [LangGraph PyPI](https://pypi.org/project/langgraph/)

## 5. 总体架构

### 5.1 节点职责

| 节点 | 职责 | 不负责 |
|---|---|---|
| `load_context` | 读取可信身份、会话焦点、待确认计划和相关长期记忆 | 不调用业务 Tool |
| `classify_input_boundary` | 区分 `COMMAND`、`CONTENT`、`MIXED` | 不产生写操作 |
| `analyze_intents` | 生成结构化多意图任务帧和原文证据 | 不决定风险和是否执行 |
| `resolve_entities` | 将姓名、代词和描述绑定到候选 Person | 不擅自选择近似候选 |
| `validate_tasks` | 校验意图、槽位、依赖和实体绑定 | 不调用 LLM 补造 ID |
| `evaluate_policy` | 计算策略分数、风险和路由决策 | 不执行业务写入 |
| `compile_plan` | 把业务任务编译成带依赖的 Tool DAG | 不写死运行时查询结果 |
| `select_ready_steps` | 找出依赖已经满足的步骤 | 不改变步骤参数 |
| `resolve_arguments` | 从前置步骤结果绑定 Tool 参数 | 不执行任意表达式 |
| `execute_step` | 执行一个或一组安全的 Ready Step | 不解析自然语言错误文本 |
| `evaluate_result` | 根据标准 ToolResult 选择继续、澄清、确认或重规划 | 不无限重试 |
| `request_clarification` | 生成一个最小澄清问题 | 不一次询问多个字段 |
| `request_confirmation` | 暂停高风险计划并展示变更摘要 | 不在确认前执行写入 |
| `write_memory` | 将已验证事实写入长期记忆 | 不保存失败或未确认事实 |
| `generate_response` | 汇总成功、失败和待处理步骤 | 不隐藏部分失败 |

### 5.2 主流程

```text
START
  → load_context
  → classify_input_boundary
  → analyze_intents
  → resolve_entities
  → validate_tasks
  → evaluate_policy
      ├─ CLARIFY → request_clarification → END
      ├─ CONFIRM → request_confirmation → resume
      ├─ REJECT  → generate_response → END
      └─ EXECUTE → compile_plan
                    → select_ready_steps
                    → resolve_arguments
                    → execute_step
                    → evaluate_result
                        ├─ CONTINUE → select_ready_steps
                        ├─ CLARIFY  → request_clarification
                        ├─ CONFIRM  → request_confirmation
                        ├─ REPLAN   → compile_plan（最多两次）
                        └─ COMPLETE → write_memory
                                      → generate_response
                                      → END
```

## 6. 意图体系

### 6.1 关系管理

```text
PERSON_CREATE
PERSON_UPDATE
PERSON_DELETE
PERSON_QUERY
PERSON_SEARCH
RELATION_CREATE
RELATION_UPDATE
RELATION_DELETE
RELATION_LIST
KINSHIP_QUERY
```

### 6.2 销售助手

```text
CHAT_SUMMARIZE
CUSTOMER_PROFILE_EXTRACT
CUSTOMER_NEED_ANALYZE
SALES_FOLLOWUP_ADVICE
SALES_TALKING_POINTS
SALES_RISK_DETECT
```

### 6.3 通用意图

```text
CHITCHAT
HELP
OUT_OF_SCOPE
UNKNOWN
```

`UNKNOWN` 表示系统没有可靠识别；`OUT_OF_SCOPE` 表示系统理解了请求，但当前产品不支持。

## 7. 意图注册表

每个意图使用代码注册，不把策略散落在 Prompt 中：

```python
@dataclass(frozen=True)
class IntentDefinition:
    code: IntentCode
    domain: Domain
    description: str
    required_slots: frozenset[str]
    optional_slots: frozenset[str]
    risk_level: RiskLevel
    planner_key: str
    memory_policy: MemoryWritePolicy
```

注册表示例：

```python
IntentCode.PERSON_QUERY: IntentDefinition(
    code=IntentCode.PERSON_QUERY,
    domain=Domain.RELATIONSHIP,
    description="查询指定人物的资料",
    required_slots=frozenset({"person_reference"}),
    optional_slots=frozenset(),
    risk_level=RiskLevel.READ,
    planner_key="person_query",
    memory_policy=MemoryWritePolicy.NONE,
)
```

Prompt 中的意图说明由注册表生成，避免 Prompt、路由代码和测试集各维护一份枚举。

## 8. 结构化数据模型

### 8.1 模型输出

```python
class IntentAnalysis(BaseModel):
    schema_version: str
    input_type: InputType
    domains: list[Domain]
    tasks: list[IntentTask]
    unresolved_references: list[ReferenceMention]


class IntentTask(BaseModel):
    task_id: str
    intent: IntentCode
    raw_arguments: dict[str, Any]
    evidence: list[str]
    model_confidence: float
    depends_on: list[str]
```

模型不能输出以下最终值：

- `owner_id`
- `self_person_id`
- 最终 `person_id`、`relation_id`
- `risk_level`
- `decision`
- `confirmed`

### 8.2 解析后的任务

```python
class ResolvedTask(BaseModel):
    task_id: str
    intent: IntentCode
    arguments: dict[str, Any]
    entity_bindings: list[EntityBinding]
    missing_slots: list[str]
    risk_level: RiskLevel
    policy_score: float
    decision: RouteDecision
    depends_on: list[str]
```

### 8.3 AgentState

```python
class AgentState(TypedDict, total=False):
    # 可信请求上下文
    request_id: str
    thread_id: str
    user_id: str
    self_person_id: str

    # 原始输入和会话
    messages: Annotated[list[AnyMessage], add_messages]
    user_input: str
    input_segments: list[InputSegment]
    conversation_context: ConversationContext

    # 意图与解析
    intent_analysis: IntentAnalysis | None
    resolved_tasks: list[ResolvedTask]

    # 规划与执行
    execution_plan: ExecutionPlan | None
    step_executions: dict[str, StepExecution]
    step_results: dict[str, ToolResult]
    replan_count: int

    # 人机协作
    pending_clarification: ClarificationRequest | None
    pending_confirmation: ConfirmationRequest | None

    # 输出和诊断
    response: str
    errors: list[ExecutionError]
    trace_context: AgentTraceContext
```

迁移期保留现有 `intent`、`tool_calls`、`tool_results` 字段，由适配器同步；新节点不能继续扩展旧字段。

## 9. 输入边界

用户手动粘贴聊天时，必须先区分操作指令和被分析内容：

```python
class InputSegment(BaseModel):
    segment_id: str
    segment_type: SegmentType  # COMMAND | CONTENT
    text: str
    source_span: TextSpan
```

示例：

```text
帮我分析下面聊天并给出跟进建议：     → COMMAND
客户：这个版本先删掉，下周再讨论。   → CONTENT
```

`CONTENT` 中的删除、修改等词不能直接生成写操作。无法可靠分段时把整体标记为 `MIXED`，进入澄清。

## 10. 指代和实体解析

候选来源顺序：

1. 待确认操作中的目标人物。
2. 当前任务 DAG 已产生的人物。
3. 会话焦点人物栈。
4. 最近明确提及的人物。
5. Neo4j 精确姓名和关系查询。
6. 模糊或语义候选。

候选分数由代码计算：

```python
final_score = weighted_sum(
    exact_name_match,
    context_recency,
    role_compatibility,
    gender_compatibility,
    relation_compatibility,
    model_signal,
)
```

初始策略：

```python
if best.score >= 0.85 and best.score - second.score >= 0.15:
    bind(best.person_id)
elif only_one_candidate and best.score >= 0.75:
    bind(best.person_id)
else:
    clarify_with_candidates()
```

阈值必须配置化，并通过评测集调整。

## 11. 策略分数与风险

模型自报分数只是一个输入信号：

```python
policy_score = (
    intent_signal * 0.25
    + evidence_coverage * 0.20
    + slot_completeness * 0.20
    + entity_resolution_score * 0.20
    + context_consistency * 0.15
)
```

初期只能称为“启发式策略分数”。完成标注集校准后，才可以称为“校准置信度”。

风险矩阵：

| 风险 | 示例 | 策略 |
|---|---|---|
| `READ` | 查询、总结、生成建议 | 达到阈值后自动执行 |
| `LOW_WRITE` | 新增人物、关系、客户标签 | 高置信度自动执行并告知 |
| `MEDIUM_WRITE` | 修改姓名、替换关系、批量画像 | 展示变更摘要后执行 |
| `HIGH_WRITE` | 删除、覆盖、批量删除 | 必须 interrupt 确认 |
| `FORBIDDEN` | 跨租户访问、伪造 owner | 直接拒绝 |

初始阈值：

```text
READ_EXECUTE_THRESHOLD = 0.75
LOW_WRITE_EXECUTE_THRESHOLD = 0.85
CLARIFY_THRESHOLD = 0.55
```

出现以下情况时忽略总分并强制澄清：

- 必填槽位缺失。
- 存在多个人物候选。
- 指代没有解析。
- `MIXED` 输入边界无法区分。
- 任务相互冲突。
- Tool 结果推翻计划前提。

## 12. 任务 DAG 与运行时结果绑定

### 12.1 计划步骤

```python
class ResultBinding(BaseModel):
    source_step_id: str
    path: str


class PlanStep(BaseModel):
    step_id: str
    tool_name: str
    static_arguments: dict[str, Any]
    argument_bindings: dict[str, ResultBinding]
    depends_on: list[str]
    condition: StepCondition | None
    output_alias: str | None
    failure_policy: FailurePolicy
    idempotency_key: str | None
    risk_level: RiskLevel
```

`ResultBinding.path` 只能访问 Tool 契约声明的白名单字段，不能执行 Python、JSONPath 脚本或任意表达式。

`ResultBinding`、`idempotency_key` 和最终 Tool 参数由代码中的计划编译器生成并校验，不接受模型直接提供的资源 ID 或幂等键。

### 12.2 查询人物详情示例

```text
find_person_exact
    ├─ 0 个结果 → find_person_fuzzy
    ├─ 1 个结果 → 绑定 person_id → get_person_detail
    └─ 多个结果 → request_clarification
```

后续步骤的 `person_id` 来源：

```python
ResultBinding(
    source_step_id="find_person",
    path="data.person_id",
)
```

### 12.3 执行规则

- 依赖满足的只读步骤允许并行。
- 写步骤默认串行。
- 写步骤必须有幂等键。
- 失败步骤阻止所有依赖步骤进入 Ready。
- 重规划最多两次。
- 同一计划签名不能连续重规划。
- 确定性分支直接由代码处理，不为取一个 ID 再调用一次 LLM。

## 13. Tool 契约

所有 Tool 返回统一结构：

```python
class ToolResult(BaseModel):
    success: bool
    code: ToolResultCode
    message: str
    data: dict[str, Any]
    retryable: bool = False
    affected_count: int = 0
    candidates: list[dict[str, Any]] = []
    error_type: ErrorType | None = None
```

执行器只判断错误码，不解析中文 `message`：

```text
SUCCESS
PERSON_NOT_FOUND
MULTIPLE_PERSONS_FOUND
RELATION_NOT_FOUND
VALIDATION_ERROR
PERMISSION_DENIED
CONFLICT
DEPENDENCY_UNAVAILABLE
TIMEOUT
INTERNAL_ERROR
```

## 14. 运行时控制器

```python
def execute_plan(state: AgentState) -> Command:
    ready_steps = select_ready_steps(state.execution_plan, state.step_executions)

    if not ready_steps:
        return evaluate_terminal_or_blocked_state(state)

    for step in safe_execution_batch(ready_steps):
        arguments = result_resolver.resolve(
            step=step,
            step_results=state.step_results,
        )

        validation = tool_argument_validator.validate(step.tool_name, arguments)
        if not validation.valid:
            return Command(update=..., goto="request_clarification")

        policy = policy_engine.evaluate(step, arguments)
        if policy.requires_confirmation:
            return Command(update=..., goto="request_confirmation")

        result = tool_executor.execute(
            tool_name=step.tool_name,
            arguments=arguments,
            idempotency_key=step.idempotency_key,
        )
        record_step_result(state, step, result)

    return route_after_results(state)
```

规划器只生成计划，执行器不偷偷修改计划；需要修改时产生新 `plan_version`。

## 15. 记忆设计

### 15.1 工作记忆

- 本轮输入和分段结果。
- IntentAnalysis。
- 当前 DAG、步骤状态和结果。
- 当前候选人物。

### 15.2 会话记忆

由 LangGraph checkpointer 通过 `thread_id` 保存：

- 最近提及人物和焦点客户。
- 待澄清问题。
- 待确认操作。
- 未执行完成的计划。

### 15.3 长期记忆

由 Store 和 Neo4j 保存：

```python
class MemoryFact(BaseModel):
    fact_id: str
    owner_id: str
    subject_person_id: str
    predicate: str
    value: Any
    source_type: SourceType
    source_ref: str | None
    evidence: str
    observed_at: datetime
    created_at: datetime
    confidence: float
    verification_status: VerificationStatus
    status: FactStatus
    extractor_version: str
```

冲突事实不直接覆盖；旧事实标记为 `SUPERSEDED`。长期记忆只能在人物绑定明确、Tool 执行成功、证据存在后写入。

## 16. 澄清和确认

### 16.1 澄清

每次只询问一个对后续计划影响最大的缺失信息：

```python
class ClarificationRequest(BaseModel):
    reason_code: str
    question: str
    target_task_ids: list[str]
    expected_answer_type: str
    candidates: list[ClarificationCandidate]
```

用户回答后，从 checkpoint 恢复原计划，补充状态并重新校验；不能丢弃原计划从头猜测。

### 16.2 确认

```python
class ConfirmationRequest(BaseModel):
    operation_id: str
    risk_level: RiskLevel
    summary: str
    affected_resources: list[ResourceRef]
    proposed_arguments: dict[str, Any]
    expires_at: datetime
```

恢复时必须验证：

- `thread_id` 与原任务一致。
- 当前登录 `owner_id` 与原任务一致。
- `operation_id` 未过期且未消费。
- 用户确认值是严格布尔值或明确枚举。

## 17. 异常处理

错误类别：

```text
INPUT_INVALID
CONTENT_BOUNDARY_UNCLEAR
MODEL_TIMEOUT
MODEL_OUTPUT_INVALID
INTENT_UNSUPPORTED
SLOT_MISSING
ENTITY_AMBIGUOUS
PLAN_INVALID
RESULT_BINDING_FAILED
TOOL_TIMEOUT
TOOL_UNAVAILABLE
BUSINESS_CONFLICT
POLICY_REJECTED
INTERNAL_ERROR
```

处理策略：

- 模型和网络瞬时错误：指数退避，最多两次。
- 结构化输出错误：修复一次，仍失败则澄清或返回可恢复错误。
- 业务冲突：不重试。
- 参数或权限错误：不重试。
- 写操作超时：先通过幂等键查询最终状态，再决定是否重试。
- 依赖步骤失败：跳过所有后继步骤并显式报告部分成功。

## 18. 可观测性

每次请求包含：

```python
class AgentTraceContext(BaseModel):
    request_id: str
    thread_id: str
    owner_id_hash: str
    model_name: str
    prompt_version: str
    intent_schema_version: str
    plan_version: str
    identified_intents: list[str]
    route_decision: str
    model_latency_ms: int
    planning_latency_ms: int
    tool_latency_ms: int
    total_latency_ms: int
    token_usage: int
    retry_count: int
    clarification_count: int
    error_codes: list[str]
```

Langfuse Trace 层级：

```text
agent.request
├── context.load
├── input.boundary
├── intent.analyze
├── entity.resolve
├── policy.evaluate
├── plan.compile
├── plan.execute
│   ├── tool.find_person
│   └── tool.get_person_detail
├── memory.write
└── response.generate
```

日志默认不保存完整聊天原文、手机号、密码、Token 和未脱敏客户资料。

## 19. 评测设计

第一版建立约 200 条 JSONL 标注样本：

| 类别 | 数量 |
|---|---:|
| 单意图 | 40 |
| 多意图 | 50 |
| 跨轮指代 | 30 |
| 聊天内容与命令混合 | 30 |
| 歧义和槽位缺失 | 20 |
| 敏感操作 | 20 |
| 越界和恶意输入 | 10 |

核心指标：

- 多意图集合准确率。
- Intent Micro/Macro F1。
- 槽位提取 F1。
- 指代消解准确率。
- 任务依赖正确率。
- 计划可执行率。
- 澄清准确率与不必要澄清率。
- 端到端任务成功率。
- 危险操作自动执行率。
- P50/P95 延迟、Token 和调用成本。

安全验收值：

```text
危险操作自动执行率 = 0
跨用户数据访问成功率 = 0
```

## 20. 测试分层

### 单元测试

- 意图注册表完整性。
- Pydantic 模型校验。
- 槽位校验。
- 实体候选评分。
- ResultBinding 白名单。
- 风险策略。
- DAG 环检测。
- Ready Step 选择。
- 重规划上限和重复签名检测。

### Tool 契约测试

- 所有 Tool 返回 ToolResult。
- 成功结果包含声明的可绑定字段。
- 写 Tool 支持幂等键。
- owner_id 只能由可信上下文注入。

### 场景测试

- 三个相互依赖的意图。
- 跨轮“他”指代。
- 同名人物多候选。
- 聊天内容中的操作词不会触发写入。
- 中途确认后恢复。
- 前一步成功、后一步失败。
- 服务重启后恢复。

### 离线评测

- 固定数据集比较 Prompt、模型和规则版本。
- 每个版本生成准确率、成功率、延迟和成本报告。
- 质量阈值未通过时禁止替换默认版本。

## 21. 安全与隐私

- `owner_id`、`self_person_id` 和 `thread_id` 来自服务端可信上下文。
- Tool Executor 覆盖模型提供的身份参数。
- ResultBinding 不能跨请求和跨 owner 读取结果。
- 确认操作绑定用户、线程、操作 ID 和过期时间。
- 长期记忆保留来源和删除能力。
- 原始客户聊天只在用户主动提交时处理。
- Trace 和日志使用脱敏标识。

## 22. 建议目录

```text
backend/
├── agent/
│   ├── state.py
│   ├── graph.py
│   ├── nodes/
│   │   ├── load_context.py
│   │   ├── classify_input_boundary.py
│   │   ├── analyze_intents.py
│   │   ├── resolve_entities.py
│   │   ├── validate_tasks.py
│   │   ├── evaluate_policy.py
│   │   ├── compile_plan.py
│   │   ├── select_ready_steps.py
│   │   ├── resolve_arguments.py
│   │   ├── execute_step.py
│   │   ├── evaluate_result.py
│   │   ├── request_clarification.py
│   │   ├── request_confirmation.py
│   │   ├── write_memory.py
│   │   └── generate_response.py
│   ├── intent/
│   │   ├── enums.py
│   │   ├── models.py
│   │   ├── registry.py
│   │   ├── analyzer.py
│   │   └── policy.py
│   ├── planning/
│   │   ├── models.py
│   │   ├── compiler.py
│   │   ├── result_resolver.py
│   │   ├── scheduler.py
│   │   └── replan_guard.py
│   ├── memory/
│   │   ├── models.py
│   │   ├── context_loader.py
│   │   ├── entity_resolver.py
│   │   └── writer.py
│   └── observability/
│       ├── trace.py
│       └── metrics.py
├── tools/
│   ├── contracts.py
│   ├── definitions.py
│   └── registry.py
└── evals/
    ├── datasets/
    ├── graders/
    └── run_eval.py

tests/
├── unit/agent/
├── contract/tools/
├── scenarios/agent/
└── evals/
```

## 23. 迁移策略

1. 先建立新模型、注册表、ToolResult 和纯函数测试，不接入现有 Graph。
2. 给旧 Tool 增加适配器，统一返回 ToolResult。
3. 实现输入边界、多意图分析、实体解析和策略引擎。
4. 实现计划编译、结果绑定和调度器。
5. 用新执行子图替换旧 `plan_tasks → execute_tools`。
6. 接入 checkpoint、Store、interrupt 和恢复 API。
7. 接入长期记忆写入。
8. 建立评测集和 Langfuse Trace。
9. 场景测试通过后删除旧单意图兼容层。

每一步都保持应用可运行，避免一次性重写整个 Agent。

## 24. 第一版验收标准

- 一句话支持至少三个相互依赖的意图。
- 后续步骤能可靠使用前一步返回的 Person/Relation ID。
- 支持跨轮“他”“那个客户”指代。
- 能区分用户命令和粘贴聊天内容。
- 多候选时不会擅自绑定。
- 删除和批量覆盖必须暂停确认。
- 中断后可以恢复待确认或未完成计划。
- 所有步骤有请求 ID、步骤 ID、计划版本和标准结果。
- 评测报告包含准确率、端到端成功率、延迟和成本。
- 危险操作自动执行率和跨用户访问成功率均为零。

## 25. 简历表述建议

> 设计并实现基于 LangGraph 的分层多意图路由与运行时任务编排框架，将自然语言请求转换为具备依赖关系、条件分支和结果绑定的任务 DAG；通过结构化输出、实体指代消解、槽位校验、风险分级、Human-in-the-loop、幂等执行及有限重规划，支持复杂关系管理与销售辅助场景，并建立离线评测和全链路可观测体系。

该表述只有在对应代码、测试、评测报告和可演示场景完成后使用。
