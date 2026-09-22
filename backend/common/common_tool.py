import uuid

def generate_uuid() -> str:
    # call_id 会用于审计和幂等，不能截断成只有 32 位随机空间的短 ID。
    return str(uuid.uuid4())

def generate_prefix_uuid(prefix:str) -> str:
    return f"{prefix}{generate_uuid()}"
