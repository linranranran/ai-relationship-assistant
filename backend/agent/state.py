# ============================================================
# Agent 状态定义
# ============================================================
from langgraph.graph import MessagesState
from typing import List, Optional, TypedDict
from langgraph.managed import RemainingSteps


class AgentState(TypedDict):
    # execution_version 是图状态结构版本，与 strategy_version（模式协议）不同。
    # 旧 Plan 检查点仍有单目标兼容；已移除的 Direct 版本在恢复入口拒绝执行。
    execution_version: int
    remaining_steps: RemainingSteps  # 框架管理，不能作为跨 resume 的预算账本
    goals: List[dict]
    active_goal_id: Optional[str]
    plan_revision: int              # 参数/计划变更版本，与自动重规划次数分开
    budget_snapshot: dict
    budget_reason: str
    # === 输入 ===
    user_input: str                    # 用户原始输入
    response_kind: str                 # chat / sales_preparation；期望产物，不是 goal
    user_id: str                       # 当前用户 ID
    self_person_id: str                # 当前用户私有关系图中的“本人”节点 ID
    request_id: str                    # 本次请求链路 ID
    strategy_mode: str                 # 请求固定的 plan/direct/discovery，不随配置热切换。
    strategy_version: str
    catalog_schema_hash: str
    decision_seq: int                  # 已接受动作序号；稳定 call_id 与缓存键的一部分。
    pending_decision: dict             # 决策节点持久化后，执行节点才允许产生副作用。
    strategy_messages: List[dict]      # 完整 assistant tool_calls / tool 消息组。
    goal_evaluation: dict
    progress_snapshot: dict
    no_progress_rounds: int
    finish_repair_count: int
    protocol_repair_count: int
    clarification_question: str
    stop_reason: str
    loaded_tool_schemas: List[str]
    discovery_result: Optional[dict]
    # Direct 的请求账本；没有 goal，不能借用最后一轮工具数组保存整次执行事实。
    direct_tool_calls: List[dict]
    direct_tool_results: List[dict]
    direct_clarifications: List[str]
    direct_final_message: dict         # 模型的结束草稿，不能当作业务成功证据。
    direct_rejected_mutations: List[str]  # 用户拒绝的操作内容指纹，禁止换 call_id 重提。
    completion_basis: str              # MODEL_STOP_WITH_TOOL_RECEIPTS / SYSTEM_STOP
    requirements_verified: Optional[bool]  # Direct 固定 false；Plan / Discovery 为 None。
    thread_id: str                     # LangGraph 检查点 ID，只能由服务端生成
    conversation_id: str               # 服务端校验的业务会话 ID；当前兼容值为 default
    user_name: str                     # 当前用户姓名
    history: List[dict]                # 兼容旧检查点；新代码不再把它作为上下文入口

    # === 三层记忆与上下文预算 ===
    recent_messages: List[dict]        # Checkpoint 内的紧凑短期消息窗口
    conversation_summary: dict         # 旧消息的滚动结构化摘要
    summary_cursor: Optional[str]       # 摘要已覆盖到的最后 message_id
    retrieved_memories: List[dict]     # 本轮按问题检索出的长期事实
    model_context: List[dict]           # 本轮允许发送给模型的受控上下文
    context_stats: dict                 # Token、裁剪和降级统计
    memory_write_candidates: List[dict] # 尚未完成证据/确认校验的长期事实候选
    person_focus: Optional[dict]         # 上轮唯一且已验证的人物；由 Tool 结果产生
    person_focus_candidates: List[dict]  # 上轮多个候选，用于代词追问时澄清
    person_focus_candidates_owner_id: str
    person_focus_candidates_conversation_id: str
    resolved_reference: dict             # 本轮解析出的可信人物 ID
    reference_resolution: dict            # 本轮解析方式、候选数或澄清原因
    pending_reference: Optional[dict]    # 等待用户消除代词歧义

    # === 意图分类 ===
    intent: str                        # 识别的意图：ADD_PERSON / QUERY_PERSON / ...
    intent_confidence: float           # 置信度 0.0-1.0

    # === 信息提取 ===
    extracted_info: dict               # 从用户输入中提取的结构化信息

    # === 任务规划 ===
    tool_calls: List[dict]             # 待执行的 Tool 调用序列
    # 每个元素: {"tool": "find_person", "args": {"query": "李四"}}

    # === 执行 ===
    tool_results: List[dict]           # 每个 Tool 的执行结果
    execution_errors: List[str]        # 执行中的错误
    retry_count: int                   # 重试次数
    next_tool_index: int               # 依赖执行器下一步要执行的 Tool 下标
    resume_execution: bool             # WAIT 主动结束后重试原计划，入口跳过模型规划
    completed_mutations: List[dict]    # 本请求已完成的写操作，澄清重规划也不能丢失

    # === 执行观察与重规划 ===
    execution_decision: str            # COMPLETE / REPLAN / ASK_USER / WAIT / CANCELLED
    replan_count: int                   # 自动重规划次数，必须设置上限防止死循环
    replan_feedback: dict               # 上轮失败步骤和错误码，供规划器修正计划

    # === 人机交互 ===
    # clarification：Agent 缺少业务信息；confirmation：操作已明确但需要授权。
    # 两者都可以 interrupt，但恢复数据的校验规则不能混用。
    needs_confirmation: bool           # 是否需要用户确认
    confirmation_type: str             # 例如 HIGH_RISK_TOOL
    user_confirmed: bool               # 用户是否已确认
    pending_confirmation: Optional[dict]  # 等待前端确认的结构化操作摘要

    # === 输出 ===
    response: str                      # 最终回复给用户
    sales_preparation: Optional[dict]   # 校验后的准备单；每个新请求重置，恢复原请求保留
    response_error_code: Optional[str] # 安全的业务失败代码，不暴露供应商或数据库异常
    response_retryable: Optional[bool]
