"""Tool 执行结果的观察与路由策略。

这一层只做确定性的控制决策，不让大模型直接决定是否重试写操作。
你接下来主要实现各个 Tool 的结构化 ``error_code``，本策略就能逐步生效。
"""

from dataclasses import dataclass, field
from enum import StrEnum

from backend.agent.execution_enums import ToolStepStatus


MAX_AUTO_REPLANS = 1

# 已经成功执行过这些 Tool 时，禁止自动重新规划。否则模型可能生成新的
# call_id，再次执行具有副作用的操作。第一版宁可询问用户，也不要冒险重复写。
MUTATING_TOOLS = frozenset(
    {
        "add_person",
        "update_person",
        "delete_person",
        "add_relation",
        "update_relation",
        "delete_relation",
    }
)


class ToolErrorCode(StrEnum):
    """Tool失败的机器可读原因；不要依赖中文错误文案做流程判断。"""

    NOT_FOUND = "NOT_FOUND"  # 查询目标不存在，可能需要换方案或补建前置数据。
    AMBIGUOUS = "AMBIGUOUS"  # 返回多个候选，需要用户选择。
    INVALID_ARGUMENT = "INVALID_ARGUMENT"  # 规划参数错误，可以安全重规划一次。
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 越权或禁止操作，不能自动重试。
    CONFLICT = "CONFLICT"  # 数据冲突，需要用户决定如何处理。
    TRANSIENT = "TRANSIENT"  # 网络、限流等临时故障，应稍后重试原请求。
    IN_PROGRESS = "IN_PROGRESS"  # 同一个调用正由其他工作进程执行。
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"  # 前置步骤失败，当前步骤没有执行。
    INTERNAL = "INTERNAL"  # Agent自身异常，需要记录和人工排查。
    UNKNOWN = "UNKNOWN"  # 旧Tool尚未接入结构化错误协议。


class ExecutionDecision(StrEnum):
    """观察节点对下一步图路由作出的决定。"""

    COMPLETE = "COMPLETE"  # 执行完成，可以生成最终回复。
    REPLAN = "REPLAN"  # 没有产生写副作用，可以携带失败上下文重新规划一次。
    ASK_USER = "ASK_USER"  # 信息不足、存在歧义或风险，需要用户参与。
    WAIT = "WAIT"  # 其他进程执行中或临时故障，结束本轮并提示稍后重试。


@dataclass(frozen=True, slots=True)
class ExecutionObservation:
    """观察节点的结构化输出，便于保存进LangGraph state和审计。"""

    decision: ExecutionDecision
    reason: str
    failed_steps: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)

    def as_feedback(self) -> dict:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "failed_steps": self.failed_steps,
            "error_codes": self.error_codes,
        }


def parse_tool_error_code(result: dict | None) -> ToolErrorCode:
    """读取Tool返回的结构化错误码，无法识别时安全降级为UNKNOWN。

    TODO(你来实现 Tool 时遵守以下失败协议)：
    ``{"success": False, "error_code": "NOT_FOUND", "retryable": False,
       "message": "未找到张三", "data": {}}``
    """
    if not isinstance(result, dict):
        return ToolErrorCode.UNKNOWN
    raw_code = result.get("error_code")
    try:
        return ToolErrorCode(raw_code)
    except (ValueError, TypeError):
        return ToolErrorCode.UNKNOWN


def observe_tool_results(
    *,
    records: list[dict],
    execution_errors: list[str],
    replan_count: int,
) -> ExecutionObservation:
    """根据结构化结果决定下一步；不调用LLM，因此结果稳定、可审计。"""
    failed_records = [
        record
        for record in records
        if record.get("status") in {ToolStepStatus.FAILED, ToolStepStatus.SKIPPED}
    ]
    if not failed_records and not execution_errors:
        return ExecutionObservation(
            decision=ExecutionDecision.COMPLETE,
            reason="全部Tool步骤执行成功",
        )

    failed_steps = [
        str(record.get("step_id"))
        for record in failed_records
        if record.get("step_id")
    ]
    codes = {
        _record_error_code(record)
        for record in failed_records
    }

    if ToolErrorCode.IN_PROGRESS in codes or ToolErrorCode.TRANSIENT in codes:
        return ExecutionObservation(
            decision=ExecutionDecision.WAIT,
            reason="调用仍在执行或遇到临时故障，应稍后重试原request_id",
            failed_steps=failed_steps,
            error_codes=sorted(code.value for code in codes),
        )

    if codes & {
        ToolErrorCode.AMBIGUOUS,
        ToolErrorCode.CONFLICT,
        ToolErrorCode.PERMISSION_DENIED,
    }:
        return ExecutionObservation(
            decision=ExecutionDecision.ASK_USER,
            reason="执行结果需要用户选择或授权",
            failed_steps=failed_steps,
            error_codes=sorted(code.value for code in codes),
        )

    successful_mutations = [
        record
        for record in records
        if record.get("status") == ToolStepStatus.SUCCESS
        and record.get("tool") in MUTATING_TOOLS
    ]
    replanable = bool(
        codes & {ToolErrorCode.NOT_FOUND, ToolErrorCode.INVALID_ARGUMENT}
    )
    if (
        replanable
        and replan_count < MAX_AUTO_REPLANS
        and not successful_mutations
    ):
        return ExecutionObservation(
            decision=ExecutionDecision.REPLAN,
            reason="查询或规划失败，且尚未产生写副作用，允许自动重规划一次",
            failed_steps=failed_steps,
            error_codes=sorted(code.value for code in codes),
        )

    # UNKNOWN、INTERNAL、依赖失败以及超过重规划上限都采用安全降级。
    # TODO(你来实现)：当所有Tool都接入error_code后，UNKNOWN应进入监控告警。
    return ExecutionObservation(
        decision=ExecutionDecision.ASK_USER,
        reason="当前结果无法安全自动恢复，需要补充信息或人工处理",
        failed_steps=failed_steps,
        error_codes=sorted(code.value for code in codes),
    )


def _record_error_code(record: dict) -> ToolErrorCode:
    raw_code = record.get("error_code")
    try:
        return ToolErrorCode(raw_code)
    except (ValueError, TypeError):
        return parse_tool_error_code(record.get("result"))
