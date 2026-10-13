"""目标模式入口及 Discovery 循环；Direct 不使用本文件的编排节点。

strategy_dispatch 把 Plan 交给 plan_tasks，把 Discovery 交给下面的逐轮决策节点。
Direct 的唯一入口在 direct_nodes.py，共用工具协议位于 strategies/tool_protocol.py。

建议先读四个主函数，再回头读辅助函数：
1. decide_next：检查已有证据；还未完成才让模型提出下一个动作。
2. validate_decision：校验动作，把业务调用转成公共执行器可接受的步骤。
3. execute_discovery：仅 Discovery 搜索/描述工具目录时走这里，不写业务数据。
4. observe_strategy_execution：回填真实结果，检查目标完成条件和循环停止条件。

Discovery 业务主链：decide_next -> validate_decision -> execute_tools -> observe_execution。
其中 observe_execution 在 Python 内委托本文件的 observe_strategy_execution，
再返回 decide_next / finalize_goal / ask_clarification / END 的路由。
Discovery 目录链：validate_decision -> execute_discovery -> observe_strategy；
图中的 observe_strategy 注册的处理函数也是 observe_strategy_execution。

模型原始 Function Calling 被策略适配器转换为统一的 StrategyDecision：
EXECUTE_BATCH / SEARCH / DESCRIBE / ASK_CLARIFICATION / FINISH_CANDIDATE。
Discovery 每轮至多一个动作，EXECUTE_BATCH 也只含一个业务工具；
Plan 才一次提供多步骤计划，strategy_dispatch 会把它交给 plan_tasks。

一次用户请求可有多个目标；本文件始终围绕 active_goal_id 运转，不重新分类
整句请求。finalize_goal 收尾当前目标后，由外部调度器选择下一个目标。
循环通过 Command 路由实现，节点内部不另建 while；pending_decision、工具步骤
和观察反馈在节点之间进入图状态，供 Checkpointer 恢复。持久化保障仍取决于
Checkpointer / durability 配置和业务工具幂等，不能只靠“节点拆开”保证只执行一次。
"""
import json
from hashlib import sha256
from langgraph.graph import END
from langgraph.types import Command

from backend.agent.contracts import ModelBusinessValidationError
from backend.agent.confirmation import MODEL_FORBIDDEN_ARGUMENTS, sanitize_model_tool_calls
from backend.agent.goal_completion import build_completion_spec, covered_requirements, evaluate_goal, record_facts, trusted_bindings
from backend.agent.goal_policy import active_goal, archive_current_plan, validate_plan_scope
from backend.agent.goal_scope import validate_business_scope as _known_args
from backend.agent.strategies.catalog import require_current_catalog
from backend.agent.strategies.contracts import DecisionKind
from backend.agent.strategies.factory import get_strategy
from backend.agent.strategies.tool_protocol import append_tool_result_messages, tool_message_group
from backend.agent.strategies.loop_policy import compute_progress, evaluate_loop
from backend.agent.streaming import emit_progress
from backend.agent.budget import reserve, settle


def _evaluation(state):
    """统一验收当前目标，返回完成结论和可信人物/关系绑定，不调用模型或数据库。

    record_facts 合并当前目标之前各轮及当前轮回执，防止新一轮覆盖旧的成功事实。
    trusted_bindings 从实际回执、已校验焦点及成功前置目标取得姓名 -> ID 等绑定，
    不把模型自行填入 args 的 ID 当作身份依据；同名多 ID 仍是歧义。
    build_completion_spec 定义用户目标需要哪些属性、关系或查询结果；evaluate_goal
    按这些条件核验回执。find_person 成功不等于“人物爱好查询”已经完成。
    """
    goal = active_goal(state)
    records = record_facts(goal, state)
    bindings = trusted_bindings(state, records)
    return evaluate_goal(build_completion_spec(goal, state.get("extracted_info")), records, bindings), bindings


def strategy_dispatch(state):
    """用请求状态中保存的模式选择入口，不在这里创建另一张图。

    新请求已冻结 strategy_mode / strategy_version / 工具目录摘要；重试、resume
    应使用原请求的值。版本或目录不兼容时拒绝静默迁移，避免旧计划按新协议执行。
    Plan 去批次规划节点，Discovery 去逐轮决策节点；Direct 不经过目标入口。
    """
    require_current_catalog(state)
    mode = state.get("strategy_mode", "plan")
    if mode not in {"plan", "discovery"}:
        raise ModelBusinessValidationError("GOAL_ROUTE_INVALID", "目标编排入口只用于 Plan / Discovery。")
    return Command(goto="plan_tasks" if mode == "plan" else "decide_next")


