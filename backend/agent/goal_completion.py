"""业务完成合同：工具执行成功只是证据，不能代替目标验收。

此模块不调用模型或数据库。Observe 和 finalize_goal 使用同一规则，避免
Direct 的 find_person 成功后提前收尾，也避免模型一句“完成了”绕过验收。
"""
from dataclasses import dataclass, field
from enum import StrEnum


class CompletionStatus(StrEnum):
    SATISFIED = "SATISFIED"
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class GoalCompletionSpec:
    intent: str
    target_name: str | None = None
    query_kind: str | None = None
    requested_fields: tuple[str, ...] = ()
    expected_attributes: dict = field(default_factory=dict)
    relation: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GoalEvaluation:
    status: CompletionStatus
    evidence_call_ids: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    outcome: str = ""
    reason_code: str = ""

    def as_dict(self):
        return {"status": self.status.value, "evidence_call_ids": list(self.evidence_call_ids),
                "missing_requirements": list(self.missing_requirements),
                "outcome": self.outcome, "reason_code": self.reason_code}


def build_completion_spec(goal: dict, extracted_info: dict | None = None) -> GoalCompletionSpec:
    info = extracted_info or {}
    person = info.get("primary_person") or goal.get("person") or {}
    query = goal.get("query_spec") or {}
    relations = info.get("relations") or []
    relation = relations[0] if len(relations) == 1 else (goal.get("relation") or {})
    # Extraction 默认 None，显式 [] 表示清空，不能等同“用户未提供”。
    attrs = {k: v for k, v in person.items() if v is not None}
    return GoalCompletionSpec(goal["intent"], query.get("target_name") or person.get("name"),
                              query.get("kind"), tuple(query.get("requested_fields", [])), attrs, relation)


def record_facts(goal: dict, state: dict) -> list[dict]:
    """为旧步骤回执补入实际参数；失败保留，不能只归档成功再丢失排障证据。"""
    calls = {c["call_id"]: c for c in [*goal.get("previous_tool_calls", []), *state.get("tool_calls", [])]}
    records = {r["call_id"]: r for r in [*goal.get("previous_tool_results", []), *state.get("tool_results", [])]}
    return [{**r, "args": calls.get(r["call_id"], {}).get("args", r.get("args", {}))}
            for r in records.values()]


def trusted_bindings(state: dict, records: list[dict]) -> dict:
    """绑定只来自执行回执或服务端已校验焦点，绝不从模型的 args 声称中产生。

    value 为姓名到 ID 集合；同名多个 ID 不能自动选择。关系 ID 来自 list_relations。
    """
    people, relations, owners = {}, {}, {}
    for name in ("当前用户", "我", "自己", "@self", state.get("user_name") or "当前用户"):
        if state.get("self_person_id"):
            people[name] = {state["self_person_id"]}
    ref = state.get("resolved_reference") or {}
    valid_focus = (ref.get("owner_id") == state.get("user_id")
                   and ref.get("conversation_id") == state.get("conversation_id", "default"))
    if valid_focus:
        if ref.get("person_id"):
            people.setdefault(ref.get("name") or "@focus", set()).add(ref["person_id"])
    # 前置成功目标只贡献可信身份，不充当当前目标的完成证据。
    # 例如先创建张三、再建立客户关系，不必为了取得已知 ID 再查询一次。
    goal_by_id = {g["goal_id"]: g for g in state.get("goals", [])}
    current = goal_by_id.get(state.get("active_goal_id"), {})
    dependency_records, visited = [], set()
    def visit(goal_id):
        if goal_id in visited:
            return
        visited.add(goal_id)
        dependency = goal_by_id.get(goal_id, {})
        if dependency.get("status") != "SUCCEEDED":
            return
        for parent in dependency.get("depends_on", []):
            visit(parent)
        dependency_records.extend(dependency.get("tool_results", []))
    for goal_id in current.get("depends_on", []):
        visit(goal_id)
    for record in [*dependency_records, *records]:
        result = record.get("result") or {}
        if not result.get("success") or record.get("status") not in {"SUCCESS", "RESOLVED"}:
            continue
        tool, data = record.get("tool"), result.get("data")
        if tool == "get_person_detail" and isinstance(data, dict):
            data = data.get("person", data)
        if tool in {"find_person", "add_person", "update_person", "get_person_detail"}:
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    people.setdefault(item["name"], set()).add(item["id"])
                    owners[item["id"]] = item
        if tool in {"list_relations", "add_relation", "get_person_detail"}:
            if tool == "get_person_detail":
                data = (result.get("data") or {}).get("relations", [])
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("relation_id"):
                    relations[item["relation_id"]] = {**item, "source_person_id": record.get("args", {}).get("person_id")}
    return {"people": people, "relations": relations, "person_data": owners,
            "focus_id": ref.get("person_id") if valid_focus else None}


