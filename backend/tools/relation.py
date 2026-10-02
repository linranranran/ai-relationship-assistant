# ============================================================
# 关系管理 Tools
# ============================================================

from backend.tools.person import tool_response, write_failure
import logging
from backend.db import neo4j_client
from backend.db.neo4j_client import get_neo4j_driver
logger = logging.getLogger(__name__)

def add_relation(**kwargs) -> dict:
    """
    新增关系。

    需要：
    - from_person_id, to_person_id, relation_type, through_person_id (可选), note (可选)
    - owner_id: 当前用户 ID

    Returns:
        {"relation_id": "r_xxx", "from": "p_xxx", "to": "p_yyy", "type": "朋友"}
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 调用 backend.db.neo4j.add_relation()

    # ── 第 1 层：参数校验（零成本，先做） ──
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    to_person_id = kwargs.get("to_person_id")
    if not to_person_id:
        return tool_response(msg="缺少 to_person_id，无法建立人物关系", success=False)
    from_person_id = kwargs.get("from_person_id")
    if not from_person_id:
        return tool_response(msg="缺少 from_person_id，无法建立人物关系", success=False)
    relation_type = kwargs.get("relation_type")
    if not relation_type:
        return tool_response(msg="缺少 relation_type，无法建立人物关系", success=False)
    driver = None
    try:
        # 2.2 写入 Neo4j（本地 Docker，基本不炸）
        driver = get_neo4j_driver()
        key_args = {"idempotency_key": kwargs["idempotency_key"]} if "idempotency_key" in kwargs else {}
        # key 只由服务端执行器注入；直接旧调用省略 key 时保持原有签名兼容。
        relation = neo4j_client.add_relation(
            driver, from_person_id, to_person_id, relation_type, owner_id,
            kwargs.get("through_person_id"), kwargs.get("note"), **key_args,
        )
        return tool_response(msg="创建成功", success=True, data=relation)
    except Exception as e:
        logger.error(f"创建人物关系失败: {str(e)}", exc_info=True)
        return write_failure(e, f"创建人物关系失败: {e}")
    finally:
        if driver is not None:
            driver.close()


def update_relation(**kwargs) -> dict:
    """
    修改关系。

    需要：
    - relation_id: 关系边 ID
    - new_relation_type (可选), note (可选)

    Returns:
        更新后的关系信息
    """
    # TO-DO: 实现
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    relation_id = kwargs.get("relation_id")
    if not relation_id:
        return tool_response(msg="缺少 relation_id，无法修改人物关系", success=False)
    new_relation_type = kwargs.get("new_relation_type")
    driver = None
    try:
        # 2.2 写入 Neo4j（本地 Docker，基本不炸）
        driver = get_neo4j_driver()
        key_args = {"idempotency_key": kwargs["idempotency_key"]} if "idempotency_key" in kwargs else {}
        # 不在事务外先查旧边：旧请求重放时边可能已被删除，或后来又被修改。
        # 传入原请求的补丁，由 Neo4j 在回执检查之后合并，摘要始终稳定。
        neo4j_client.update_relation(
            driver, relation_id, new_relation_type or None, owner_id,
            kwargs.get("note"), kwargs.get("through_person_id"), **key_args,
        )

        return tool_response(msg="修改任务关系成功", success=True, data=None)
    except Exception as e:
        logger.error(f"修改人物关系失败: {str(e)}", exc_info=True)
        return write_failure(e, f"修改人物关系失败: {e}")
    finally:
        if driver is not None:
            driver.close()


def delete_relation(**kwargs) -> dict:
    """
    删除关系。

    需要：
    - relation_id: 关系边 ID

    Returns:
        {"success": True}
    """
    # 最后一层防线：即使上游节点以后被改坏，没有服务端授权也不能删除。
    if kwargs.get("confirmed") is not True:
        return {
            **tool_response(msg="需要用户手动确认是否删除人物关系", success=False),
            "needs_confirmation": True,
        }

    # TO-DO: 实现
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    relation_id = kwargs.get("relation_id")
    if not relation_id:
        return tool_response(msg="缺少 relation_id，无法删除人物关系", success=False)
    driver = None
    try:
        # 2.2 写入 Neo4j（本地 Docker，基本不炸）
        driver = get_neo4j_driver()
        key_args = {"idempotency_key": kwargs["idempotency_key"]} if "idempotency_key" in kwargs else {}
        neo4j_client.delete_relation(driver, relation_id, owner_id, **key_args)
        return tool_response(msg="删除人物关系成功", success=True, data=None)
    except Exception as e:
        logger.error(f"删除人物关系失败: {str(e)}", exc_info=True)
        return write_failure(e, f"删除人物关系失败: {e}")
    finally:
        if driver is not None:
            driver.close()
