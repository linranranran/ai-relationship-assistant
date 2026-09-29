"""为不同 LLM 节点选择它真正需要的受控上下文。"""

from backend.agent.state import AgentState


def select_context_for_node(state: AgentState, node_name: str) -> list[dict]:
    """从 ``model_context`` 选择节点子集，并避免重复当前用户输入。

    ``prepare_context`` 已负责总 Token 预算。本函数只做权限最小化：分类器不需要
    Tool 结果，回复生成器的当前问题已经包含在专用 Prompt 中，不应再重复发送。

    各节点增加专用 Prompt 后，``services.llm.chat`` 会对最终载荷再次执行预算检查，
    防止节点 Prompt 绕过 prepare_context 的总预算。
    """
    allowed_nodes = {"classify_intent", "plan_tasks", "generate_response"}
    if node_name not in allowed_nodes:
        raise ValueError(f"未知上下文消费者：{node_name}")

    current_input = state.get("user_input", "")
    selected: list[dict] = []
    context_items = state.get("model_context", [])
    for index, item in enumerate(context_items):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        context_type = item.get("context_type")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            continue

        # prepare_context 总是把本轮输入放在最后。只移除最后这一条，不能按文本
        # 删除全部匹配项，否则用户重复说“继续”时会误删真正的历史消息。
        is_current_input = (
            index == len(context_items) - 1
            and role == "user"
            and content == current_input
        )
        if node_name in {"classify_intent", "generate_response"} and is_current_input:
            continue
        if node_name == "classify_intent" and context_type == "long_term_memory":
            # 意图分类只需要理解用户表达，不需要读取客户画像事实。
            continue
        selected.append({"role": role, "content": content})

    if node_name == "plan_tasks" and not any(
        item.get("role") == "user" and item.get("content") == current_input
        for item in selected
    ):
        selected.append({"role": "user", "content": current_input})
    return selected
