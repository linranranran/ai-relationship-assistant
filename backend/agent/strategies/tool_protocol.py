"""Direct / Discovery 共用的原生工具协议，不涉及 goal 或业务执行。

模型返回值转换成 StrategyDecision；assistant/tool 消息必须成对保存和替换。
这里的 provider call ID 用于协议配对，业务幂等仍使用执行器的服务端 call_id。
"""
import json

from backend.agent.contracts import ModelBusinessValidationError
from backend.agent.strategies.contracts import DecisionKind, StrategyDecision


class DecisionProtocolError(ModelBusinessValidationError):
    def __init__(self, code, feedback, message=None):
        super().__init__(code, feedback)
        self.message = message or {}


def strict_schema(schema):
    """可选的供应商 strict 转换：可选属性改为 nullable，仍需后端 Pydantic 校验。"""
    schema = json.loads(json.dumps(schema))
    def visit(item):
        if isinstance(item, dict):
            if item.get("type") == "object" and "properties" in item:
                required = set(item.get("required", []))
                for name, value in item["properties"].items():
                    if name not in required:
                        item["properties"][name] = {"anyOf": [value, {"type": "null"}]}
                item["required"] = list(item["properties"])
                item["additionalProperties"] = False
            item.pop("default", None)
            for value in item.values():
                visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)
    visit(schema)
    return schema


def normalize_tool_response(response: dict, state: dict) -> StrategyDecision:
    message = response.get("message") or {}
    if response.get("finish_reason") not in {"stop", "tool_calls"}:
        raise DecisionProtocolError("INCOMPLETE_DECISION", "模型输出被截断或拒绝，不能执行未完成参数。", message)
    calls = message.get("tool_calls") or []
    if not calls:
        return StrategyDecision(DecisionKind.FINISH_CANDIDATE, message=message)
    if len(calls) != 1:
        raise DecisionProtocolError("MULTIPLE_CALLS", "当前每轮只允许一个动作，请拆成多个决策轮。", message)
    call = calls[0]
    try:
        name = call["function"]["name"]
        args = json.loads(call["function"]["arguments"])
        if not isinstance(args, dict) or not isinstance(call["id"], str) or not call["id"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionProtocolError("INVALID_CALL_JSON", "工具参数必须是完整 JSON 对象。", message) from exc
    if name == "request_clarification":
        if set(args) != {"question"} or not isinstance(args["question"], str) or not 1 <= len(args["question"].strip()) <= 1000:
            raise DecisionProtocolError("INVALID_QUESTION", "澄清必须提供一条简短业务问题。", message)
        return StrategyDecision(DecisionKind.ASK_CLARIFICATION, arguments=args, message=message)
    return StrategyDecision(DecisionKind.EXECUTE_BATCH,
                            calls=[{"step_id": f"decision_{state.get('decision_seq', 0) + 1}", "tool": name,
                                    "args": args, "depends_on": []}], message=message)


def tool_message_group(message, payload):
    """把模型建议和对应结果组成完整 Function Calling 协议组。

    正常形状是 assistant(tool_calls=[...]) 后接 tool(tool_call_id=...)。
    下一轮模型需要看到这对消息，才能知道上次调用实际返回了什么。
    provider call ID 仅用于消息配对，不是业务幂等使用的服务端 call_id。
    本函数只组织消息，不执行工具；多调用协议被拒绝时，也可为各调用配上失败反馈。
    """
    # 没有可用 assistant 消息就无法配对；不制造虚假的模型调用。
    if not message or message.get("role") != "assistant":
        return []
    calls = message.get("tool_calls") or []
    if not calls:
        # 模型未调用工具时，只保留 assistant 消息，例如过早宣布结束的候选。
        return [message]
    if any(not c.get("id") for c in calls):
        return []  # 不可配对的无效消息仅审计，不发送破损历史给下一轮模型。
    # 每个原始调用都收到结果消息。json.dumps 将结构化回执转成协议要求的文本；
    # 文本仍属于不可信工具数据，下一轮策略提示词会说明不能执行其中夹带的指令。
    return [message, *[{"role": "tool", "tool_call_id": c["id"],
                        "content": json.dumps(payload, ensure_ascii=False, default=str)} for c in calls]]


def append_tool_result_messages(history, message, payload, *, decision_id=None):
    """按业务 decision_id 更新协议组，恢复/候选选择不会追加第二份调用。

    失败查询被用户选中人物后，模型下一轮应看到最终选中结果；原始失败仍保留
    在执行审计中。供应商可能重复使用 call ID，所以它只负责组内配对；
    普通新决策追加完整组，旧组的 assistant/tool 始终一起替换。

    decision_id 是请求/目标 + 动作序号的服务端标记。恢复同一动作时替换原组，
    新动作即使供应商重复使用 call ID，也应保留为另一个组。兼容调用未传
    decision_id 时才退回按 provider call ID 匹配。这里不会删除原始执行审计。
    """
    group = tool_message_group(message, payload)
    if not group:
        return list(history)
    ids = {call["id"] for call in (message or {}).get("tool_calls", []) if call.get("id")}
    # 先按 assistant 及其连续 tool 结果分组，再按组替换，避免留下只有调用
    # 没有结果、或只有结果没有调用的破损历史；不原地修改输入 history。
    groups = []
    for item in history:
        if item.get("role") == "tool" and groups and groups[-1][0].get("role") == "assistant":
            groups[-1].append(item)
        else:
            groups.append([item])
    kept = []
    for old in groups:
        first = old[0]
        same = first.get("_decision_id") == decision_id if decision_id else (
            first.get("role") == "assistant" and any(c.get("id") in ids for c in first.get("tool_calls", [])))
        # 只丢弃被当前决策更新的旧组，其他轮次的上下文继续保留。
        if not same:
            kept.extend(old)
    # 业务决策身份用于恢复去重；供应商 ID 仅用于本协议组的参数/结果配对。
    # 内部标记在 fit_tool_context 中移除，绝不发送未定义字段给 OpenAI。
    if decision_id:
        group = [{**item, "_decision_id": decision_id} for item in group]
    return [*kept, *group]