def _reject(state, exc, message=None):
    """模型协议/参数不合法时，给一次受预算约束的修复机会。

    例：find_person 应接收 query，模型却返回 name。应把字段错误反馈给模型，
    而不是让用户解释“query 是什么”。第一次去 decide_next，再次不合法则收尾。
    这是修复错误决策，不是重新执行已失败的业务工具；此处没有业务写入。
    """
    # protocol_repair_count 控制连续协议修复；合法业务决策或目录操作会将其清零。
    # decision_seq 是动作/修复标识序列，拒绝也会递增，不等于模型调用总次数。
    count = state.get("protocol_repair_count", 0) + 1
    seq = state.get("decision_seq", 0) + 1
    # 清空不合法待执行动作，保存机器可读反馈，并补齐 assistant/tool 失败协议组。
    # 校验阶段传入 message 时，替换当前动作的组；提议阶段异常没有显式 message，
    # 使用新序号组织错误反馈。上游异常有原始消息时从 exc.message 读取。
    update = {"protocol_repair_count": count, "decision_seq": seq,
              "pending_decision": {}, "replan_feedback": {"error_code": getattr(exc, "code", "INVALID_DECISION"),
                                                          "feedback": getattr(exc, "feedback", "修复工具调用协议")},
              "strategy_messages": append_tool_result_messages(state.get("strategy_messages", []),
                                    message or getattr(exc, "message", {}),
                                    {"success": False, "error_code": getattr(exc, "code", "INVALID_DECISION"),
                                     "message": getattr(exc, "feedback", "工具参数未通过校验，未执行")},
                                    decision_id=f"{state.get('active_goal_id')}:{state.get('decision_seq', 0) if message is not None else seq}")}
    if count > 1:
        # stop_reason 强制本目标失败；finalize_goal 仍保存已完成的部分操作。
        # 此处不调用生成回复模型，也不把错误升级成无穷的重新决策循环。
        return Command(update={**update, "stop_reason": "MODEL_PROTOCOL_INVALID", "execution_errors": ["模型工具决策修复后仍不合法"]}, goto="finalize_goal")
    # 每一次真正修复也受请求级重规划额度限制，普通下一步没有这个消耗。
    attempt = reserve("replan", f"protocol:{state['active_goal_id']}:{seq}")
    # 预算不足会由外层节点包装器转到 finish_budget，而不是继续付费调用模型。
    settle(attempt, result={"repair": count})
    return Command(update=update, goto="decide_next")


def decide_next(state):
    """先用事实判断，再按当前模式向模型索取一个建议，保存后交给校验节点。

    pending_decision 是待校验的模型建议，不是已授权的执行计划。
    拆成独立节点后，校验/执行阶段恢复可以读取该建议，而非重新请求模型造新动作。
    """
    evaluation, bindings = _evaluation(state)
    if evaluation.status == "SATISFIED":
        # 已有证据满足业务目标，省掉一次没有必要的模型调用。
        return Command(update={"goal_evaluation": evaluation.as_dict()}, goto="finalize_goal")
    spec = build_completion_spec(active_goal(state), state.get("extracted_info"))
    if spec.query_kind in {"person_detail", "relation_path", "kinship_title"} and not spec.target_name and not bindings.get("focus_id"):
        # 这是用户才能补充的业务信息：既无明确姓名，也无已验证会话焦点。
        # “他的爱好是什么”若无法解析“他”，直接澄清，不能让模型猜 person_id。
        return Command(update={"clarification_question": "你要查询哪位人物？请提供姓名。"}, goto="ask_clarification")
    emit_progress("strategy.deciding", message="根据当前结果选择下一步", goal_id=state.get("active_goal_id"))
    try:
        catalog = require_current_catalog(state)
        # 这里只运行 Discovery：注册搜索/描述/调用控制工具。
        # propose 内部调用模型并统一原始返回格式；此处不执行任何返回的函数。
        decision = get_strategy("discovery", state.get("strategy_version", "1")).propose(
            {**state, "goal_evaluation": evaluation.as_dict()}, catalog)
    except ModelBusinessValidationError as exc:
        # 格式错误、截断或多调用等可识别错误进入受限修复；未知系统异常不在这里
        # 一概吞掉，由外层审计、重试/恢复机制处理。
        return _reject(state, exc)
    # 正常建议发布后序号递增。下一节点读取同一 pending_decision 并验证，
    # 不依靠模型的“我已经完成”文案决定成功。
    return Command(update={"pending_decision": decision.as_dict(), "goal_evaluation": evaluation.as_dict(),
                           "decision_seq": state.get("decision_seq", 0) + 1}, goto="validate_decision")


