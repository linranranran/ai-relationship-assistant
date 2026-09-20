# ============================================================
# 查询 Tools
# ============================================================
from backend.tools.person import tool_response
from backend.db import neo4j_client
import logging
logger = logging.getLogger(__name__)

def query_relation_path(**kwargs) -> dict:
    """
    查询关系路径。

    需要：
    - target_person_id: 目标人物 ID
    - max_depth: 最大搜索深度（默认 7）
    - owner_id: 当前用户 ID（作为查询起点）

    Returns:
        {"path": [{"step": 1, "from": "我", "to": "张大", "relation": "父亲"}, ...]}
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 调用 backend.db.neo4j.get_relation_path()，起点是当前用户
    # 2. 把 raw 路径格式化为易读的 step-by-step 格式
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    from_person_id = kwargs.get("from_person_id")
    if not from_person_id:
        return tool_response(msg="缺少 from_person_id，无法确定查询起点", success=False)
    target_person_id = kwargs.get("target_person_id")
    if not target_person_id:
        return tool_response(msg="缺少target_person_id，无法查询人物关系", success=False)
    max_depth = kwargs.get("max_depth" , 7)
    try:
        neo4j_driver = neo4j_client.get_neo4j_driver()
        relation_list = neo4j_client.get_relation_path(neo4j_driver , from_person_id , target_person_id , owner_id , max_depth)
        return tool_response(msg="查询成功", success=True, data=relation_list)
    except Exception as e:
        logger.error(f"查询人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"查询人物关系失败: {str(e)}", success=False)

def get_person_detail(**kwargs) -> dict:
    """
    获取人物完整信息。

    需要：
    - person_id: 人物 ID

    Returns:
        {"person": {...}, "relations": [...], "relation_path": [...]}
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 调用 backend.db.neo4j.get_person_detail()
    # 2. 同时查询该人物到当前用户的关系路径
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    person_id = kwargs.get("person_id")
    if not person_id:
        return tool_response(msg="缺少target_person_id，无法查询人物关系", success=False)
    try:
        neo4j_driver = neo4j_client.get_neo4j_driver()
        relation_list = neo4j_client.get_person_detail(neo4j_driver, person_id , owner_id)
        return tool_response(msg="查询成功", success=True, data=relation_list)
    except Exception as e:
        logger.error(f"查询人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"查询人物关系失败: {str(e)}", success=False)


def list_relations(**kwargs) -> dict:
    """
    列出某人的所有关系。

    需要：
    - person_id: 人物 ID
    - relation_type: 可选筛选

    Returns:
        {"person_name": "张三", "relations": [...]}
    """
    # TO-DO: 实现
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)
    person_id = kwargs.get("person_id")
    if not person_id:
        return tool_response(msg="缺少target_person_id，无法查询人物关系", success=False)
    relation_type = kwargs.get("relation_type")
    try:
        neo4j_driver = neo4j_client.get_neo4j_driver()
        result = neo4j_client.list_person_relations(neo4j_driver, person_id, owner_id, relation_type)
        return tool_response(msg="查询成功", success=True, data=result)
    except Exception as e:
        logger.error(f"查询人物关系失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"查询人物关系失败: {str(e)}", success=False)