"""导入边界：浏览器选择 Person，后端验证归属，原文直接保存。

模拟数据与未来企微数据应在这一层标准化，不应把文件解析、去重或授权交给 LLM。
本版只实现模拟来源，不添加空壳 WeCom 适配器，也不宣称已接入企业微信。
"""

from backend.db import neo4j_client
from backend.db.customer_chat_store import save_messages
from backend.sales.contracts import ChatImportRequest, ChatImportResult


class SalesPersonNotFound(ValueError):
    """人物不存在或不属于当前用户，对外使用同一种结果。"""


def require_owned_profile(owner_id: str, person_id: str) -> dict:
    if not owner_id or not person_id:
        raise SalesPersonNotFound("客户不存在")
    driver = neo4j_client.get_neo4j_driver()
    try:
        detail = neo4j_client.get_person_detail(driver, person_id, owner_id)
        if not detail or not detail.get("person"):
            raise SalesPersonNotFound("客户不存在")
        return detail["person"]
    finally:
        driver.close()


def import_simulated_chat(*, owner_id: str, request: ChatImportRequest) -> ChatImportResult:
    # Neo4j 校验与 PG 写入不是跨库事务；不声称能防止期间的并发删除。
    # 生成准备前会重新检查人物；原文记录则保留来源，不能因此被清理。
    require_owned_profile(owner_id, request.person_id)
    return save_messages(owner_id=owner_id, person_id=request.person_id, payload=request.payload)
