"""Agent 请求级幂等。

``request_id`` 标识一次用户请求，``call_id`` 标识该请求中的一个 Tool 步骤。
这一层负责防止前端超时重试时重新运行整张 Agent 图。
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from uuid import uuid4

from backend.db.agent_request_store import (
    finish_agent_request,
    get_agent_request,
    try_insert_agent_request,
    try_reclaim_stale_agent_request,
    try_restart_failed_agent_request,
    try_resume_agent_request,
)


class AgentRequestStatus(StrEnum):
    """Agent 请求在 MySQL 中的生命周期状态。"""

    RUNNING = "running"  # 当前有工作进程执行这次请求。
    COMPLETED = "completed"  # 请求已完成，可以直接复用 response。
    # 数据库沿用原字段值；它现在表示 LangGraph 正在等待任意人工交互，具体类型
    # 由已保存 ChatResponse 的 status 区分。
    CONFIRMATION_REQUIRED = "confirmation_required"
    FAILED = "failed"  # 请求发生系统错误，可以使用同一个 request_id 重试。


class AgentRequestAction(StrEnum):
    """请求抢占后，HTTP 层应该采取的动作。"""

    EXECUTE = "EXECUTE"  # 当前进程获得执行权，可以运行或恢复 Agent 图。
    RETURN_RESPONSE = "RETURN_RESPONSE"  # 请求已有结果，直接返回保存的响应。
    IN_PROGRESS = "IN_PROGRESS"  # 其他进程正在执行，立即返回处理中。


@dataclass(frozen=True, slots=True)
class AgentRequestClaim:
    action: AgentRequestAction
    execution_token: str | None = None
    response: dict | None = None


def load_agent_request(owner_id: str, request_id: str) -> dict | None:
    """按登录用户读取请求状态；调用方不能跨 owner 查询。"""
    return get_agent_request(owner_id, request_id)


def hash_request_message(message: str) -> str:
    """生成消息摘要，阻止同一个 request_id 被换成另一条消息复用。"""
    return sha256(message.encode("utf-8")).hexdigest()


def claim_agent_request(
    *,
    owner_id: str,
    request_id: str,
    thread_id: str,
    message: str,
    stale_after_seconds: int = 300,
) -> AgentRequestClaim:
    """原子地决定当前进程是否可以执行这次 Agent 请求。"""
    message_hash = hash_request_message(message)
    execution_token = str(uuid4())

    if try_insert_agent_request(
        owner_id=owner_id,
        request_id=request_id,
        thread_id=thread_id,
        message_hash=message_hash,
        execution_token=execution_token,
    ):
        return AgentRequestClaim(
            AgentRequestAction.EXECUTE,
            execution_token=execution_token,
        )

    record = get_agent_request(owner_id, request_id)
    if record is None:
        raise RuntimeError("请求幂等记录发生并发异常：唯一键冲突后未查询到记录")
    if record.get("thread_id") != thread_id:
        raise RuntimeError("request_id 已被其他 thread 使用")
    if record.get("message_hash") != message_hash:
        raise ValueError("request_id 已用于另一条消息，请为新消息生成新的 request_id")

    status = record.get("status")
    if status in {
        AgentRequestStatus.COMPLETED,
        AgentRequestStatus.CONFIRMATION_REQUIRED,
    }:
        response = record.get("response")
        if not isinstance(response, dict):
            raise RuntimeError("已完成的请求缺少可复用 response")
        return AgentRequestClaim(
            AgentRequestAction.RETURN_RESPONSE,
            response=response,
        )

    if status == AgentRequestStatus.FAILED:
        if try_restart_failed_agent_request(
            owner_id,
            request_id,
            execution_token,
        ):
            return AgentRequestClaim(
                AgentRequestAction.EXECUTE,
                execution_token=execution_token,
            )
        return AgentRequestClaim(AgentRequestAction.IN_PROGRESS)

    if status == AgentRequestStatus.RUNNING:
        if try_reclaim_stale_agent_request(
            owner_id,
            request_id,
            stale_after_seconds,
            execution_token,
        ):
            return AgentRequestClaim(
                AgentRequestAction.EXECUTE,
                execution_token=execution_token,
            )
        return AgentRequestClaim(AgentRequestAction.IN_PROGRESS)

    raise RuntimeError(f"未知 Agent 请求状态：{status}")


def claim_agent_resume(*, owner_id: str, request_id: str) -> AgentRequestClaim:
    """把等待确认的请求原子地恢复为 running。"""
    execution_token = str(uuid4())
    if try_resume_agent_request(owner_id, request_id, execution_token):
        return AgentRequestClaim(
            AgentRequestAction.EXECUTE,
            execution_token=execution_token,
        )

    record = get_agent_request(owner_id, request_id)
    if record and record.get("status") == AgentRequestStatus.COMPLETED:
        response = record.get("response")
        if isinstance(response, dict):
            return AgentRequestClaim(
                AgentRequestAction.RETURN_RESPONSE,
                response=response,
            )
    return AgentRequestClaim(AgentRequestAction.IN_PROGRESS)


def save_agent_response(
    *,
    owner_id: str,
    request_id: str,
    response: dict,
    execution_token: str,
) -> None:
    """保存可供重复请求直接复用的最终响应或确认响应。"""
    response_status = response.get("status")
    status = (
        AgentRequestStatus.CONFIRMATION_REQUIRED
        if response_status in {"CONFIRMATION_REQUIRED", "CLARIFICATION_REQUIRED"}
        else AgentRequestStatus.COMPLETED
    )
    if not finish_agent_request(
        owner_id=owner_id,
        request_id=request_id,
        status=status.value,
        response=response,
        error_message=None,
        execution_token=execution_token,
    ):
        raise RuntimeError("Agent 已返回结果，但请求幂等记录未能保存")


def mark_agent_request_failed(
    *,
    owner_id: str,
    request_id: str,
    error_message: str,
    execution_token: str,
) -> None:
    """记录系统级失败，使同一个 request_id 可以安全重试。"""
    if not finish_agent_request(
        owner_id=owner_id,
        request_id=request_id,
        status=AgentRequestStatus.FAILED.value,
        response=None,
        error_message=error_message,
        execution_token=execution_token,
    ):
        raise RuntimeError("Agent 请求失败状态未能保存")
