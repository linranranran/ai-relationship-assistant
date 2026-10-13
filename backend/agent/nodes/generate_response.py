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

    from backend.agent.strategies.contracts import is_request_direct
    if is_request_direct(state):
        import json
        from backend.agent.direct_runtime import direct_records
        prompt = (
            "回答用户的完整原始请求。下面工具回执才是操作是否成功的证据；模型草稿不是证据。"
            "不要把一次工具成功说成全部需求完成。说明已完成、被拒绝、失败和未执行部分；"
            "NOT_FOUND 表示查不到人物，空字段表示暂无记录，不得编造。没有业务工具的闲聊可以直接回答。"
            "用户拒绝的写操作未执行，已经提交的操作不会因后续取消或失败而回滚。\n"
            "用户界面只展示姓名、业务结果和变更，不输出内部人物/关系 ID、call_id 或参数 JSON。"
            "用户本人称为‘你’，不要把登录手机号当作姓名展示。\n"
            + json.dumps({"question": state["user_input"], "user_clarifications": state.get("direct_clarifications", []),
                          "tool_records": direct_records(state),
                          "answer_draft": state.get("direct_final_message", {}).get("content", ""),
                          "requirements_verified": False}, ensure_ascii=False, default=str)
        )
    elif state.get("execution_version") == 2:
        import json

        prompt = (
            "根据下面各业务目标的真实执行结果回答原始问题。分别说明成功、失败和未执行部分；不能把一个成功工具说成整个任务成功。"
            "completion_outcome=NOT_FOUND 表示查询完成但没有找到目标；NO_RECORD 表示对应字段暂无记录。不能编造属性或把负向查询说成写入成功。\n"
            + json.dumps(
                {
                    "question": state["user_input"],
                    "goals": [
                        {
                            "description": goal["description"],
                            "status": goal["status"],
                            "failure_reason": goal.get("failure_reason"),
                            "completion_outcome": goal.get("completion_outcome"),
                            "query_spec": goal.get("query_spec"),
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
    if is_request_direct(state):
        # 没有需求清单不能宣称逐项验收；这个终态只代表模型结束了调用循环。
        update.update(execution_decision="AGENT_FINISHED", completion_basis="MODEL_STOP_WITH_TOOL_RECEIPTS",
                      requirements_verified=False)
    elif state.get("execution_version") == 2:
        update["execution_decision"] = (
            "COMPLETE"
            if all(g["status"] == "SUCCEEDED" for g in state["goals"])
            else "PARTIALLY_COMPLETED"
        )
    return Command(update=update, goto="finalize_memory")
