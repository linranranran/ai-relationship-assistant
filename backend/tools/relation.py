# ============================================================
# 关系管理 Tools
# ============================================================

from backend.tools.person import tool_response
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
        relation = neo4j_client.add_relation(driver, from_person_id, to_person_id, relation_type, owner_id ,kwargs.get("through_person_id" , None),kwargs.get("note" , None))
        return tool_response(msg="创建成功", success=True, data=relation)
    except Exception as e:
        logger.error(f"创建人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"创建人物关系失败: {str(e)}", success=False)
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
        old_relation = neo4j_client.get_relation_by_id(driver, relation_id, owner_id)
        if old_relation is None:
            return tool_response(msg="未查询到正确的人物关系，请确认relation_id是否正确", success=False)
        # 2. 合并：旧值打底，新值覆盖
        new_type = new_relation_type or old_relation["type"]
        new_owner = owner_id or old_relation["owner"]
        new_through_arg = kwargs.get("through_person_id")
        new_through = new_through_arg if new_through_arg is not None else old_relation["through"]
        new_note_arg = kwargs.get("note")
        new_note = new_note_arg if new_note_arg is not None else old_relation["note"]

        neo4j_client.update_relation(driver, relation_id,new_type, new_owner, new_note,new_through)

        return tool_response(msg="修改任务关系成功", success=True, data=None)
    except Exception as e:
        logger.error(f"修改人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"修改人物关系失败: {str(e)}", success=False)
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
        neo4j_client.delete_relation(driver, relation_id, owner_id)
        return tool_response(msg="删除人物关系成功", success=True, data=None)
    except Exception as e:
        logger.error(f"删除人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"删除人物关系失败: {str(e)}", success=False)
    finally:
        if driver is not None:
            driver.close()
