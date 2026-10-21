"""Direct 请求账本和确定性执行边界，不识别意图、不建立 goal。

先读 direct_records（事实从哪里来），再读 validate_direct_call（哪些建议不能执行）。
这些规则只能证明 ID 有来源、操作有回执；自然语言是否全部完成仍由模型判断。
"""
from hashlib import sha256
import json

from backend.agent.contracts import ModelBusinessValidationError
from backend.agent.goal_completion import trusted_bindings

DIRECT_WRITE_TOOLS = frozenset({"add_person", "update_person", "delete_person",
                                "add_relation", "update_relation", "delete_relation"})
PERSON_ID_FIELDS = ("person_id", "target_person_id", "from_person_id", "to_person_id", "through_person_id")


def _merge_by_call_id(previous, current):
    # 恢复同一步、选择候选后用新观察替换旧观察；执行审计仍保留原始失败。
    return list({r["call_id"]: r for r in [*previous, *current] if r.get("call_id")}.values())


def direct_calls(state: dict) -> list[dict]:
    return _merge_by_call_id(state.get("direct_tool_calls", []), state.get("tool_calls", []))


def direct_records(state: dict) -> list[dict]:
    calls = {c["call_id"]: c for c in direct_calls(state)}
    return [{**r, "args": calls.get(r["call_id"], {}).get("args", r.get("args", {}))}
            for r in _merge_by_call_id(state.get("direct_tool_results", []), state.get("tool_results", []))]


def direct_bindings(state: dict) -> dict:
    # 复用回执解析能力，显式排除旧 goal 的依赖身份；不调用完成合同。
    records = direct_records(state)
    bindings = trusted_bindings({**state, "goals": [], "active_goal_id": None}, records)
    deleted = {r.get("args", {}).get("person_id") for r in records
               if r.get("tool") == "delete_person" and (r.get("result") or {}).get("success")}
    bindings["people"] = {name: ids - deleted for name, ids in bindings["people"].items() if ids - deleted}
    return bindings


def mutation_fingerprint(call: dict) -> str:
    """确认只影响授权，不影响操作内容。拒绝后同样内容不能换 ID 重提。"""
    args = {k: v for k, v in call.get("args", {}).items() if k != "confirmed"}
    return sha256(json.dumps([call["tool"], args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_direct_call(state: dict, call: dict) -> None:
    """检查可确定的事实边界；新增免确认范围由 direct_write_policy 检查。

    读取到 ID 不等于用户授权修改该人，因此修改/删除仍必须经过确认。
    执行器在引用/默认值解析后还会再次调用，覆盖确认与 WAIT 的直接恢复入口。
    """
    from backend.agent.confirmation import MODEL_FORBIDDEN_ARGUMENTS
    args, tool = call["args"], call["tool"]
    if state.get("response_kind") == "sales_preparation" and tool not in {
        "find_person", "get_person_detail", "get_sales_preparation_context"
    }:
        # 同一检查也由 execute_tools 在真实参数解析后调用。故恢复旧检查点、
        # 篡改建议或聊天提示注入都不能绕过只读边界；不只依赖模型守提示词。
        raise ModelBusinessValidationError("SALES_READ_ONLY", "沟通准备只允许读取客户资料与证据，修改请另发普通请求。")
    if any(k in MODEL_FORBIDDEN_ARGUMENTS for k in args):
        raise ModelBusinessValidationError("TRUSTED_ARGUMENT_FORGED", "身份、确认和幂等参数只能由服务端提供。")
    bindings, records = direct_bindings(state), direct_records(state)
    ids = set().union(*bindings["people"].values()) if bindings["people"] else set()
    for key in PERSON_ID_FIELDS:
        if args.get(key) and args[key] not in ids:
            raise ModelBusinessValidationError("UNVERIFIED_PERSON_ID", "人物 ID 必须来自本人、已验证指代或真实工具回执；先查找人物。")
    if args.get("relation_id") and args["relation_id"] not in bindings["relations"]:
        raise ModelBusinessValidationError("UNVERIFIED_RELATION_ID", "关系 ID 必须先从真实关系查询中取得。")
    if tool == "add_person":
        probes = [r for r in records if r.get("tool") == "find_person"
                  and r.get("args", {}).get("query") == args.get("name")
                  and r.get("args", {}).get("search_mode") == "exact"]
        if not probes or probes[-1].get("error_code") != "NOT_FOUND":
            raise ModelBusinessValidationError("MISSING_CREATE_PRECHECK", "新增前必须先精确查姓名，最新结果明确 NOT_FOUND。")
        if bindings["people"].get(args.get("name")):
            raise ModelBusinessValidationError("PERSON_ALREADY_BOUND", "已有同名人物，不能重复新增。")
    if tool == "get_kinship_title" and not any(
        r.get("tool") == "query_relation_path" and (r.get("result") or {}).get("success")
        and r.get("args", {}).get("target_person_id") == args.get("target_person_id")
        and r["result"].get("data") == args.get("relation_path") for r in records
    ):
        raise ModelBusinessValidationError("UNVERIFIED_RELATION_PATH", "称谓计算必须使用目标人物真实查询得到的关系路径。")
    if tool in DIRECT_WRITE_TOOLS and mutation_fingerprint(call) in state.get("direct_rejected_mutations", []):
        raise ModelBusinessValidationError("USER_REJECTED_MUTATION", "用户已拒绝此操作，不得换调用 ID 重提；处理其他独立要求。")


def build_direct_confirmation(state: dict, call: dict) -> dict:
    """服务端展示业务内容；执行参数和授权指纹留在状态中，不混入用户文案。

    摘要显示姓名、属性变更和清空操作，不截断实际批准的字段值。
    确认仍绑定原 call_id + 完整参数指纹，展示简化不代表授权范围扩大。
    """
    from backend.agent.confirmation_display import direct_confirmation_summary
    bindings = direct_bindings(state)
    # 新增关系回执可能只返回姓名和 relation_id。补齐该次成功操作的实际参数，
    # 以展示关系双方；已查询到的关系属性优先。这里不使用模型未执行的建议。
    for record in direct_records(state):
        data = (record.get("result") or {}).get("data")
        if (record.get("tool") == "add_relation" and record.get("status") in {"SUCCESS", "RESOLVED"}
                and (record.get("result") or {}).get("success") and isinstance(data, dict)
                and data.get("relation_id") and data["relation_id"] == call["args"].get("relation_id")):
            args = record.get("args", {})
            previous = bindings["relations"].get(data["relation_id"], {})
            bindings["relations"][data["relation_id"]] = {
                "from_id": args.get("from_person_id"), "to_id": args.get("to_person_id"),
                "type": args.get("relation_type"), **previous,
            }
    return {"summary": direct_confirmation_summary(state, call, bindings),
            "tool_names": [call["tool"]], "call_id": call["call_id"],
            "mutation_fingerprint": mutation_fingerprint(call), "interaction_type": "CONFIRMATION"}
