"""策略配置只决定新请求；已有请求使用其持久化模式。"""
from backend.agent.strategies.contracts import current_strategy_version, require_supported_strategy


def validate_strategy(mode: str) -> str:
    if mode not in {"plan", "direct", "discovery"}:
        raise ValueError("AGENT_TOOL_STRATEGY 必须为 plan/direct/discovery")
    return mode


def get_strategy(mode: str, version: str | None = None):
    """每个模式只有一个实现；显式传入历史版本时只做兼容性拒绝。"""
    validate_strategy(mode)
    require_supported_strategy({
        "strategy_mode": mode,
        "strategy_version": version if version is not None else current_strategy_version(mode),
    })
    if mode == "plan":
        from backend.agent.strategies.tool_plan import ToolPlanStrategy
        return ToolPlanStrategy()
    if mode == "direct":
        from backend.agent.strategies.direct import DirectStrategy
        return DirectStrategy()
    from backend.agent.strategies.discovery import DiscoveryStrategy
    return DiscoveryStrategy()
