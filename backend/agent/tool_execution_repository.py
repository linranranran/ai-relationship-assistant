"""Application-level idempotency repository for Tool execution.

The executor depends on this small API instead of MySQL details. A PostgreSQL
adapter can later implement the same contract without changing the Agent graph.
"""

from dataclasses import dataclass
from uuid import uuid4

from backend.agent.execution_enums import ExecutionClaimAction, ToolExecutionStatus
from backend.db.mysql_client import (
    finish_tool_execution,
    get_tool_execution,
    try_insert_tool_execution,
    try_reclaim_stale_execution,
    try_restart_failed_execution,
)


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    action: ExecutionClaimAction
    result: dict | None = None
    execution_token: str | None = None


def claim_execution(
    *,
    call_id: str,
    owner_id: str,
    request_id: str,
    thread_id: str,
    tool_name: str,
    arguments: dict,
    stale_after_seconds: int = 300,
) -> ExecutionClaim:
    """
        原子地决定该工人是否可以执行该工具。
        -EXECUTE：此请求拥有执行租约。
        -WAIT：较早的尝试成功；重用其存储的结果。
        -IN_PROGRESS：另一个非过时的工作程序当前拥有该调用。
    """
    if not all((call_id, owner_id, request_id, thread_id, tool_name)):
        raise ValueError(
            "call_id、owner_id、request_id、thread_id、tool_name 不能为空"
        )

    execution_token = str(uuid4())
    if try_insert_tool_execution(
        call_id=call_id,
        owner_id=owner_id,
        request_id=request_id,
        thread_id=thread_id,
        tool_name=tool_name,
        arguments=arguments,
        execution_token=execution_token,
    ):
        # 状态1.0：原表中没有数据，并且insert数据成功，返回EXECUTE，告诉执行工具节点这一次可以执行工具
        return ExecutionClaim(ExecutionClaimAction.EXECUTE, execution_token=execution_token)

    record = get_tool_execution(call_id, owner_id)
    if record is None:
        raise RuntimeError("幂等记录发生并发异常：唯一键冲突后未查询到记录")

    # 相同 call_id 不能被换成另一个 Tool 使用。
    if (
        record.get("tool_name") != tool_name
        or record.get("thread_id") != thread_id
        or record.get("request_id") != request_id
    ):
        raise RuntimeError("call_id 已被其他 request、Tool 或 thread 使用")

    status = record.get("status")
    if status == ToolExecutionStatus.SUCCESS:
        result = record.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("成功的幂等记录缺少可复用 result")
        # 状态2.0：返回WAIT，告诉执行工具节点不需要执行，只需要沿用上一次的数据
        return ExecutionClaim(ExecutionClaimAction.WAIT, result=result)

    if status == ToolExecutionStatus.FAILED:
        if try_restart_failed_execution(call_id, owner_id, execution_token):
            # 状态1.1：原表中有数据，但是上一次执行失败了，这一次成功update状态，返回EXECUTE，告诉执行工具节点这一次可以执行工具
            return ExecutionClaim(ExecutionClaimAction.EXECUTE, execution_token=execution_token)
        # 状态3.0：返回IN_PROGRESS，告诉执行工具节点其他进程正在执行，本进程不能重复调用 Tool
        return ExecutionClaim(ExecutionClaimAction.IN_PROGRESS)

    if status == ToolExecutionStatus.RUNNING:
        if try_reclaim_stale_execution(
            call_id, owner_id, stale_after_seconds, execution_token
        ):
            # 状态1.2：原表中有数据，但是上一次执行超时（或者线程崩溃，执行完毕未修改状态），返回EXECUTE，告诉执行工具节点这一次可以执行工具
            return ExecutionClaim(ExecutionClaimAction.EXECUTE, execution_token=execution_token)
        # 状态3.1：竞争mysql锁失败，说明其它线程正在执行过程中，返回IN_PROGRESS，本进程不能重复调用 Tool
        return ExecutionClaim(ExecutionClaimAction.IN_PROGRESS)

    raise RuntimeError(f"未知 Tool 执行状态：{status}")


def mark_success(
    *,
    call_id: str,
    owner_id: str,
    result: dict,
    latency_ms: int,
    execution_token: str,
) -> None:
    if not finish_tool_execution(
        call_id=call_id,
        owner_id=owner_id,
        status=ToolExecutionStatus.SUCCESS,
        result=result,
        error_message=None,
        latency_ms=latency_ms,
        execution_token=execution_token,
    ):
        raise RuntimeError("Tool 成功，但幂等记录未能更新为 success")


def mark_failed(
    *,
    call_id: str,
    owner_id: str,
    error_message: str,
    latency_ms: int,
    result: dict | None = None,
    execution_token: str,
) -> None:
    if not finish_tool_execution(
        call_id=call_id,
        owner_id=owner_id,
        status=ToolExecutionStatus.FAILED,
        result=result,
        error_message=error_message,
        latency_ms=latency_ms,
        execution_token=execution_token,
    ):
        raise RuntimeError("Tool 失败，但幂等记录未能更新为 failed")
