# ============================================================
# Agent 状态定义
# ============================================================

from typing import List, Optional, TypedDict


class AgentState(TypedDict):
    # === 输入 ===
    user_input: str                    # 用户原始输入
    user_id: str                       # 当前用户 ID
    self_person_id: str                # 当前用户私有关系图中的“本人”节点 ID
    request_id: str                    # 本次请求链路 ID
    thread_id: str                     # LangGraph 检查点 ID，只能由服务端生成
    user_name: str                     # 当前用户姓名
    history: List[dict]                # 最近对话历史

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

    # === 执行观察与重规划 ===
    execution_decision: str            # COMPLETE / REPLAN / ASK_USER / WAIT
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
