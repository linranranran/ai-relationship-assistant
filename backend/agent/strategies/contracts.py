"""模型建议与系统路由分离，避免三个策略维护三套数据库执行逻辑。"""
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class DecisionKind(StrEnum):
    EXECUTE_BATCH = "EXECUTE_BATCH"
    SEARCH = "SEARCH"
    DESCRIBE = "DESCRIBE"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    FINISH_CANDIDATE = "FINISH_CANDIDATE"


@dataclass(frozen=True)
class StrategyDecision:
    kind: DecisionKind
    calls: list[dict] = field(default_factory=list)
    arguments: dict = field(default_factory=dict)
    message: dict = field(default_factory=dict)

    def as_dict(self):
        return {"kind": self.kind.value, "calls": self.calls, "arguments": self.arguments, "message": self.message}


class DecisionStrategy(Protocol):
    def propose(self, state: dict, catalog) -> StrategyDecision: ...


STRATEGY_VERSION = "1"
# Direct 只保留完整请求循环。仍保存版本号以识别不兼容的历史检查点，
# 不能把旧 goal 状态当作新请求状态继续执行；这不是保留另一套 Direct 实现。
DIRECT_REQUEST_VERSION = "2"


def current_strategy_version(mode: str) -> str:
    return DIRECT_REQUEST_VERSION if mode == "direct" else STRATEGY_VERSION


def is_request_direct(state: dict) -> bool:
    """必须同时匹配模式和版本，不能让旧检查点因配置变化进入新流程。"""
    return state.get("strategy_mode") == "direct" and state.get("strategy_version") == DIRECT_REQUEST_VERSION


def require_supported_strategy(state: dict) -> None:
    from backend.agent.contracts import ModelBusinessValidationError
    mode, version = state.get("strategy_mode", "plan"), state.get("strategy_version", STRATEGY_VERSION)
    if mode == "direct" and version != DIRECT_REQUEST_VERSION:
        raise ModelBusinessValidationError(
            "STRATEGY_CHANGED",
            "旧版 Direct 已移除，不能继续原任务。请先停止旧任务，再重新提问；已完成的操作仍然保留。",
        )
    if mode not in {"plan", "direct", "discovery"} or version != current_strategy_version(mode):
        raise ModelBusinessValidationError("STRATEGY_CHANGED", "任务策略版本不兼容，禁止静默迁移。")


CLARIFICATION_TOOL = {"type": "function", "function": {
    "name": "request_clarification", "description": "缺少用户才能提供的业务信息时提出问题。不能用此功能修复内部模型/工具错误。",
    "parameters": {"type": "object", "properties": {"question": {"type": "string", "maxLength": 1000}},
                   "required": ["question"], "additionalProperties": False}}}