def evaluate_goal(spec: GoalCompletionSpec, records: list[dict], bindings: dict) -> GoalEvaluation:
    """按当前目标字段核验；历史失败被后续合法证据修复后，不永久否定成功。"""
    successes = [r for r in records if r.get("status") in {"SUCCESS", "RESOLVED"}
                 and isinstance(r.get("result"), dict) and r["result"].get("success")]
    people = bindings.get("people", {})
    ids = people.get(spec.target_name, set()) if spec.target_name else set()
    if not ids and not spec.target_name and bindings.get("focus_id"):
        ids = {bindings["focus_id"]}
    if not ids and not spec.target_name and spec.query_kind == "relation_list":
        ids = people.get("我", set())
    target = next(iter(ids)) if len(ids) == 1 else None
    if spec.target_name and len(ids) > 1:
        return GoalEvaluation(CompletionStatus.BLOCKED, missing_requirements=("唯一人物身份",), reason_code="AMBIGUOUS")

    def satisfied(r, outcome="FOUND"):
        return GoalEvaluation(CompletionStatus.SATISFIED, (r["call_id"],), outcome=outcome)

    if spec.intent == "CHITCHAT":
        return GoalEvaluation(CompletionStatus.SATISFIED, outcome="CHITCHAT")
    # 合法的负向查询也是已完成查询；绝不能把它当作人物创建成功。
    if spec.intent in {"QUERY_PERSON", "SEMANTIC_SEARCH"} and spec.target_name:
        for r in reversed(records):
            if (r.get("tool") == "find_person" and r.get("args", {}).get("query") == spec.target_name
                    and r.get("args", {}).get("search_mode") == "exact"):
                if r.get("error_code") == "NOT_FOUND":
                    return satisfied(r, "NOT_FOUND")
                break
    reads = {"QUERY_PERSON": "get_person_detail", "QUERY_KINSHIP": "get_kinship_title",
             "LIST_RELATIONS": "list_relations", "SEMANTIC_SEARCH": "find_person"}
    if spec.intent in reads:
        required = "query_relation_path" if spec.query_kind == "relation_path" else reads[spec.intent]
        for r in reversed(successes):
            legacy_path = spec.intent == "QUERY_PERSON" and spec.query_kind is None and r.get("tool") == "query_relation_path"
            if r.get("tool") != required and not legacy_path:
                continue
            args, data = r.get("args", {}), r["result"].get("data")
            if r.get("tool") == "get_person_detail" and isinstance(data, dict):
                data = data.get("person", data)
            rid = args.get("person_id") or args.get("target_person_id")
            if required == "find_person":
                if not spec.target_name or args.get("query") == spec.target_name:
                    return satisfied(r)
            elif target and rid == target:
                if r.get("tool") == "get_person_detail" and (not isinstance(data, dict) or data.get("id") != target):
                    continue
                if required == "get_kinship_title" and not any(
                    path.get("tool") == "query_relation_path"
                    and path.get("args", {}).get("target_person_id") == target
                    and path.get("result", {}).get("data") == args.get("relation_path")
                    for path in successes
                ):
                    continue  # 称谓计算结果不能证明模型编造的路径真实存在。
                # Neo4j 中缺少字段表示暂无记录；成功查询正确人物仍可完成，回复不得编造。
                return satisfied(r, "NO_RECORD" if isinstance(data, dict) and spec.requested_fields
                                 and any(not data.get(f) for f in spec.requested_fields) else "FOUND")
            elif spec.query_kind is None and not spec.target_name and not target and not spec.requested_fields:
                # 旧 Checkpoint 没有结构化查询目标，保留原只读合同兼容性。
                return satisfied(r)
        return GoalEvaluation(CompletionStatus.PENDING, missing_requirements=("目标人物的查询回执", *spec.requested_fields))
    if spec.intent in {"ADD_PERSON", "UPDATE_PERSON"}:
        tools = {"add_person", "update_person"} if spec.intent == "ADD_PERSON" else {"update_person"}
        for r in reversed(successes):
            data = r["result"].get("data")
            if r.get("tool") not in tools or not isinstance(data, dict) or not data.get("id"):
                continue
            if target and data["id"] != target:
                continue
            if spec.target_name and not target and data.get("name") != spec.target_name:
                continue
            # 后续删除/更新会使旧快照失效，不能用早先的成功覆盖现在缺失的属性。
            later = successes[successes.index(r) + 1:]
            if any(x.get("tool") in {"update_person", "delete_person"} and x.get("args", {}).get("person_id") == data["id"] for x in later):
                continue
            if all(data.get(k) == v for k, v in spec.expected_attributes.items()):
                return satisfied(r, "SAVED")
        return GoalEvaluation(CompletionStatus.PENDING, missing_requirements=("人物属性保存回执",))
    relation_tools = {"ADD_RELATION": "add_relation", "UPDATE_RELATION": "update_relation", "DELETE_RELATION": "delete_relation"}
    if spec.intent in relation_tools:
        # A-[角色]->B 表示 B 是 A 的角色；抽取字段 person 指 B，related_to 指 A。
        # 验收必须核对同一物理方向，不能因为双方姓名正确就认可反向的上司/客户边。
        names = [spec.relation.get("related_to"), spec.relation.get("person")]
        endpoints = [people.get(n, set()) for n in names]
        for r in reversed(successes):
            if r.get("tool") != relation_tools[spec.intent]:
                continue
            args, data = r.get("args", {}), r["result"].get("data") or {}
            if spec.intent == "ADD_RELATION":
                forward = all(len(s) == 1 for s in endpoints) and args.get("from_person_id") in endpoints[0] and args.get("to_person_id") in endpoints[1]
                reverse_friend = spec.relation.get("type") == "朋友" and all(len(s) == 1 for s in endpoints) and args.get("from_person_id") in endpoints[1] and args.get("to_person_id") in endpoints[0]
                through_ids = people.get(spec.relation.get("through"), set())
                through_ok = not spec.relation.get("through") or (len(through_ids) == 1 and args.get("through_person_id") in through_ids)
                if ((forward or reverse_friend) and through_ok
                        and args.get("relation_type") == spec.relation.get("type")
                        and isinstance(data, dict) and data.get("relation_id")):
                    return satisfied(r, "SAVED")
            elif args.get("relation_id") in bindings.get("relations", {}):
                rel = bindings["relations"][args["relation_id"]]
                a = rel.get("from_id", rel.get("source_person_id"))
                b = rel.get("to_id", rel.get("person_id", rel.get("other_id")))
                if all(len(s) == 1 for s in endpoints) and ((a in endpoints[0] and b in endpoints[1]) or (a in endpoints[1] and b in endpoints[0])):
                    through_ids = people.get(spec.relation.get("through"), set())
                    through_ok = not spec.relation.get("through") or (len(through_ids) == 1 and args.get("through_person_id") in through_ids)
                    if spec.intent == "DELETE_RELATION" or (args.get("new_relation_type") == spec.relation.get("type") and through_ok):
                        return satisfied(r, "SAVED")
        return GoalEvaluation(CompletionStatus.PENDING, missing_requirements=("目标关系的有效操作回执",))
    if spec.intent == "DELETE_PERSON":
        for r in reversed(successes):
            if r.get("tool") == "delete_person" and target and r.get("args", {}).get("person_id") == target:
                return satisfied(r, "DELETED")
        return GoalEvaluation(CompletionStatus.PENDING, missing_requirements=("目标人物删除回执",))
    return GoalEvaluation(CompletionStatus.BLOCKED, reason_code="UNSUPPORTED_COMPLETION_CONTRACT")


def covered_requirements(spec: GoalCompletionSpec, bindings: dict) -> list[str]:
    """部分属性保存也算真实进展；重复写同值不会产生新的覆盖标识。

    绑定中的 person_data 是按回执顺序更新的最新快照，不使用旧快照证明
    目前的属性。完整目标仍由 evaluate_goal 核验，覆盖指标不授予执行权限。
    """
    ids = bindings.get("people", {}).get(spec.target_name, set())
    if len(ids) != 1:
        return []
    target = next(iter(ids))
    person = bindings.get("person_data", {}).get(target, {})
    return [f"{target}:{key}" for key, value in spec.expected_attributes.items()
            if key in person and person[key] == value]
