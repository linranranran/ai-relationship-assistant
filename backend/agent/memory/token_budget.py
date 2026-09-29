"""上下文 Token 预算与 Tool 结果压缩。

本模块只做确定性预算，不调用 LLM。这样即使摘要服务或向量库不可用，Agent 仍能
知道自己最多可以发送多少内容，并且不会把整个数据库查询结果塞进提示词。
"""

from typing import Any

from backend.agent.memory.models import MemorySettings


def calculate_input_budget(settings: MemorySettings) -> int:
    """校验配置并计算本轮最大输入 Token。

    注意：``max_input_tokens`` 是项目主动设置的输入上限，不一定等于模型厂商宣称
    的完整上下文窗口。主动留出输出和安全余量可以降低生产中偶发超限的概率。
    """
    if settings.max_input_tokens <= 0:
        raise ValueError("AGENT_MAX_INPUT_TOKENS 必须大于 0")
    if settings.reserved_output_tokens < 0 or settings.safety_tokens < 0:
        raise ValueError("输出预留和安全余量不能为负数")
    if settings.recent_message_limit < 0 or settings.memory_retrieval_limit < 0:
        raise ValueError("消息数量和长期记忆数量上限不能为负数")
    if not 0 < settings.summary_trigger_ratio < 1:
        raise ValueError("AGENT_SUMMARY_TRIGGER_RATIO 必须在 0 和 1 之间")

    budget = (
        settings.max_input_tokens
        - settings.reserved_output_tokens
        - settings.safety_tokens
    )
    if budget <= 0:
        raise ValueError("输出预留与安全余量已经占满全部上下文预算")
    return budget


def compact_tool_results(results: list[dict]) -> list[dict]:
    """把 Tool 结果压缩成适合推理的稳定结构。

    执行器的业务返回位于 ``record.result.data``，不能误读顶层 ``data``。每种
    Tool 只保留后续计划需要的字段；未知 Tool 才使用通用小标量兜底。
    """
    compacted: list[dict] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        result = {
            key: item[key]
            for key in ("step_id", "tool", "tool_name", "status", "error_code", "message")
            if key in item and _is_small_scalar(item[key])
        }
        tool_name = item.get("tool") or item.get("tool_name")
        tool_response = item.get("result")
        data = tool_response.get("data") if isinstance(tool_response, dict) else None
        compacted_data = _compact_tool_data(str(tool_name or ""), data)
        if compacted_data not in (None, {}, []):
            result["data"] = compacted_data
        compacted.append(result)
    return compacted


def _compact_tool_data(tool_name: str, data: Any) -> Any:
    """按 Tool 契约压缩结果，所有列表都设置硬上限。"""
    if tool_name == "find_person":
        if isinstance(data, dict):
            candidates = data.get("candidates") or data.get("results") or []
            return {
                "found": data.get("found"),
                "count": data.get("count", len(candidates) if isinstance(candidates, list) else None),
                "candidates": _compact_people(candidates, limit=5),
            }
        return {"candidates": _compact_people(data, limit=5)}
    if tool_name == "query_relation_path" and isinstance(data, dict):
        path = data.get("path") or data.get("relation_path") or []
        return {
            "from_person_id": data.get("from_person_id"),
            "target_person_id": data.get("target_person_id"),
            "path": path[:8] if isinstance(path, list) else path,
            "depth": data.get("depth"),
        }
    if tool_name == "get_person_detail" and isinstance(data, dict):
        person = data.get("person") if isinstance(data.get("person"), dict) else data
        relations = data.get("relations", [])
        return {
            "person": _compact_data_dict(person),
            "relations": _compact_relations(relations, limit=8),
        }
    if tool_name == "list_relations":
        relations = data.get("relations", []) if isinstance(data, dict) else data
        return {"relations": _compact_relations(relations, limit=10)}
    if tool_name in {
        "add_person", "update_person", "delete_person",
        "add_relation", "update_relation", "delete_relation",
        "get_kinship_title",
    }:
        return _compact_data_dict(data) if isinstance(data, dict) else data
    if isinstance(data, dict):
        return _compact_data_dict(data)
    return data if _is_small_scalar(data) else None


def _compact_people(value: Any, *, limit: int) -> list[dict]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [
        {
            key: person[key]
            for key in ("id", "person_id", "name", "gender", "occupation")
            if key in person and _is_small_scalar(person[key])
        }
        for person in value[:limit]
        if isinstance(person, dict)
    ]


def _compact_relations(value: Any, *, limit: int) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {
            key: relation[key]
            for key in (
                "relation_id", "type", "relation_type", "person_id", "person_name",
                "from_person_id", "to_person_id",
            )
            if key in relation and _is_small_scalar(relation[key])
        }
        for relation in value[:limit]
        if isinstance(relation, dict)
    ]


def _compact_data_dict(data: dict[str, Any]) -> dict[str, Any]:
    """通用兜底压缩；最多保留十个稳定小字段。"""
    preferred = (
        "id",
        "person_id",
        "relation_id",
        "name",
        "relation_type",
        "kinship_title",
        "count",
        "found",
        "success",
    )
    compact: dict[str, Any] = {}
    for key in preferred:
        if key in data and _is_small_scalar(data[key]):
            compact[key] = data[key]
    for key, value in data.items():
        if len(compact) >= 10:
            break
        if key not in compact and _is_small_scalar(value):
            compact[key] = value
    return compact


def _is_small_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and len(value) <= 500
