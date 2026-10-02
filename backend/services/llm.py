# ============================================================
# LLM Service — 统一的 LLM 调用封装
# ============================================================

import logging
from backend.agent.tracing import trace_event
from time import perf_counter

from httpx import Timeout

from backend.agent.memory.ports import ConservativeTokenCounter
from backend.config import (
    AGENT_CONTEXT_SAFETY_TOKENS,
    AGENT_MAX_INPUT_TOKENS,
    AGENT_RESERVED_OUTPUT_TOKENS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_REQUEST_TIMEOUT_SECONDS,
)
from langfuse.openai import OpenAI


logger = logging.getLogger(__name__)
_fallback_counter = ConservativeTokenCounter()

def get_llm_client() -> OpenAI:
    """获取 LLM 客户端"""
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        # 只延长单次模型调用的读/写等待；连接和连接池等待仍保留短超时。
        timeout=Timeout(
            connect=5.0,
            read=LLM_REQUEST_TIMEOUT_SECONDS,
            write=LLM_REQUEST_TIMEOUT_SECONDS,
            pool=10.0,
        ),
    )


def _request_kwargs(system_prompt, messages, temperature, response_format=None):
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
    final_estimate = _fallback_counter.count_messages(full_messages)
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

    return kwargs, final_estimate


def chat(client: OpenAI, system_prompt: str, messages: list[dict],
         temperature: float = 0.3, response_format: dict | None = None) -> str:
    """结构化规划等内部节点继续非流式调用，不将内部 JSON 暴露给浏览器。"""
    kwargs, final_estimate = _request_kwargs(system_prompt, messages, temperature, response_format)
    started = perf_counter()
    response = client.chat.completions.create(**kwargs)
    usage = getattr(response, "usage", None)
    if usage is not None:
        # 只记录数字，不记录包含客户原文的 Prompt。
        trace_event("model.usage", model=LLM_MODEL,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                    estimated_prompt_tokens=final_estimate,
                    latency_ms=int((perf_counter() - started) * 1000))
    return response.choices[0].message.content


def stream_chat(client: OpenAI, system_prompt: str, messages: list[dict], temperature=0.3):
    """供应商原生流，不把完整答案切成字符来模拟流式。

    与普通调用共用最终 Token 检查。只消费 delta.content，reasoning_content 和
    tool_calls 都不会作为用户回复输出。调用方负责聚合增量、保存完整答案。
    """
    from backend.agent.streaming import check_execution
    kwargs, estimate = _request_kwargs(system_prompt, messages, temperature)
    started = perf_counter()
    first_token = True
    finish_reason = None
    with client.chat.completions.create(**kwargs, stream=True) as stream:
        for chunk in stream:
            check_execution()
            if not chunk.choices:
                continue
            if chunk.choices[0].finish_reason is not None:
                finish_reason = chunk.choices[0].finish_reason
            content = chunk.choices[0].delta.content
            if content:
                if first_token:
                    trace_event("model.first_token", model=LLM_MODEL,
                                latency_ms=int((perf_counter() - started) * 1000))
                    first_token = False
                yield content
    # SDK 在异常 EOF 时可能自然结束迭代。未收到 stop 的部分文本只是草稿，
    # 不能写为 COMPLETED / conversation_message，否则重试会永远复用截断答案。
    if finish_reason != "stop":
        trace_event("model.stream.incomplete", model=LLM_MODEL, finish_reason=finish_reason)
        raise RuntimeError("模型回复未完整生成，请使用原请求重试")
    trace_event("model.stream.finished", model=LLM_MODEL, estimated_prompt_tokens=estimate,
                latency_ms=int((perf_counter() - started) * 1000))
