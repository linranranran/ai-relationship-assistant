"""确认展示与执行参数分离：用户看姓名和变更，内部 ID/指纹仍保存在状态中。

文案来自服务端模板和可信工具回执，不再让模型生成授权摘要，不输出参数 JSON。
修改时展示已读取的旧值；没读取就明确说明，不能把未知当前值写成“暂无记录”。
"""


PERSON_FIELDS = {
    "name": "姓名", "gender": "性别", "birth_year": "出生年份", "occupation": "职业",
    "education": "学历", "hobbies": "爱好", "personality": "性格", "tags": "标签",
    "note": "备注", "photo_url": "照片地址",
}
DISPLAYED_ARGS = {
    "add_person": set(PERSON_FIELDS),
    "update_person": set(PERSON_FIELDS) | {"person_id"},
    "delete_person": {"person_id"},
    "add_relation": {"from_person_id", "to_person_id", "relation_type", "through_person_id", "note"},
    "update_relation": {"relation_id", "new_relation_type", "through_person_id", "note"},
    "delete_relation": {"relation_id"},
}


def _value(value, *, clearing=False) -> str:
    if value is None or value == "" or value == []:
        return "清空" if clearing else "未填写"
    return "、".join(map(str, value)) if isinstance(value, list) else str(value)


def person_label(state: dict, bindings: dict, person_id: str | None) -> str:
    """本人显示为“你”；同名用已知资料区分，绝不回退成原始 ID。"""
    if not person_id:
        return "未读取人物"
    if person_id == state.get("self_person_id"):
        return "你"
    data = bindings.get("person_data", {}).get(person_id, {})
    name = data.get("name") or next((name for name, ids in bindings["people"].items()
                                    if person_id in ids and not name.startswith("@")), "所选人物")
    if len(bindings["people"].get(name, set())) > 1:
        position = sorted(bindings["people"][name]).index(person_id) + 1
        detail = [f"{PERSON_FIELDS[key]}：{_value(data[key])}"
                  for key in ("occupation", "birth_year", "education") if data.get(key)]
        detail.insert(0, f"同名记录 {position}")
        name += "（" + "；".join(detail) + "）"
    return name


def _changes(args: dict, fields: dict, previous: dict) -> list[str]:
    lines = []
    for key, label in fields.items():
        if key not in args or args[key] is None:
            continue
        old = _value(previous[key]) if key in previous else "未读取当前值"
        lines.append(f"{label}：{old} → {_value(args[key], clearing=True)}")
    return lines


def direct_confirmation_summary(state: dict, call: dict, bindings: dict) -> str:
    """完整展示有意义的字段和清空操作，不展示空结构及新增时的无害默认值。"""
    tool, args = call["tool"], call["args"]
    # 以后增加 Tool 字段时必须同步展示；不能让用户批准未在文案中出现的字段。
    if set(args) - DISPLAYED_ARGS.get(tool, set()) - {"confirmed"}:
        raise ValueError("存在尚未定义确认展示模板的业务字段")
    def label(person_id):
        return person_label(state, bindings, person_id)
    person = bindings.get("person_data", {}).get(args.get("person_id"), {})
    relation = bindings.get("relations", {}).get(args.get("relation_id"), {})
    relation_from = relation.get("from_id", relation.get("source_person_id"))
    relation_to = relation.get("to_id", relation.get("person_id", relation.get("other_id")))
    # list_relations / get_person_detail 的关系投影是无向查询，source_person_id
    # 表示“查询谁”，不证明关系起点。只有回执明确给出 from/to 时才显示箭头。
    from_label, to_label = label(relation_from), label(relation_to)
    if to_label in {"所选人物", "未读取人物"}:
        to_label = relation.get("to_name", relation.get("person_name", relation.get("other_name"))) or to_label
    connector = " → " if relation.get("from_id") and relation.get("to_id") else " 与 "
    endpoints = from_label + connector + to_label
    if tool == "add_person":
        lines = [f"记录新人物：{args['name']}"]
        for key, field in PERSON_FIELDS.items():
            value = args.get(key)
            if key == "name" or value is None or value == "" or value == [] or (key == "gender" and value == "未知"):
                continue
            lines.append(f"{field}：{_value(value)}")
    elif tool == "update_person":
        lines = [f"修改人物资料：{label(args.get('person_id'))}", *_changes(args, PERSON_FIELDS, person)]
    elif tool == "delete_person":
        lines = [f"删除人物：{label(args.get('person_id'))}", "会同时删除与此人物相连的关系。"]
    elif tool == "add_relation":
        lines = [f"建立关系：{label(args.get('from_person_id'))} → {label(args.get('to_person_id'))}",
                 f"关系类型：{args['relation_type']}"]
        if args.get("through_person_id"):
            lines.append(f"介绍人：{label(args['through_person_id'])}")
        if args.get("note"):
            lines.append(f"备注：{args['note']}")
    elif tool == "update_relation":
        old_type = relation.get("relation_type", relation.get("type"))
        previous = {"new_relation_type": old_type} if old_type is not None else {}
        if "note" in relation:
            previous["note"] = relation["note"]
        lines = [f"修改关系：{endpoints}", *_changes(args, {"new_relation_type": "关系类型", "note": "备注"}, previous)]
        if args.get("through_person_id"):
            old_id = relation.get("through_person_id", relation.get("through"))
            old = label(old_id) if old_id else (
                "未填写" if "through_person_id" in relation or "through" in relation else "未读取当前值"
            )
            lines.append(f"介绍人：{old} → {label(args['through_person_id'])}")
    elif tool == "delete_relation":
        lines = [f"删除关系：{endpoints}"]
        if relation.get("relation_type", relation.get("type")):
            lines.append(f"关系类型：{relation.get('relation_type', relation.get('type'))}")
    else:
        raise ValueError("没有为此写工具定义确认展示模板")
    return "\n".join(lines)
