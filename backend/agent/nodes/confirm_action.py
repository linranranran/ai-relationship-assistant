# ============================================================
# 确认节点
# ============================================================

from backend.agent.state import AgentState
from backend.agent.prompts.system_prompts import CONFIRM_ACTION_PROMPT
from backend.services.llm import get_llm_client, chat


def confirm_action(state: AgentState) -> AgentState:
    """
    需要用户确认时，生成确认提示。

    输入：state["confirmation_type"], state["tool_results"]
    输出：state["response"]
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 格式化 CONFIRM_ACTION_PROMPT
    # 2. 调用 chat() 生成确认提示
    # 3. 存入 state["response"]
    # 4. 设置 state["needs_confirmation"] = True
    #    （这样下一轮对话时，Agent 会感知到正在等待确认）
    raise NotImplementedError("confirm_action — 等你来实现 ✍️")


def route_after_confirmation(state: AgentState) -> str:
    """
    用户确认后，决定是执行还是取消。

    Returns:
        "execute" — 用户确认，执行操作
        "cancel" — 用户取消，生成取消回复
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 检查 state["user_confirmed"] 是否为 True
    # 2. 如果是 → "execute"
    # 3. 如果否 → "cancel"
    raise NotImplementedError("route_after_confirmation — 等你来实现 ✍️")