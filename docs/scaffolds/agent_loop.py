"""单 Agent 有界工具反馈循环教学骨架。

建议落地：
- backend/agent/contracts.py：NextDecision、Observation、PendingAction。
- backend/agent/nodes/decide_next.py：依据消息与观察决定下一步。
- backend/agent/nodes/execute_one.py：每轮只执行一个经过校验的工具。
- backend/services/confirmation.py：服务端待确认动作及一次性消费。
- backend/agent/graph.py：decide -> execute -> decide / clarify / final。

本文件不依赖 LangGraph，先把状态机语义弄清楚。实际接入时再适配当前
langgraph==0.2.61，不能假设最新版文档的 API 与锁定版本完全相同。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping, Protocol

from identity_access import RequestContext
from tool_contracts import ToolCall, ToolResult


@dataclass(frozen=True)
class Observation:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class FinalAnswer:
    text: str
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Clarification:
    question: str
    choices: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class NextDecision:
    kind: Literal["tool", "clarify", "final"]
    tool_call: ToolCall | None = None
    clarification: Clarification | None = None
    final: FinalAnswer | None = None


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    owner_id: str
    thread_id: str
    tool: str
    normalized_args_hash: str
    operation_id: str
    expires_at: datetime
    status: Literal["pending", "approved", "cancelled", "expired", "consumed"]


@dataclass(frozen=True)
class AgentTurnState:
    ctx: RequestContext
    thread_id: str
    user_message: str
    observations: tuple[Observation, ...] = ()
    step_count: int = 0
    started_at: datetime | None = None
    resolved_entities: Mapping[str, str] = field(default_factory=dict)


class Planner(Protocol):
    def decide(self, state: AgentTurnState) -> NextDecision: ...


class ToolExecutor(Protocol):
    def execute(self, *, ctx: RequestContext, call: ToolCall) -> ToolResult: ...


class ConversationRepository(Protocol):
    def assert_owned_thread(self, *, owner_id: str, thread_id: str) -> None: ...
    def append_observation(self, *, state: AgentTurnState, observation: Observation) -> AgentTurnState: ...


class ConfirmationRepository(Protocol):
    def create_pending(self, *, action: PendingAction) -> PendingAction: ...
    def consume_approved(self, *, owner_id: str, thread_id: str, action_id: str) -> PendingAction: ...


def validate_decision(decision: NextDecision) -> NextDecision:
    # 1. kind=tool/clarify/final 必须且只能填对应字段。
    # 2. tool call_id、名称和参数不可为空；模型不能提供 ctx 或授权字段。
    # 3. clarify 问题不能为空；choices 使用稳定 ID，显示文案不能充当人物 ID。
    # 4. final 不能声称未执行的写入成功；引用必须来自已验证观察。
    # 5. 结构错误由调用节点进行一次有限修复或转为可解释错误。
    raise NotImplementedError("请实现：验证模型的下一步决定")


def run_agent_turn(
    *,
    initial: AgentTurnState,
    planner: Planner,
    executor: ToolExecutor,
    conversations: ConversationRepository,
    max_steps: int = 6,
    deadline_seconds: float = 20.0,
) -> FinalAnswer | Clarification | PendingAction:
    # 1. 校验 thread 属于 ctx.user_id；started_at 由服务端设置，不信任客户端。
    # 2. 循环前限制 max_steps 与 deadline；每轮增加 step_count。
    # 3. planner 每次看到用户消息、已解析实体和全部结构化 observations。
    # 4. validate_decision 后处理：final 直接返回；clarify 保存状态并返回。
    # 5. tool 决定每轮只执行一个工具。需要确认时 executor 返回稳定错误码，
    #    调用确认服务生成 PendingAction 后返回，不能继续执行写入。
    # 6. 其他工具结果形成 Observation，持久化后把新 state 送回 planner。
    # 7. 同名候选是 AMBIGUOUS，下一步应澄清；没有选择前不能执行修改。
    # 8. 失败只有 retryable=True 且工具本身安全/幂等时才允许重新决定重试。
    # 9. 达到轮数或截止时间时返回部分结果、错误与下一步，而不是死循环。
    # 10. 持久化失败不能假装观察已经保存；恢复时靠 operation_id 防止重复写。
    raise NotImplementedError("请实现：一次一个动作的有界工具反馈循环")


def confirm_pending_action(
    *,
    ctx: RequestContext,
    thread_id: str,
    action_id: str,
    approved: bool,
    confirmations: ConfirmationRepository,
    executor: ToolExecutor,
) -> ToolResult:
    # 1. 按 owner+thread+action 原子加载；不存在与异主对外使用同一结果。
    # 2. 拒绝过期、取消或已消费动作；approved=False 原子标记 cancelled。
    # 3. approved=True 时原子取得执行资格，核对保存的参数摘要与操作 ID。
    # 4. 使用服务器保存的工具和参数执行，绝不采用本次请求重新提交的参数。
    # 5. 通过稳定 operation_id 实现幂等；响应丢失后重复确认不产生第二次效果。
    # 6. 保存执行结果和 consumed 状态；说明单库事务边界与故障恢复方式。
    raise NotImplementedError("请实现：一次性消费服务端待确认动作")


# 关键验收：
# - find_person 返回随机 ID，下一轮工具参数必须使用该 ID。
# - 两个同名候选先澄清、零写入；选择使用稳定 candidate ID。
# - 模型自行传 confirmed=true 无效；异主、过期、重复确认均失败。
# - 工具第二步失败时回答准确描述部分完成；不可重试写不自动重复。
# - 第六步仍未结束时受控终止；线程刷新/重启后状态可恢复且仍受 owner 限制。
