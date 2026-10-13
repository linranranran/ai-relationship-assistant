"""保留已有规划 Prompt、结构化校验和依赖机制，只抽出策略入口。"""
from backend.agent.strategies.contracts import DecisionKind, StrategyDecision


class ToolPlanStrategy:
    def propose(self, state, catalog):
        # 局部导入避免 plan_tasks 与策略注册工厂形成模块初始化循环。
        from backend.agent.nodes.plan_tasks import _build_planning_prompt, _request_model_plan
        value = _request_model_plan(state, _build_planning_prompt(state))
        return StrategyDecision(DecisionKind.EXECUTE_BATCH, calls=value.get("tool_calls", []))
