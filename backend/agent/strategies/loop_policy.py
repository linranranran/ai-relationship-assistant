"""确定性循环保护：正常下一步不是重规划，工具成功不是目标成功。"""
from dataclasses import dataclass
from enum import StrEnum
from backend.config import AGENT_MAX_NO_PROGRESS_ROUNDS


class LoopAction(StrEnum):
    CONTINUE = "CONTINUE"
    SUCCESS = "SUCCESS"
    ASK = "ASK"
    WAIT = "WAIT"
    FAIL = "FAIL"


@dataclass(frozen=True)
class LoopDecision:
    action: LoopAction
    reason: str


def compute_progress(previous: dict, current: dict) -> dict:
    """承认新绑定、实际实体变化、首次 Schema 和目标覆盖；重复缓存不算进展。"""
    progressed = bool(set(current.get("bindings", [])) - set(previous.get("bindings", []))
                      or set(current.get("schemas", [])) - set(previous.get("schemas", []))
                      or set(current.get("entity_versions", [])) - set(previous.get("entity_versions", []))
                      or set(current.get("coverage", [])) - set(previous.get("coverage", []))
                      or set(current.get("evidence", [])) - set(previous.get("evidence", [])))
    return {"progressed": progressed, "snapshot": current}


def evaluate_loop(state, goal, observation=None) -> LoopDecision:
    codes = {r.get("error_code") for r in state.get("tool_results", []) if r.get("status") in {"FAILED", "SKIPPED"}}
    if codes & {"PERMISSION_DENIED", "INTERNAL", "UNKNOWN"}:
        return LoopDecision(LoopAction.FAIL, "TOOL_UNRECOVERABLE")
    if codes & {"IN_PROGRESS", "TRANSIENT"}:
        return LoopDecision(LoopAction.WAIT, "TOOL_PENDING")
    if codes & {"AMBIGUOUS", "CONFLICT"} or goal.reason_code == "AMBIGUOUS":
        return LoopDecision(LoopAction.ASK, "AMBIGUOUS")
    if goal.status == "SATISFIED":
        return LoopDecision(LoopAction.SUCCESS, "GOAL_SATISFIED")
    if goal.status == "BLOCKED":
        return LoopDecision(LoopAction.FAIL, goal.reason_code or "GOAL_BLOCKED")
    if state.get("no_progress_rounds", 0) >= AGENT_MAX_NO_PROGRESS_ROUNDS:
        return LoopDecision(LoopAction.FAIL, "LOOP_NO_PROGRESS")
    return LoopDecision(LoopAction.CONTINUE, "GOAL_PENDING")
