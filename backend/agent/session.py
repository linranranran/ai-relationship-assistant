"""Agent 会话标识。

thread_id 是 LangGraph 查找检查点的主键。它必须由服务端生成，不能信任前端，
否则用户可以猜测别人的 thread_id 并恢复别人的任务。
"""

from hashlib import sha256


# 同一张图升级到不兼容的 State 结构时，可以提升命名空间版本，避免新代码误读
# 旧检查点。namespace 不能替代数据库迁移，但能提供清晰的版本隔离边界。
CHECKPOINT_NAMESPACE = "relationship-agent-v1"
DEFAULT_CONVERSATION_ID = "default"


def derive_thread_id(owner_id: str) -> str:
    """为当前用户生成稳定且不暴露原始用户 ID 的默认会话标识。

    第一版每个用户只有一个 Agent 会话，足够跑通学习链路。
    TODO(你来实现)：支持多会话时，把服务端校验过的 conversation_id 加入原文，
    并在数据库中保存 conversation_id 与 owner_id 的归属关系。
    """
    raw = f"relationship-agent:v1:{owner_id}:{DEFAULT_CONVERSATION_ID}"
    return f"thread_{sha256(raw.encode('utf-8')).hexdigest()}"
