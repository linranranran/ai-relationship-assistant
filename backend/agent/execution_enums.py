"""Agent Tool 执行链路使用的状态枚举。

三个枚举分别描述数据库事实、执行权判断结果和 LangGraph 步骤结果，
避免把不同层次的状态混为一谈。
"""

from enum import StrEnum


class ToolExecutionStatus(StrEnum):
    """Tool 执行记录在数据库中的生命周期状态。"""

    RUNNING = "running"  # 已获得执行权，Tool 正在执行或等待超时回收。
    SUCCESS = "success"  # Tool 已执行成功，result 可以被后续请求复用。
    FAILED = "failed"  # Tool 执行失败，允许后续请求重新抢占执行权。


class ExecutionClaimAction(StrEnum):
    """claim_execution 返回给当前执行器的下一步动作。"""

    EXECUTE = "EXECUTE"  # 当前请求获得执行权，可以调用真实 Tool。
    WAIT = "WAIT"  # 已存在成功记录，不再执行 Tool，直接复用历史 result。
    IN_PROGRESS = "IN_PROGRESS"  # 其他请求持有执行权，当前请求暂不执行。


class ToolStepStatus(StrEnum):
    """LangGraph state 中单个 Tool 步骤的本轮执行结果。"""

    SUCCESS = "SUCCESS"  # 本轮执行成功，或成功复用了历史结果。
    FAILED = "FAILED"  # 本轮执行、参数解析或执行权获取失败。
    SKIPPED = "SKIPPED"  # 因依赖失败或其他请求正在执行而跳过本步骤。