def validate_decision(state):
    """把统一模型建议分流，并对业务调用做结构与语义两层校验。

    路由顺序：结束候选 -> 澄清 -> 目录操作 -> 业务工具。
    SDK strict / JSON Schema 只能约束结构，不能证明 ID、操作对象或属性符合用户要求。
    业务工具最终仍走 execute_tools，复用身份注入、执行前校验、确认及幂等机制。
    """
    decision = state.get("pending_decision") or {}
    try:
        catalog = require_current_catalog(state)
        kind = decision["kind"]
        if kind == DecisionKind.FINISH_CANDIDATE:
            # 无工具调用只表示模型“想结束”，不是执行成功证据。
            # 服务端完成合同满足才接受，否则最多给一次纠正过早结束的机会。
            evaluation, _ = _evaluation(state)
            if evaluation.status == "SATISFIED":
                return Command(update={"goal_evaluation": evaluation.as_dict()}, goto="finalize_goal")
            count = state.get("finish_repair_count", 0) + 1
            if count > 1:
                return Command(update={"stop_reason": "MODEL_STOPPED_EARLY", "execution_errors": ["模型停止时尚缺少目标完成证据"]}, goto="finalize_goal")
            # 明确告知缺失条件并保留本轮模型消息。finish_repair_count 与协议修复
            # 分开计数；这个分支没有单独 reserve(replan)，后续模型调用仍受总预算限制。
            return Command(update={"finish_repair_count": count,
                                   "replan_feedback": {"error_code": "GOAL_INCOMPLETE", "missing": evaluation.missing_requirements},
                                   "strategy_messages": [*state.get("strategy_messages", []), *tool_message_group(decision.get("message", {}), {})]}, goto="decide_next")
        if kind == DecisionKind.ASK_CLARIFICATION:
            # 正在修工具协议时禁止将内部问题转嫁给用户；合法业务问题才可中断。
            if state.get("protocol_repair_count", 0):
                raise ModelBusinessValidationError("INTERNAL_ERROR_IS_NOT_CLARIFICATION", "上一轮是工具参数/协议错误，不能让用户提供内部参数；应按工具 Schema 修复。")
            question = decision["arguments"]["question"]
            return Command(update={"clarification_question": question}, goto="ask_clarification")
        if kind in {DecisionKind.SEARCH, DecisionKind.DESCRIBE}:
            # 这里只路由。目录函数会进一步检查参数与目录范围，不走业务写执行器。
            return Command(goto="execute_discovery")
        calls = decision.get("calls", [])
        # 即使统一动作名叫 EXECUTE_BATCH，此链路也只接受一个当前目录内的工具。
        # Discovery 的 tool_call(id,args) 已在适配器解包成真实工具名与业务参数，
        # 所以下面的检查和 Direct 完全相同，不另开一套绕过校验的执行入口。
        if len(calls) != 1 or calls[0]["tool"] not in catalog.tools:
            raise ModelBusinessValidationError("TOOL_SCOPE_VIOLATION", "只允许当前目标展示的一个业务工具。")
        if any(key in MODEL_FORBIDDEN_ARGUMENTS for key in calls[0]["args"]):
            # owner_id、confirmed、业务幂等参数等必须由服务端掌握。
            # 即使模型知道当前登录用户 ID，也没有填写这些可信参数的权限。
            raise ModelBusinessValidationError("TRUSTED_ARGUMENT_FORGED", "不能设置服务端身份、确认或幂等参数。")
        # SDK strict 的 nullable 可选项先恢复为“未提供”，再走现有参数模型默认值。
        raw_calls = [{**c, "args": {k: v for k, v in c["args"].items() if v is not None}} for c in calls]
        # sanitize 检查参数定义、未知工具、依赖格式等，并生成服务端稳定 call_id。
        # request_id / goal_id / plan_revision / step_id 共同区分请求、目标与步骤，
        # 同一步骤恢复保留身份；provider call ID 不用于业务幂等。
        normalized = sanitize_model_tool_calls(raw_calls, state["request_id"],
                                              plan_version=state.get("plan_revision", 0), goal_id=state["active_goal_id"])
        # 先诊断参数名/类型，再检查业务身份。否则 find_person(name=...) 会被
        # 误报“姓名不匹配”，模型甚至要求用户解释关系来修复内部的 query 参数。
        _, bindings = _evaluation(state)
        # 参数模型会填默认值；做“用户要求了哪些属性”校验时，只取模型实际提供
        # 且非 None 的字段，避免把默认 hobbies=[] 等误认成用户要求清空属性。
        # 真正执行仍使用 normalized 全量参数，执行器还有自己的检查。
        mentioned = [{**c, "args": {k: v for k, v in c["args"].items() if k in raw_calls[i]["args"]}}
                     for i, c in enumerate(normalized)]
        _known_args(state, mentioned, bindings)
        # _known_args 实际是 validate_business_scope 的别名：检查人物 ID 来源、
        # 目标人物是否一致、新增前是否精确查过 NOT_FOUND、关系方向和类型等。
        # 这属于业务语义校验，数据库 owner 隔离仍须由工具层落实。
        validate_plan_scope(state, normalized)
        # validate_plan_scope 再检查工具是否适用于当前目标，及已完成写操作等边界。
        # 进入新轮前，先将旧轮工具计划/回执归档到当前目标，再清空本轮工作区。
        # 清空 tool_results 不会删除归档事实；_evaluation 下次会合并读取。
        return Command(update={"tool_calls": normalized, "tool_results": [], "execution_errors": [],
                               "next_tool_index": 0, "goals": archive_current_plan(state),
                               "protocol_repair_count": 0, "pending_confirmation": None, "needs_confirmation": False}, goto="execute_tools")
    except ModelBusinessValidationError as exc:
        # 业务范围拒绝也提供安全反馈；不直接执行、不自动降级成更宽松的工具。
        return _reject(state, exc, decision.get("message"))
    except (ValueError, KeyError, TypeError) as exc:
        # 保留参数模型提供的字段级安全反馈，让模型知道要修复哪个参数。
        feedback = getattr(exc, "feedback", "参数未通过定义校验，不能执行。")
        if state.get("strategy_mode") == "discovery":
            # Discovery 参数没掌握时应查看 Schema，不能要求用户补“内部参数名”。
            feedback += " 参数 Schema 未知时先调用 tool_describe 查看，不能要求用户提供内部参数。"
        return _reject(state, ModelBusinessValidationError(getattr(exc, "code", "INVALID_DECISION"), feedback), decision.get("message"))


