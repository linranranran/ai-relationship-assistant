"""原生 OpenAI 工具决策；这里只发请求，不执行任何模型提出的工具。

Schema、工具消息和参数均计入输入预算。SDK 隐藏重试关闭，实际调用使用
原有 PG 预占/结算与 Langfuse generation，缓存重放仍须由策略重新校验。
"""
from hashlib import sha256
import json
from typing import Any

from backend.agent.audit import audit_model, record_model_result
from backend.agent.budget import model_operation, reserve, settle
from backend.services.llm import _fallback_counter, _request_kwargs, get_llm_client
from backend.config import AGENT_MAX_INPUT_TOKENS, AGENT_RESERVED_OUTPUT_TOKENS, AGENT_CONTEXT_SAFETY_TOKENS
from backend.agent.budget import BudgetExceeded

ModelToolResponse = dict[str, Any]


def fit_tool_context(prompt: str, context: list[dict], task_messages: list[dict], history: list[dict], tools: list[dict]) -> list[dict]:
    """只裁掉完整旧工具协议组；最后一次结果和当前目标、可信绑定永远保留。

    协议历史只是模型上下文；完整事实仍保留在图状态/审计/回执中。不能删掉
    assistant 调用却留下 tool 消息，或删掉最近结果造成模型重复写入。
    """
    groups = []
    for message in history:
        if message.get("role") == "assistant" or not groups:
            groups.append([])
        groups[-1].append(message)
    base = list(context)
    limit = AGENT_MAX_INPUT_TOKENS - AGENT_RESERVED_OUTPUT_TOKENS - AGENT_CONTEXT_SAFETY_TOKENS
    while True:
        history_messages = [{k: v for k, v in item.items() if k in {"role", "content", "tool_calls", "tool_call_id"}}
                            for group in groups for item in group]
        messages = [{"role": "system", "content": prompt}, *base, *task_messages, *history_messages]
        extra = json.dumps({"tools": tools, "calls": [m.get("tool_calls") for m in messages if m.get("tool_calls")]}, ensure_ascii=False)
        estimate = _fallback_counter.count_messages(messages) + _fallback_counter.count_messages([{"role": "system", "content": extra}])
        if estimate <= limit:
            return messages
        if base:
            # 摘要/事实是一条，普通旧问答按用户+助手整体删除，保留协议完整性。
            base.pop(0)
            if base and base[0].get("role") == "assistant":
                base.pop(0)
        elif len(groups) > 1:
            groups.pop(0)
        else:
            raise BudgetExceeded("当前目标与最近工具证据超过输入预算，停止继续调用模型")


def invoke_tool_decision(messages: list[dict], tools: list[dict], operation_key: str) -> ModelToolResponse:
    # 原计数器主要计 content；tool_calls 中的 JSON 也必须计入，不能成为预算盲区。
    extra = json.dumps({"tools": tools, "calls": [m.get("tool_calls") for m in messages if m.get("tool_calls")]}, ensure_ascii=False)
    schema_tokens = _fallback_counter.count_messages([{"role": "system", "content": extra}])
    kwargs, estimate = _request_kwargs("", messages, 0, extra_input_tokens=schema_tokens)
    kwargs.update(tools=tools, parallel_tool_calls=False, tool_choice="auto")
    digest = sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    attempt = reserve("model", model_operation(messages, f"native:{operation_key}:{digest}"), estimate + kwargs["max_tokens"])
    if attempt and attempt["cached"]:
        return attempt["result"]  # 传输层缓存不是执行授权，调用方仍做范围和参数校验。
    try:
        with get_llm_client() as client, audit_model(kwargs, mode="tool_decision") as generation:
            response = client.chat.completions.create(**kwargs)
            message = response.choices[0].message.model_dump(exclude_none=True)
            # 不保存/回填供应商的隐藏推理字段；只保留标准工具调用协议。
            message = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
            result = {"message": message, "finish_reason": response.choices[0].finish_reason}
            record_model_result(generation, result, getattr(response, "usage", None))
            settle(attempt, getattr(getattr(response, "usage", None), "total_tokens", None), result)
            return result
    except Exception:
        settle(attempt)  # 未知消耗保守记账，不返还未知费用。
        raise
