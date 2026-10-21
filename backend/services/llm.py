# ============================================================
# LLM Service — 统一的 LLM 调用封装
# ============================================================

import logging
from backend.agent.tracing import trace_event
from time import perf_counter
from backend.agent.budget import reserve, settle, model_operation, current_budget

from httpx import Timeout

from backend.agent.memory.ports import ConservativeTokenCounter
from backend.config import (
    AGENT_CONTEXT_SAFETY_TOKENS,
    AGENT_MAX_INPUT_TOKENS,
    AGENT_RESERVED_OUTPUT_TOKENS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_ENABLE_THINKING,
    LLM_REQUEST_TIMEOUT_SECONDS,
)
from openai import OpenAI
from backend.agent.audit import audit_model, record_model_result


logger = logging.getLogger(__name__)
_fallback_counter = ConservativeTokenCounter()

def get_llm_client() -> OpenAI:
    """只创建普通 SDK 客户端，审计放在明确的业务调用边界。

    Langfuse OpenAI 包装器会全局修改 OpenAI 的方法，影响 ChatOpenAI 的调用。
    所以结构化输出交给官方图回调，原生 chat/stream 使用官方 Generation API；
    同一次调用不会重复记录，关闭审计后也不会遗留全局自动采集。
    """
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        # 只延长单次模型调用的读/写等待；连接和连接池等待仍保留短超时。
        timeout=Timeout(
            connect=5.0,
            read=_call_timeout(),
            write=_call_timeout(),
            pool=10.0,
        ),
        max_retries=0,  # 重试必须在 Agent 预算层可见，不能由 SDK 隐藏消耗。
    )


def _call_timeout():
    scope = current_budget()
    if scope is None:
        return LLM_REQUEST_TIMEOUT_SECONDS
    snapshot = scope["store"].snapshot()
    remaining = snapshot["limits"]["active_seconds"]-snapshot["active_seconds"]
    return max(0.1,min(60.0,LLM_REQUEST_TIMEOUT_SECONDS,remaining))


def _request_kwargs(system_prompt, messages, temperature, response_format=None, *, extra_input_tokens=0):
    """发送已经过 Context Builder 裁剪的消息。

    本层只负责模型协议，不负责加载记忆或决定哪些历史可以发送。调用方必须先经过
    ``prepare_context``。空 system prompt 不再生成无意义消息。

    供应商返回的 prompt/completion usage 会以纯数字写入日志并与本地估算并列；
    禁止记录完整 prompt 和客户原文。
    """
    full_messages = list(messages)
    if system_prompt:
        full_messages.insert(0, {"role": "system", "content": system_prompt})

    # Context Builder 预算的是共享上下文；每个节点随后还会增加自己的 system/user
    # Prompt。发送前再检查最终载荷，避免这些新增内容绕过预算。这里使用与骨架一致
    # 的保守估算器；供应商返回的真实 usage 会在调用后用于校准。
    final_estimate = _fallback_counter.count_messages(full_messages) + extra_input_tokens
    final_input_budget = (
        AGENT_MAX_INPUT_TOKENS
        - AGENT_RESERVED_OUTPUT_TOKENS
        - AGENT_CONTEXT_SAFETY_TOKENS
    )
    if final_estimate > final_input_budget:
        raise ValueError(
            f"最终模型输入超过应用预算: estimated={final_estimate}, "
            f"budget={final_input_budget}"
        )

    kwargs = {
        "model": LLM_MODEL,
        "messages": full_messages,
        "temperature": temperature,
        # 与 Context Builder 的输出预留保持一致，避免只在预算中“假装预留”。
        "max_tokens": AGENT_RESERVED_OUTPUT_TOKENS,
    }
    if response_format:
        kwargs["response_format"] = response_format
    if LLM_ENABLE_THINKING is not None:
        # OpenAI SDK 不定义 enable_thinking；不能把它塞入 messages 或伪装成
        # 标准参数。供应商扩展统一在这里传递，普通文本、tools 和流式均复用。
        kwargs["extra_body"] = {"enable_thinking": LLM_ENABLE_THINKING}

    return kwargs, final_estimate