def execute_discovery(state):
    """执行工具目录的搜索或描述，结果只提供能力信息，不证明业务目标已完成。

    tool_search 返回候选和参数概要；tool_describe 返回指定工具定义，过大时可能
    截断。只搜索当前目标允许目录，不动态导入未知工具，也不执行 tool_call。
    这里是本地目录操作，不需要模型调用，但仍计入请求的 discovery 次数预算。
    """
    from backend.agent.strategies.tool_search import search_tools, describe_tool
    from backend.agent.audit import audit_tool, record_tool_result
    decision = state["pending_decision"]
    seq = state["decision_seq"]
    name = "tool_search" if decision["kind"] == DecisionKind.SEARCH else "tool_describe"
    catalog = require_current_catalog(state)
    # 目标 + 决策序号是该目录动作的预算标识；恢复时可复用已结算的结果。
    attempt = reserve("discovery", f"{state['active_goal_id']}:{seq}")
    try:
        if attempt and attempt["cached"]:
            result = attempt["result"]
        else:
            # 审计记录目录操作的输入、输出；discovery_序号是这里的审计标识，
            # 不替代公共业务执行器生成的 call_id 或业务幂等记录。
            emit_progress("tool.searching" if name == "tool_search" else "tool.describing", message="搜索可用工具" if name == "tool_search" else "查看工具参数")
            with audit_tool(name, decision["arguments"], call_id=f"discovery_{seq}", step_id=f"decision_{seq}", attempt=1) as span:
                result = search_tools(catalog, **decision["arguments"]) if name == "tool_search" else describe_tool(catalog, decision["arguments"]["id"])
                record_tool_result(span, result)
            settle(attempt, result=result)
    except ModelBusinessValidationError as exc:
        # 参数/目录范围错误已经发生，结算尝试再走协议修复，不询问用户工具内部信息。
        settle(attempt)
        return _reject(state, exc, decision.get("message"))
    except (KeyError, TypeError, ValueError):
        settle(attempt)
        return _reject(state, ModelBusinessValidationError("DISCOVERY_ARGUMENT_INVALID", "发现操作的参数不合法，未执行业务。"), decision.get("message"))
    schemas = list(state.get("loaded_tool_schemas", []))
    if name == "tool_describe" and result.get("schema_complete"):
        # 只有拿到完整 Schema 才记录“已加载”。工具 ID + Schema 摘要用于判断
        # 是否首次获得定义，也避免重复查看相同定义被算作新进展；不是新的权限。
        token = f"{result['id']}:{result['schema_hash']}"
        if token not in schemas:
            schemas.append(token)
    # 先保存目录回执，再去独立 observe_strategy 节点回填协议消息、检查循环。
    return Command(update={"discovery_result": result, "loaded_tool_schemas": schemas,
                           "protocol_repair_count": 0}, goto="observe_strategy")


