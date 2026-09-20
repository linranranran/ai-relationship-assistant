# ============================================================
# Tool 注册表 — 将 Tool 名称映射到实际函数
# 每个 Tool 函数签名：(**kwargs) -> dict
# 你需要在对应的 .py 文件中实现具体的函数体
# ============================================================

# TO-DO: 实现完每个 Tool 后，取消对应的注释，替换占位函数

from backend.tools.person import add_person, update_person, delete_person, find_person
from backend.tools.relation import add_relation, update_relation, delete_relation
from backend.tools.query import query_relation_path, get_person_detail, list_relations
from backend.tools.kinship import get_kinship_title



TOOL_REGISTRY = {
    "add_person": add_person,
    "update_person": update_person,
    "delete_person": delete_person,
    "add_relation": add_relation,
    "update_relation": update_relation,
    "delete_relation": delete_relation,
    "find_person": find_person,
    "query_relation_path": query_relation_path,
    "get_kinship_title": get_kinship_title,
    "get_person_detail": get_person_detail,
    "list_relations": list_relations,
}


def execute_tool(tool_name: str, **kwargs) -> dict:
    """
    执行一个 Tool。

    Args:
        tool_name: Tool 名称，如 "add_person"
        **kwargs: Tool 参数

    Returns:
        Tool 执行结果 dict

    Raises:
        ValueError: 未知的 Tool 名称
    """
    if tool_name not in TOOL_REGISTRY:
        return {"success":False,"message": f"未知的 Tool: {tool_name}"}

    try:
        return TOOL_REGISTRY[tool_name](**kwargs)
    except Exception as e:
        return {"success":False,"message": str(e)}