def chat(client: OpenAI, system_prompt: str, messages: list[dict],
         temperature: float = 0.3, response_format: dict | None = None) -> str:
    """结构化规划等内部节点继续非流式调用，不将内部 JSON 暴露给浏览器。"""
    kwargs, final_estimate = _request_kwargs(system_prompt, messages, temperature, response_format)
    attempt = reserve("model",model_operation(kwargs["messages"]),final_estimate+kwargs["max_tokens"])
    if attempt and attempt["cached"]:
        trace_event("model.cached", mode="text", model=LLM_MODEL)
        return attempt["result"]["text"]
    started = perf_counter()
    try:
        with audit_model(kwargs, mode="text") as generation:
            response = client.chat.completions.create(**kwargs)
            record_model_result(generation, response.choices[0].message.content,
                                getattr(response, "usage", None))
    except Exception:
        settle(attempt)
        raise
    usage = getattr(response, "usage", None)
    if usage is not None:
        # 只记录数字，不记录包含客户原文的 Prompt。
        trace_event("model.usage", model=LLM_MODEL,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                    estimated_prompt_tokens=final_estimate,
                    latency_ms=int((perf_counter() - started) * 1000))
    text = response.choices[0].message.content
    settle(attempt,getattr(usage,"total_tokens",None),{"text":text})
    return text


def stream_chat(client: OpenAI, system_prompt: str, messages: list[dict], temperature=0.3):
    """供应商原生流，不把完整答案切成字符来模拟流式。

    与普通调用共用最终 Token 检查。只消费 delta.content，reasoning_content 和
    tool_calls 都不会作为用户回复输出。调用方负责聚合增量、保存完整答案。
    """
    from backend.agent.streaming import check_execution
    kwargs, estimate = _request_kwargs(system_prompt, messages, temperature)
    attempt = reserve("model",model_operation(kwargs["messages"],"stream"),estimate+kwargs["max_tokens"])
    if attempt and attempt["cached"]:
        trace_event("model.cached", mode="stream", model=LLM_MODEL)
        yield attempt["result"]["text"]
        return
    started = perf_counter()
    first_token = True
    finish_reason = None
    pieces, total_tokens = [], None
    usage = None
    with audit_model(kwargs, mode="stream") as generation:
        try:
            with client.chat.completions.create(**kwargs, stream=True, stream_options={"include_usage":True}) as stream:
                for chunk in stream:
                    check_execution()
                    if getattr(chunk, "usage", None) is not None:
                        usage = chunk.usage
                        total_tokens = getattr(usage,"total_tokens",None)
                    if not chunk.choices:
                        continue
                    if chunk.choices[0].finish_reason is not None:
                        finish_reason = chunk.choices[0].finish_reason
                    content = chunk.choices[0].delta.content
                    if content:
                        pieces.append(content)
                        if first_token:
                            trace_event("model.first_token", model=LLM_MODEL,
                                        latency_ms=int((perf_counter() - started) * 1000))
                            first_token = False
                        yield content
        finally:
            # 只在结束/异常时聚合一次，不能每个 token 创建观察或 flush。
            # 失败留下的部分文本标记为未完整生成，不能误当成成功回复。
            record_model_result(generation, "".join(pieces), usage,
                                incomplete=finish_reason != "stop")
    # SDK 在异常 EOF 时可能自然结束迭代。未收到 stop 的部分文本只是草稿，
    # 不能写为 COMPLETED / conversation_message，否则重试会永远复用截断答案。
    if finish_reason != "stop":
        settle(attempt,total_tokens)
        trace_event("model.stream.incomplete", model=LLM_MODEL, finish_reason=finish_reason)
        raise RuntimeError("模型回复未完整生成，请使用原请求重试")
    settle(attempt,total_tokens,{"text":"".join(pieces)})
    trace_event("model.stream.finished", model=LLM_MODEL, estimated_prompt_tokens=estimate,
                latency_ms=int((perf_counter() - started) * 1000))
