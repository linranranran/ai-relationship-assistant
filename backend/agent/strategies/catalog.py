"""权威工具目录：Schema 来自 Pydantic；按请求或当前目标限定可见范围。"""
from dataclasses import dataclass
from hashlib import sha256
import json

from backend.agent.confirmation import HIGH_RISK_TOOLS
from backend.agent.goal_policy import ALLOWED_TOOLS, active_goal
from backend.tools.registry import TOOL_ARGUMENT_MODELS
from backend.tools.definitions import ALL_TOOL_DEFINITIONS

# 输入 Schema 说明“怎么调用”，返回契约说明“下一步怎么引用”。不说明后者，
# 模型容易把唯一人物误当候选数组，生成 data.0.id 而浪费一次查库/重规划。
OUTPUT_DESCRIPTIONS = {
    "find_person": " 唯一命中：success=true,data={id,name,...}，ID 引用为 data.id，绝不是 data.0.id；多候选为 AMBIGUOUS，需用户选择。",
    "get_person_detail": " 成功：data={person:{id,name,...},relations:[...]}，人物字段位于 data.person。",
    "query_relation_path": " 成功：data 为路径步骤列表，可整体传给 get_kinship_title 的 relation_path。",
}


@dataclass(frozen=True)
class CatalogSnapshot:
    tools: dict[str, dict]
    schema_hash: str

    def native_tools(self):
        # risk 是应用目录元数据，不能塞进供应商 Function 定义的顶层字段。
        return [{"type": "function", "function": {k: item[k] for k in ("name", "description", "parameters")}}
                for item in self.tools.values()]


class ToolCatalog:
    @staticmethod
    def for_request(state: dict) -> CatalogSnapshot:
        """当前产品的登录用户可使用这些注册工具，数据权限仍由 owner 检查落实。

        这里不能再使用 intent 白名单，也不从模型参数接受工具权限。未来接入角色
        权限时在此交集过滤能力；当前并没有实现 RBAC，不宣称角色级授权。
        """
        return ToolCatalog.snapshot()

    @staticmethod
    def for_goal(state: dict) -> CatalogSnapshot:
        goal = active_goal(state)
        allowed = ALLOWED_TOOLS.get((goal or {}).get("intent", state.get("intent")), set())
        return ToolCatalog.snapshot(allowed)

    @staticmethod
    def snapshot(allowed=None) -> CatalogSnapshot:
        descriptions = {d["function"]["name"]: d["function"].get("description", "") for d in ALL_TOOL_DEFINITIONS}
        names = set(TOOL_ARGUMENT_MODELS) if allowed is None else set(allowed)
        tools = {name: {"name": name, "description": descriptions.get(name, name) + OUTPUT_DESCRIPTIONS.get(name, ""),
                        "risk": "confirmation_required" if name in HIGH_RISK_TOOLS else "write" if name.startswith(("add_", "update_")) else "read",
                        "parameters": TOOL_ARGUMENT_MODELS[name].model_json_schema()}
                 for name in sorted(names) if name in TOOL_ARGUMENT_MODELS}
        digest = sha256(json.dumps(tools, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return CatalogSnapshot(tools, digest)


def require_current_catalog(state: dict) -> CatalogSnapshot:
    """核对版本/Schema；Direct 按请求选工具，其余模式按当前目标选工具。

    目录兼容不能代替数据归属检查或写入确认；旧哈希也不是永久通行证。
    """
    from backend.agent.contracts import ModelBusinessValidationError
    from backend.agent.strategies.contracts import is_request_direct, require_supported_strategy
    require_supported_strategy(state)
    expected = state.get("catalog_schema_hash")
    if expected and expected != ToolCatalog.snapshot().schema_hash:
        raise ModelBusinessValidationError("CATALOG_CHANGED", "工具定义已变化，本任务不能自动迁移参数协议。")
    return ToolCatalog.for_request(state) if is_request_direct(state) else ToolCatalog.for_goal(state)
