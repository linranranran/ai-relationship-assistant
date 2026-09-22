"""高风险 Tool 的确认策略。

这一层必须是普通 Python 代码，不能把授权判断交给大模型。
大模型只能提出“想调用哪个 Tool”，只有这里可以把用户确认转换成
Tool 真正接受的 ``confirmed=True``。
"""
from backend.db import neo4j_client
from copy import deepcopy
from typing import Any
from backend.common import common_tool
from backend.tools.registry import TOOL_ARGUMENT_MODELS,TOOL_REGISTRY
from backend.exception.AgentException import ToolPlanValidationError
from backend.agent.tool_dependencies import (
    build_step_metadata,
    collect_reference_steps,
)

# 第一版只保护删除操作。以后增加“批量修改、合并人物、发送消息”等能力时，
# 只需要在这里扩展白名单，不要把判断散落到 prompt 和各个节点中。
HIGH_RISK_TOOLS = frozenset({"delete_person", "delete_relation"})

# 这些参数属于服务端信任边界，大模型即使生成了也必须丢弃。
MODEL_FORBIDDEN_ARGUMENTS = frozenset(
    {"owner_id", "user_id", "self_person_id", "confirmed", "idempotency_key"}
)

def sanitize_model_tool_calls(raw_tool_calls: Any) -> list[dict]:
    """清洗模型产生的 Tool 计划。

    1. 按 Tool 定义校验必填参数和参数类型；
    2. 拒绝未知 Tool；
    3. 为每个调用生成 call_id，供审计和幂等控制使用。

    当前骨架先完成最关键的权限隔离：模型不能伪造 owner_id 和 confirmed。
    """
    if not isinstance(raw_tool_calls, list):
        return []

    sanitized: list[dict] = []
    known_step_ids: set[str] = set()
    for index, item in enumerate(raw_tool_calls):
        # 1、校验LLM返回的数据类型,过滤掉不合法的工具名
        if not isinstance(item, dict):
            raise ToolPlanValidationError(f"第 {index + 1} 个 Tool 计划不是对象")
        if not isinstance(item.get("tool"), str) or item.get("tool") not in TOOL_REGISTRY:
            raise ToolPlanValidationError(f"未知 Tool：{item.get('tool')}")
        tool_name = item.get("tool")
        step_id, dependencies = build_step_metadata(item, index, known_step_ids)
        raw_args = item.get("args", {})
        if not isinstance(raw_args, dict):
            raw_args = {}
        # 2、过滤掉不安全的参数
        safe_args = {
            key: value
            for key, value in raw_args.items()
            if key not in MODEL_FORBIDDEN_ARGUMENTS
        }
        # 3、校验参数的类型
        try:
            argument_model = TOOL_ARGUMENT_MODELS[tool_name]
            reference_steps = collect_reference_steps(safe_args)
            if reference_steps:
                # 引用尚未解析，当前只能校验必填参数是否出现；具体类型在执行前
                # 解析出真实值后再次用 Pydantic 校验。
                missing = [
                    field_name
                    for field_name, field in argument_model.model_fields.items()
                    if field.is_required() and field_name not in safe_args
                ]
                if missing:
                    raise ValueError(f"缺少必填参数：{', '.join(missing)}")
            else:
                validated_args = argument_model.model_validate(safe_args)
                safe_args = validated_args.model_dump(exclude_none=True)
        except Exception as e:
            # 3.1 如果校验失败，把错误信息回传给 LLM，让它修正
            print(f"参数校验失败：tool_name={tool_name}, tool_args={safe_args}")
            raise ToolPlanValidationError(f"{tool_name} 参数校验失败：{e}")
        # 4、生成call_id
        call_id = common_tool.generate_prefix_uuid("call_")
        sanitized.append(
            {
                "step_id": step_id,
                "call_id": call_id,
                "tool": tool_name,
                "args": safe_args,
                "depends_on": dependencies,
            }
        )
        known_step_ids.add(step_id)
    return sanitized


def build_confirmation_request(tool_calls: list[dict] ,owner_id:str) -> dict:
    """生成返回给前端的结构化确认信息。

    摘要由服务端按 Tool 类型生成，不能直接照抄模型的“已确认”等文本。
    TODO(你来实现)：查询人物姓名/关系双方，让摘要从 ID 变成可读文本，
    例如“将删除张三以及与他相连的 4 条关系”。
    """
    risky_calls = [call for call in tool_calls if call.get("tool") in HIGH_RISK_TOOLS]
    tool_names = [call["tool"] for call in risky_calls]

    summary_parts: list[str] = []
    for call in risky_calls:
        args = call.get("args", {})
        if call["tool"] == "delete_person":
            person_name = args.get('person_id')
            relation_count = 0
            if args.get('person_id', None) is not None:
                with neo4j_client.get_neo4j_driver() as driver:
                    person_detail = neo4j_client.get_person_detail(driver, args['person_id'],owner_id)
                if person_detail is not None:
                    person_dict = person_detail.get("person", {})
                    relation_list = person_detail.get("relations", [])
                    person_name = person_dict.get("name", person_name)
                    relation_count = len(relation_list)
            summary_parts.append(f"删除人物 {person_name},同时会删除与他关联的{relation_count}条人际关系，该操作执行后无法恢复。")
        elif call["tool"] == "delete_relation":
            relation_id = args.get('relation_id')
            if relation_id is not None:
                with neo4j_client.get_neo4j_driver() as driver:
                    relation = neo4j_client.get_relation_by_id(driver, relation_id , owner_id)
                if relation is not None:
                    from_name = relation.get("from_name")
                    to_name = relation.get("to_name")
                    if from_name is not None and to_name is not None:
                        summary_parts.append(f"删除从{from_name}到{to_name}关系 {args.get('relation_id', '未知关系')}")
                    else:
                        summary_parts.append(f"删除未知关系")

    return {
        "summary": "；".join(summary_parts) or "执行高风险操作",
        "tool_names": tool_names,
    }


def authorize_high_risk_calls(
    tool_calls: list[dict],
    only_index: int | None = None,
) -> list[dict]:
    """在用户明确确认后，由服务端注入授权标记。

    依赖执行器采用逐个高风险步骤确认，因此 request_confirmation 会传入
    当前执行下标，避免一次确认授权尚未展示的后续操作。
    """
    authorized = deepcopy(tool_calls)
    for index, call in enumerate(authorized):
        if only_index is not None and index != only_index:
            continue
        if call.get("tool") in HIGH_RISK_TOOLS:
            call.setdefault("args", {})["confirmed"] = True
    return authorized
