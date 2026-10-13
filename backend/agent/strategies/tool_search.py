"""有限目录的词法发现。中英文别名显式维护，不宣称是语义检索。"""
import json
import re
from backend.agent.contracts import ModelBusinessValidationError

ALIASES = {
    "find_person": "查找 搜索 人物 客户 同名 姓名 查询 search find person customer",
    "get_person_detail": "资料 详情 爱好 职业 出生 生日 年龄 性格 hobbies profile detail",
    "add_person": "新增 创建 添加 人物 客户 create add person",
    "update_person": "更新 修改 补充 人物 资料 update person",
    "delete_person": "删除 移除 人物 delete person",
    "add_relation": "建立 新增 添加 关系 朋友 客户 同事 create relationship",
    "update_relation": "修改 更新 关系 update relationship",
    "delete_relation": "删除 关系 delete relationship",
    "list_relations": "列出 列表 朋友 同事 客户 关系 list relations",
    "query_relation_path": "关系 路径 链路 查询 relation path",
    "get_kinship_title": "亲属 称谓 堂哥 堂弟 叔叔 称呼 kinship title",
}


def search_tools(snapshot, query: str, limit: int = 5) -> dict:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ModelBusinessValidationError("SEARCH_ARGUMENT_INVALID", "搜索需提供 1-500 字查询，limit 为 1-5。")
    # 英文单词和中文二字片段，对小目录可解释且无需额外服务。
    terms = set(re.findall(r"[a-z_]+|[\u4e00-\u9fff]{2}", query.lower()))
    chinese = re.findall(r"[\u4e00-\u9fff]+", query)
    terms.update(word[i:i+2] for word in chinese for i in range(len(word)-1))
    hits = []
    for name, tool in snapshot.tools.items():
        text = f"{name} {tool['description']} {ALIASES.get(name, '')}".lower()
        score = sum(term in text for term in terms) + (100 if query.strip() == name else 0)
        if score:
            hits.append((score, name, tool))
    hits.sort(key=lambda hit: (-hit[0], hit[1]))
    candidates, truncated = [], False
    for score, name, tool in hits[:limit]:
        row = {"id": name, "description": tool["description"][:240], "risk": tool.get("risk", "read"),
               "input": {"required": tool["parameters"].get("required", []),
                         "fields": list(tool["parameters"].get("properties", {}))},
               "schema_hash": snapshot.schema_hash}
        if len(json.dumps([*candidates, row], ensure_ascii=False)) > 3800:
            truncated = True
            break
        candidates.append(row)
    return {"candidates": candidates, "truncated": truncated, "algorithm": "bilingual_keywords_v1"}


def describe_tool(snapshot, tool_id: str) -> dict:
    if tool_id not in snapshot.tools:
        raise ModelBusinessValidationError("TOOL_UNAVAILABLE", "工具不在当前允许目录中。")
    tool = snapshot.tools[tool_id]
    if len(json.dumps(tool, ensure_ascii=False)) > 7800:
        return {"id": tool_id, "truncated": True, "schema_complete": False}
    return {"id": tool_id, **tool, "truncated": False, "schema_complete": True, "schema_hash": snapshot.schema_hash}
