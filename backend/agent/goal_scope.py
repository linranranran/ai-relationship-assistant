"""公共业务范围校验：拥有数据访问权不代表用户要求修改这份数据。

只使用服务端目标、已授权工具回执和当前会话焦点。Plan 的引用必须先解析，
因此执行器在每一步执行前校验；Direct/Discovery 决策入口也提前复用。
"""
from backend.agent.contracts import ModelBusinessValidationError
from backend.agent.confirmation import MODEL_FORBIDDEN_ARGUMENTS
from backend.agent.goal_policy import active_goal
from backend.agent.goal_completion import build_completion_spec, record_facts


def validate_business_scope(state, calls, bindings):
    """模型不能凭空编 ID，也不能用允许工具操作另一个不属于当前目标的人。

    人物候选来自同一用户查询回执。完整执行前仍由工具层校验数据库 owner。
    """
    from backend.agent.contracts import PersonExtraction
    goal = active_goal(state)
    expected = state.get("extracted_info", {}).get("primary_person") or goal.get("person") or {}
    target = build_completion_spec(goal, state.get("extracted_info")).target_name
    ids = set().union(*bindings["people"].values()) if bindings["people"] else set()
    ids |= {"@self", "self_person_id"}
    intended = bindings["people"].get(target, set()) if target else set()
    spec = build_completion_spec(goal, state.get("extracted_info"))
    if not target and bindings.get("focus_id"):
        intended = {bindings["focus_id"]}
    elif not target and spec.query_kind == "relation_list":
        intended = bindings["people"].get("我", set())
    relations = state.get("extracted_info", {}).get("relations") or [goal.get("relation") or {}]
    for call in calls:
        args, tool = call["args"], call["tool"]
        if any(key in MODEL_FORBIDDEN_ARGUMENTS for key in args):
            raise ModelBusinessValidationError("TRUSTED_ARGUMENT_FORGED", "不能设置服务端身份、确认或幂等参数。")
        for key in ("person_id", "target_person_id", "from_person_id", "to_person_id", "through_person_id"):
            if args.get(key) and args[key] not in ids:
                raise ModelBusinessValidationError("UNVERIFIED_PERSON_ID", "人物 ID 必须来自已有工具回执；先查人并消除歧义。")
        if (target or spec.query_kind) and tool in {"get_person_detail", "update_person", "delete_person", "query_relation_path", "get_kinship_title", "list_relations"}:
            value = args.get("person_id") or args.get("target_person_id")
            if len(intended) != 1 or value not in intended:
                raise ModelBusinessValidationError("TARGET_MISMATCH", "当前目标人物尚未唯一绑定，或 ID 与当前目标不符。")
        if args.get("relation_id") and args["relation_id"] not in bindings["relations"]:
            raise ModelBusinessValidationError("UNVERIFIED_RELATION_ID", "关系 ID 必须先从关系查询中取得。")
        if tool == "find_person" and goal["intent"] != "SEMANTIC_SEARCH":
            names = {target} if goal["intent"] not in {"ADD_RELATION", "UPDATE_RELATION", "DELETE_RELATION"} else {relations[0].get("person"), relations[0].get("related_to")}
            if goal["intent"] in {"ADD_RELATION", "UPDATE_RELATION", "DELETE_RELATION"} and relations[0].get("through"):
                names.add(relations[0]["through"])
            if any(names) and args.get("query") not in names:
                raise ModelBusinessValidationError("SEARCH_TARGET_MISMATCH", "只能查找当前目标涉及的人物姓名。")
        if tool == "add_person":
            name = args.get("name")
            allowed_names = {expected.get("name")} if goal["intent"] == "ADD_PERSON" else {relations[0].get("person"), relations[0].get("related_to")}
            if name not in allowed_names:
                raise ModelBusinessValidationError("PERSON_SCOPE_VIOLATION", "只能新增当前目标提到的人物。")
            records = record_facts(goal, state)
            if not any(r.get("tool") == "find_person" and r.get("args", {}).get("query") == name
                       and r.get("args", {}).get("search_mode") == "exact" and r.get("error_code") == "NOT_FOUND" for r in records):
                raise ModelBusinessValidationError("MISSING_CREATE_PRECHECK", "新增前先精确查询姓名，只有明确 NOT_FOUND 才可新增。")
            if bindings["people"].get(name):
                raise ModelBusinessValidationError("PERSON_ALREADY_BOUND", "已有同名人物，不得重复新增；先消歧或补充资料。")
        if tool in {"add_person", "update_person"}:
            # Pydantic 的默认 gender/hobbies 是实现默认，不等于用户要求修改。
            for key in set(PersonExtraction.model_fields) - {"name"}:
                value = args.get(key)
                if value is not None and value != expected.get(key):
                    raise ModelBusinessValidationError("ATTRIBUTE_SCOPE_VIOLATION", "不得写入用户未指定的人物属性。")
            if args.get("photo_url") or (tool == "update_person" and args.get("name") and args["name"] != expected.get("name")):
                raise ModelBusinessValidationError("ATTRIBUTE_SCOPE_VIOLATION", "不得补充照片或擅自更改姓名。")
        if tool == "get_kinship_title":
            records = record_facts(goal, state)
            if not any(r.get("tool") == "query_relation_path" and r.get("status") in {"SUCCESS", "RESOLVED"}
                       and r.get("result", {}).get("success")
                       and r.get("args", {}).get("target_person_id") == args.get("target_person_id")
                       and r.get("result", {}).get("data") == args.get("relation_path") for r in records):
                raise ModelBusinessValidationError("UNVERIFIED_RELATION_PATH", "先查询目标人物的真实关系路径，称谓工具只能使用该查询结果。")
        if tool == "add_relation":
            rel = relations[0]
            # Extraction 的 person 是角色持有者；related_to 是被描述的人。
            # “张建国是杨鑫的上司”：person=张、related_to=杨，物理边为杨→张。
            # 校验器必须和图存储/Tool Schema 共用这个语义，不能拒绝正确计划。
            a, b = bindings["people"].get(rel.get("related_to"), set()), bindings["people"].get(rel.get("person"), set())
            av = state.get("self_person_id") if args.get("from_person_id") == "@self" else args.get("from_person_id")
            bv = state.get("self_person_id") if args.get("to_person_id") == "@self" else args.get("to_person_id")
            direct = len(a) == len(b) == 1 and av in a and bv in b
            symmetric = rel.get("type") == "朋友" and len(a) == len(b) == 1 and av in b and bv in a
            if not (direct or symmetric) or args.get("relation_type") != rel.get("type"):
                raise ModelBusinessValidationError("RELATION_SCOPE_VIOLATION", "关系双方、方向和类型必须符合当前目标。")
            if args.get("note"):
                raise ModelBusinessValidationError("ATTRIBUTE_SCOPE_VIOLATION", "当前目标未声明关系备注，不能擅自写入。")
            if rel.get("through") or args.get("through_person_id"):
                through = bindings["people"].get(rel.get("through"), set())
                if len(through) != 1 or args.get("through_person_id") not in through:
                    raise ModelBusinessValidationError("RELATION_SCOPE_VIOLATION", "中间人 ID 必须对应用户指定的中间人。")
        if tool in {"update_relation", "delete_relation"}:
            rel = bindings["relations"].get(args.get("relation_id"), {})
            names = relations[0]
            a = bindings["people"].get(names.get("person"), set())
            b = bindings["people"].get(names.get("related_to"), set())
            origin = rel.get("from_id", rel.get("source_person_id"))
            other = rel.get("to_id", rel.get("person_id", rel.get("other_id")))
            if len(a) != 1 or len(b) != 1 or not ((origin in a and other in b) or (origin in b and other in a)):
                raise ModelBusinessValidationError("RELATION_SCOPE_VIOLATION", "关系 ID 与本目标双方不符。")
            if tool == "update_relation" and args.get("new_relation_type") != names.get("type"):
                raise ModelBusinessValidationError("RELATION_TYPE_MISMATCH", "关系类型与目标不符。")
            if tool == "update_relation" and args.get("note"):
                raise ModelBusinessValidationError("ATTRIBUTE_SCOPE_VIOLATION", "不能写入当前目标未声明的关系备注。")
            if tool == "update_relation" and (names.get("through") or args.get("through_person_id")):
                through = bindings["people"].get(names.get("through"), set())
                if len(through) != 1 or args.get("through_person_id") not in through:
                    raise ModelBusinessValidationError("RELATION_SCOPE_VIOLATION", "中间人 ID 与目标不符。")
