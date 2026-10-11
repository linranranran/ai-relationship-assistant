# ============================================================
# 回复生成节点
# ============================================================

from backend.agent.memory.context_selector import select_context_for_node
from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import GENERATE_RESPONSE_PROMPT
from time import monotonic
from uuid import uuid4
from backend.services.llm import get_llm_client, stream_chat
from backend.agent.streaming import emit_progress
from langgraph.types import Command


def generate_response(state: AgentState) -> Command:
    """
    根据执行结果生成自然语言回复。

    输入：state["intent"], state["user_input"], state["tool_results"]
    输出：state["response"]
    """

    if state.get("execution_version") == 2:
        import json

        prompt = (
            "根据下面各业务目标的真实执行结果回答原始问题。分别说明成功、失败和未执行部分；不能把一个成功工具说成整个任务成功。\n"
            + json.dumps(
                {
                    "question": state["user_input"],
                    "goals": [
                        {
                            "description": goal["description"],
                            "status": goal["status"],
                            "failure_reason": goal.get("failure_reason"),
                            "result_excerpt": json.dumps(
                                goal.get("result", []), ensure_ascii=False
                            )[:1600],
                            "result_truncated": len(
                                json.dumps(goal.get("result", []), ensure_ascii=False)
                            )
                            > 1600,
                        }
                        for goal in state.get("goals", [])
                    ],
                },
                ensure_ascii=False,
            )
        )
    else:
        prompt = GENERATE_RESPONSE_PROMPT.format(
            intent=state["intent"],
            user_input=state["user_input"],
            tool_results=state["tool_results"],
        )
    llm_client = get_llm_client()
    messages = select_context_for_node(state, "generate_response")
    messages.append({"role": "user", "content": prompt})
    # 每次重新生成都有新 response_id。重试时前端替换旧草稿，避免两份答案拼接。
    response_id = str(uuid4())
    emit_progress("response.started", node="generate_response", response_id=response_id)
    pieces, pending = [], []
    last_flush = monotonic()
    try:
        for piece in stream_chat(llm_client, "", messages):
            pieces.append(piece)
            pending.append(piece)
            # 约 80ms 或 128 字合并一次，减少 PG 写入和 React 渲染次数。
            # 单次供应商读取卡住时不会强制定时刷；首个内容立即推送。
            if (
                len(pieces) == 1
                or monotonic() - last_flush >= 0.08
                or sum(map(len, pending)) >= 128
            ):
                emit_progress(
                    "response.delta",
                    node="generate_response",
                    response_id=response_id,
                    delta="".join(pending),
                )
                pending.clear()
                last_flush = monotonic()
    finally:
        llm_client.close()
    if pending:
        emit_progress(
            "response.delta",
            node="generate_response",
            response_id=response_id,
            delta="".join(pending),
        )
    # Checkpoint / conversation_message 保存完整答案，不保存逐块增量。
    update = {"response": "".join(pieces)}
    if state.get("execution_version") == 2:
        update["execution_decision"] = (
            "COMPLETE"
            if all(g["status"] == "SUCCEEDED" for g in state["goals"])
            else "PARTIALLY_COMPLETED"
        )
    return Command(update=update, goto="finalize_memory")
