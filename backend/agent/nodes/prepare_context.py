"""在意图识别前准备受 Token 预算约束的三层记忆上下文。"""

from dataclasses import asdict

from backend.agent.memory.context_builder import build_context_bundle
from backend.agent.memory.runtime import get_memory_runtime
from backend.agent.memory.token_budget import compact_tool_results
from backend.agent.state import AgentState
import logging
logger = logging.getLogger(__name__)
def prepare_context(state: AgentState) -> dict:
    """读取短期/长期记忆并生成本轮 ``model_context``。

    Repository 的网络异常由 Context Builder 转为降级信息；配置错误、类型错误等
    编程问题继续抛出，避免生产环境悄悄使用错误预算。

    TODO(你来实现)：多会话上线后，这里的 conversation_id 必须来自服务端已校验
    的 conversation 记录，而不是任意浏览器参数。
    """

    logger.info(f"1、进入到装载上下文节点，prepare_context: {state}")
    runtime = get_memory_runtime()
    bundle = build_context_bundle(
        owner_id=state["user_id"],
        conversation_id=state.get("conversation_id", "default"),
        current_input=state["user_input"],
        runtime=runtime,
        compact_execution={
            "intent": state.get("intent", ""),
            "tool_results": compact_tool_results(state.get("tool_results", [])),
            "pending_confirmation": state.get("pending_confirmation"),
            "resolved_reference": state.get("resolved_reference"),
        },
        resolved_reference=state.get("resolved_reference"),
        checkpoint_recent_messages=state.get("recent_messages", []),
        checkpoint_summary=state.get("conversation_summary", {}),
    )
    return {
        "model_context": bundle.messages,
        "conversation_summary": bundle.summary,
        "retrieved_memories": bundle.memories,
        "context_stats": asdict(bundle.stats) if bundle.stats is not None else {},
    }
