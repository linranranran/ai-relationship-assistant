# ============================================================
# LLM Service — 统一的 LLM 调用封装
# ============================================================

import logging

from backend.agent.memory.ports import ConservativeTokenCounter
from backend.config import (
    AGENT_CONTEXT_SAFETY_TOKENS,
    AGENT_MAX_INPUT_TOKENS,
    AGENT_RESERVED_OUTPUT_TOKENS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
)
from langfuse.openai import OpenAI


logger = logging.getLogger(__name__)
_fallback_counter = ConservativeTokenCounter()

def get_llm_client() -> OpenAI:
    """获取 LLM 客户端"""
    return OpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
    )


def chat(
    client: OpenAI,
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.3,
    response_format: dict | None = None,
) -> str:
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

    response = client.chat.completions.create(**kwargs)
    usage = getattr(response, "usage", None)
    if usage is not None:
        # 只记录数字，不记录包含客户原文的 Prompt。
        logger.info(
            "LLM usage: model=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s "
            "estimated_prompt_tokens=%s",
            LLM_MODEL,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(usage, "total_tokens", None),
            final_estimate,
        )
    return response.choices[0].message.content
