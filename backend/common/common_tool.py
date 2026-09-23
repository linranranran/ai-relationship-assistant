import uuid

def generate_uuid() -> str:
    # call_id 会用于审计和幂等，不能截断成只有 32 位随机空间的短 ID。
    return str(uuid.uuid4())

def generate_prefix_uuid(prefix:str) -> str:
    return f"{prefix}{generate_uuid()}"


def generate_deterministic_prefix_uuid(prefix: str, stable_key: str) -> str:
    """根据稳定业务键生成可重复计算的 UUID，用于请求重放。"""
    return f"{prefix}{uuid.uuid5(uuid.NAMESPACE_URL, stable_key)}"
