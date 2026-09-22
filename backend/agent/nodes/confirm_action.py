"""旧确认节点兼容入口。

最初的骨架准备通过大模型判断“用户是否确认”，这种方式无法证明用户确认的是
哪一次操作，也无法真正暂停状态图。真实实现已经迁移到
``request_confirmation.py``：它使用 LangGraph interrupt 保存检查点，并通过
独立的恢复接口接收结构化布尔值。

保留这个别名是为了避免你旧代码中的 import 立即报错；新代码请直接导入
``request_confirmation``。
"""

from backend.agent.nodes.request_confirmation import request_confirmation


confirm_action = request_confirmation


def route_after_confirmation(*_args, **_kwargs):
    raise RuntimeError(
        "route_after_confirmation 已废弃：确认分支现在由 request_confirmation 的 "
        "Command(goto=...) 完成"
    )