def observe_strategy_execution(state):
    """逐轮观察的核心：回填结果 -> 比较业务进展 -> 选择确定性路由。

    两个入口共用此函数：目录操作后的 observe_strategy 图节点，以及业务操作后
    observe_execution 的函数委托。Plan 的批次观察走 observe_execution 的另一分支。
    本函数不调用模型，CONTINUE 才去 decide_next 提出下一轮动作。

    请按下面五个步骤阅读；是否继续由 evaluate_loop 的固定优先级判断，
    不是解析模型自然语言“我认为成功了”，也不是所有错误都自动重试。
    """
    # 第 1 步：合并实际证据验收目标，并获取可信绑定；不只看最新一个工具成功。
    evaluation, bindings = _evaluation(state)
    decision = state.get("pending_decision") or {}
    # 第 2 步：为本轮 assistant 调用构造配对结果。目录动作反馈 Schema/候选，
    # 业务动作反馈回执和完成条件，使下一轮模型能用真实人物 ID 决定动作。
    payload = state.get("discovery_result") if decision.get("kind") in {DecisionKind.SEARCH, DecisionKind.DESCRIBE} else {
        "records": state.get("tool_results", []), "goal_evaluation": evaluation.as_dict()}
    messages = append_tool_result_messages(state.get("strategy_messages", []), decision.get("message", {}), payload,
                                          decision_id=f"{state.get('active_goal_id')}:{state.get('decision_seq', 0)}")
    from backend.agent.contracts import PersonExtraction
    # 第 3 步：建立“是否有进展”的快照，避免模型不断重复查同一个人或目录。
    # 相同 ID 的实际属性变化也是新证据；只哈希业务字段，排除调用 ID、耗时等
    # 每次都会变化的噪声。否则重复缓存会被误判成进展，或合法重读被永久禁止。
    versions = []
    for person_id, person in bindings.get("person_data", {}).items():
        # 查询投影可能返回 null，详情投影可能省略未存字段；统一形状，避免两种
        # 等价结果交替出现时不断重置“无进展”计数。
        fields = {key: person.get(key) for key in PersonExtraction.model_fields}
        # SHA-256 在这里是字段快照摘要，不是记录修改时间，也不是 Token 长度。
        # 同一人物 ID 的爱好/职业实际变化会形成新版本；相同内容返回不形成新版本。
        serialized = json.dumps(fields, sort_keys=True, ensure_ascii=False)
        version = sha256(serialized.encode()).hexdigest()
        versions.append(f"{person_id}:{version}")
    # 五类进展：新姓名-ID 绑定、人物属性版本、新工具完整定义、已覆盖的目标属性、
    # 完成合同引用的证据。成功调用次数本身不算进展；coverage 只是部分完成指标，
    # 不能代替 evaluate_goal 的完整验收，也不能给后续操作授予权限。
    snapshot = {"bindings": sorted(f"{name}:{pid}" for name, ids in bindings["people"].items() for pid in ids),
                "entity_versions": sorted(versions),
                "schemas": state.get("loaded_tool_schemas", []),
                "coverage": covered_requirements(build_completion_spec(active_goal(state), state.get("extracted_info")), bindings),
                "evidence": list(evaluation.evidence_call_ids)}
    progress = compute_progress(state.get("progress_snapshot", {}), snapshot)
    # 当前快照相较上一轮出现任一类新标记就清零，否则连续无进展次数 +1。
    # 这是“相邻快照”的启发式保护，不是证明推理正确；另外还有总调用/Token 预算。
    # 搜索目录本身不一定增加这些标记；首次获得完整 Schema 才明确计入 schemas。
    rounds = 0 if progress["progressed"] else state.get("no_progress_rounds", 0) + 1
    update = {"strategy_messages": messages, "progress_snapshot": snapshot, "no_progress_rounds": rounds,
              "goal_evaluation": evaluation.as_dict(), "discovery_result": None}
    # 第 4 步：用合入本轮 update 后的状态判断，确保无进展计数包含这一轮。
    # evaluate_loop 的优先级（先匹配先返回）：
    # 1. 当前失败/跳过回执含 PERMISSION_DENIED / INTERNAL / UNKNOWN -> FAIL；
    # 2. 含 IN_PROGRESS / TRANSIENT -> WAIT；
    # 3. 含   / CONFLICT，或目标验收为人物歧义 -> ASK；
    # 4. 目标 SATISFIED -> SUCCESS；其他 BLOCKED -> FAIL；
    # 5. 连续无进展达到配置上限 -> FAIL；其余 -> CONTINUE。
    # 混合错误按这个优先级处理，不能用一条成功回执覆盖本轮执行不确定性。
    outcome = evaluate_loop({**state, **update}, evaluation)
    emit_progress("goal.evaluated", goal_id=state.get("active_goal_id"), message="检查当前目标完成条件")
    if outcome.action == "SUCCESS":
        # 第 5 步：只收尾当前目标，后续由 select_next_goal 调度其他目标。
        return Command(update=update, goto="finalize_goal")
    if outcome.action == "ASK":
        # ask_clarification 负责 interrupt、候选选择和恢复；这里不自行中断。
        return Command(update=update, goto="ask_clarification")
    if outcome.action == "WAIT":
        # 不能回填 IN_PROGRESS 后立即让模型另造 call_id。恢复仍执行当前步骤。
        # 本分支丢弃刚构造的协议组，保留原 strategy_messages 和待执行动作。
        # goto END 结束本轮；稍后用原 request_id 走恢复/幂等链路，不重新决策。
        return Command(update={**update, "strategy_messages": state.get("strategy_messages", []),
                               "execution_decision": "WAIT",
                               "response": "任务仍在执行或暂时不可用，请稍后使用原请求重试。"}, goto=END)
    if outcome.action == "FAIL":
        # 记录停止原因；finalize_goal 会保留成功写入等部分结果，不能向用户声称
        # “什么都没发生”。失败不会抹掉审计或已经产生的业务副作用。
        emit_progress("loop.stopped", message="停止继续调用，保留已完成结果", reason=outcome.reason)
        return Command(update={**update, "stop_reason": outcome.reason, "execution_errors": [outcome.reason]}, goto="finalize_goal")
    # 明确的参数失败修复才扣重规划额度。NOT_FOUND→新增是正常业务分支。
    errors = {r.get("error_code") for r in state.get("tool_results", []) if r.get("status") == "FAILED"}
    if errors & {"INVALID_ARGUMENT", "DEPENDENCY_FAILED"}:
        # 只有循环策略允许 CONTINUE 后才走到这里；使用原目标 + 动作序号扣修复额度。
        # NOT_FOUND 表示明确查不到人，若目标允许创建，下一步新增是正常业务推进；
        # 查询目标则可能已经完成负向查询，不能自动为了消除 NOT_FOUND 去新增人物。
        attempt = reserve("replan", f"tool-repair:{state['active_goal_id']}:{state['decision_seq']}")
        settle(attempt, result={"repair": "tool_arguments"})
    # 普通继续不重置动作序号/预算，不把“模型再想一步”误计作失败重规划。
    # 清空已消费的 pending_decision 后去 decide_next；前面的 messages 已提供实际结果。
    # 即使 CONTINUE，下一轮模型建议仍必须经过 validate_decision，不能直接调用工具。
    return Command(update={**update, "pending_decision": {}}, goto="decide_next")
