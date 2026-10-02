# ============================================================
# 人物管理 Tools
# 每个函数接收 (**kwargs) 并返回一个 dict
# ============================================================
import logging
import httpx
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from openai import APIConnectionError, APITimeoutError, RateLimitError, InternalServerError
from backend.db import neo4j_client
from backend.db import chroma_client
from backend.db.neo4j_operations import (
    OperationConflict, OperationNotFound, OperationPermissionDenied,
)
from backend.db.neo4j_client import get_neo4j_driver
from backend.db.chroma_client import get_chroma_client, get_person_collection
from backend.services.llm import get_llm_client
from backend.services.embedding import get_embedding_client, embed_text
from backend.agent.execution_policy import ToolErrorCode

logger = logging.getLogger(__name__)

EMBEDDING_FIELDS = {"name", "gender", "birth_year", "occupation",
                    "education", "hobbies", "personality", "tags"}

def tool_response(
    msg: str,
    success: bool,
    data: dict | list | None = None,
    error_code: str | None = None,
    retryable: bool = False,
) -> dict:
    """所有Tool共用的结构化返回协议。

    TODO(你来实现各Tool时)：失败必须选择准确的error_code，例如
    NOT_FOUND、AMBIGUOUS、INVALID_ARGUMENT、CONFLICT或TRANSIENT；不要让
    observe_execution解析中文msg。临时网络错误设置retryable=True。
    """
    response = {"success": success, "msg": msg, "data": data or {}}
    if not success:
        response["error_code"] = error_code or "UNKNOWN"
        response["retryable"] = retryable
    return response

def write_failure(exc: Exception, msg: str, *, business_committed: bool = False,
                  data: dict | None = None) -> dict:
    """写 Tool 共用错误分类，流程判断使用类型，不解析中文错误文案。"""
    if business_committed:
        # 此时回执已经落盘。只允许按原 key 重试索引同步，不能补偿删除图数据。
        return tool_response(msg=msg, success=False, data=data,
                             error_code=ToolErrorCode.TRANSIENT, retryable=True)
    if isinstance(exc, OperationConflict):
        code = ToolErrorCode.CONFLICT
    elif isinstance(exc, OperationPermissionDenied):
        code = ToolErrorCode.PERMISSION_DENIED
    elif isinstance(exc, OperationNotFound):
        code = ToolErrorCode.NOT_FOUND
    elif isinstance(exc, (ValueError, TypeError)):
        code = ToolErrorCode.INVALID_ARGUMENT
    elif isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError,
                          APIConnectionError, APITimeoutError, RateLimitError,
                          InternalServerError, ServiceUnavailable, SessionExpired)):
        # 连接中断可能发生在提交确认丢失之后，按原 key 重放回执才可确定结果。
        code = ToolErrorCode.TRANSIENT
    elif isinstance(exc, Neo4jError) and exc.is_retryable():
        code = ToolErrorCode.TRANSIENT
    else:
        # 编程错误、错误配置以及未识别的永久故障不能诱导自动重试。
        code = ToolErrorCode.UNKNOWN
    return tool_response(msg=msg, success=False, data=data, error_code=code,
                         retryable=code == ToolErrorCode.TRANSIENT)


def _sync_person_index(driver, person_id: str, owner_id: str) -> str | None:
    """把 Neo4j 当前事实同步到可重建的 Chroma 次级索引。

    回执保留首次结果，但人物可能已被后续请求修改或删除，因此不能用旧回执
    的人物快照生成向量。每次重试都读取最新图数据，避免覆盖之后的属性。
    Chroma 的 upsert 用稳定 person_id 替换同一个文档，避免 delete+add
    中间失败把原索引清空。两个存储没有共同事务，也不宣称分布式 exactly-once：
    并发修改仍可能产生短暂滞后，原 key 重试或后续同步可再次收敛到图事实。
    """
    latest = neo4j_client.get_person_detail(driver, person_id, owner_id)
    collection = get_person_collection(get_chroma_client())
    if latest is None:
        # 历史创建/更新重放时目标可能已经被删除，不得复活节点或旧索引。
        chroma_client.delete_person_embedding(collection, person_id, owner_id)
        return None
    person_text = chroma_client.build_person_text(latest["person"])
    embedding = embed_text(get_embedding_client(), person_text)
    collection.upsert(ids=[person_id], embeddings=[embedding], documents=[person_text],
                      metadatas=[{"person_id": person_id, "owner_id": owner_id}])
    return person_id


def add_person(**kwargs) -> dict:
    """先原子提交图业务与回执，再同步索引；创建仍要求服务端幂等键。"""
    owner_id = kwargs.get("owner_id")
    idempotency_key = kwargs.get("idempotency_key")
    if not owner_id or not idempotency_key:
        return tool_response(msg="缺少 owner_id 或服务端幂等键，无法安全创建人物",
                             success=False, error_code=ToolErrorCode.INVALID_ARGUMENT)
    param = {key: value for key, value in kwargs.items() if key in neo4j_client.ALLOWED_FIELDS_CN}
    if not param.get("name"):
        return tool_response(msg="缺少 name，无法创建人物", success=False,
                             error_code=ToolErrorCode.INVALID_ARGUMENT)
    driver = None
    person = None
    try:
        driver = get_neo4j_driver()
        person = neo4j_client.create_person(driver, param, owner_id, idempotency_key)
        doc_id = _sync_person_index(driver, person["id"], owner_id)
        if doc_id is not None:
            person["document_id"] = doc_id
        return tool_response(msg="创建成功", success=True, data=person)
    except Exception as exc:
        logger.error("创建人物或同步索引失败", exc_info=True)
        # 图已经提交时保留人物和独立回执。重试只重建索引，不会再次 CREATE。
        message = "人物已保存，索引同步暂未完成，请按原请求重试" if person is not None else f"创建人物失败: {exc}"
        return write_failure(exc, message, business_committed=person is not None, data=person)
    finally:
        if driver is not None:
            driver.close()


def update_person(**kwargs) -> dict:
    """摘要和图写入都只使用本次补丁；旧值合并发生在数据库事务内。"""
    owner_id, person_id = kwargs.get("owner_id"), kwargs.get("person_id")
    if not owner_id or not person_id:
        return tool_response(msg="缺少 owner_id 或 person_id，无法更新人物信息",
                             success=False, error_code=ToolErrorCode.INVALID_ARGUMENT)
    param = {key: value for key, value in kwargs.items() if key in neo4j_client.ALLOWED_FIELDS_CN}
    driver = None
    person = None
    try:
        driver = get_neo4j_driver()
        key_args = {"idempotency_key": kwargs["idempotency_key"]} if "idempotency_key" in kwargs else {}
        # 不能在 Tool 中读取整个人物再传回 UPDATE：旧请求重试会覆盖后来新值，
        # 参数摘要也会随目标状态变化。回执检查必须先于任何目标存在性检查。
        person = neo4j_client.update_person(driver, person_id, param, owner_id, **key_args)
        if any(field in param for field in EMBEDDING_FIELDS):
            doc_id = _sync_person_index(driver, person_id, owner_id)
            if doc_id is not None:
                person["document_id"] = doc_id
        return tool_response(msg="更新人物成功", success=True, data=person)
    except Exception as exc:
        logger.error("更新人物或同步索引失败", exc_info=True)
        message = "人物更新已保存，索引同步暂未完成，请按原请求重试" if person is not None else f"更新人物失败: {exc}"
        return write_failure(exc, message, business_committed=person is not None, data=person)
    finally:
        if driver is not None:
            driver.close()


def delete_person(**kwargs) -> dict:
    """删除业务的成功回执独立保存；索引失败不回滚已确认的图删除。"""
    if kwargs.get("confirmed") is not True:
        return {**tool_response(msg="需要用户手动确认是否删除人物", success=False),
                "needs_confirmation": True}
    person_id, owner_id = kwargs.get("person_id"), kwargs.get("owner_id")
    if not person_id or not owner_id:
        return tool_response(msg="缺少 person_id 或 owner_id，无法删除人物信息",
                             success=False, error_code=ToolErrorCode.INVALID_ARGUMENT)
    driver = None
    committed = False
    try:
        driver = get_neo4j_driver()
        key_args = {"idempotency_key": kwargs["idempotency_key"]} if "idempotency_key" in kwargs else {}
        neo4j_client.delete_person(driver, person_id, owner_id, **key_args)
        committed = True
        collection = get_person_collection(get_chroma_client())
        chroma_client.delete_person_embedding(collection, person_id, owner_id)
        return tool_response(msg="删除成功", success=True)
    except Exception as exc:
        logger.error("删除人物或清理索引失败", exc_info=True)
        message = "人物已删除，索引清理暂未完成，请按原请求重试" if committed else f"删除人物失败: {exc}"
        return write_failure(exc, message, business_committed=committed)
    finally:
        if driver is not None:
            driver.close()


def find_person(**kwargs) -> dict:
    """
    查找人物。

    需要：
    - query: 搜索关键词
    - search_mode: "exact" | "fuzzy" | "semantic"
    - limit: 返回数量上限
    - owner_id: 当前用户 ID

    Returns:
        {"found": True, "results": [...], "count": N}
    """
    # TO-DO: 实现
    #
    # 备注：
    # exact — 精确匹配
    # 用户明确知道名字，想查这个人。
    # 用户说："张三"
    # → 搜Neo4j WHERE p.name = "张三"
    # → 返回：张三，你的朋友，爱好钓鱼

    # fuzzy — 模糊匹配
    # 用户记不全名字，只知道一部分。
    # 用户说："张"
    # → 搜Neo4j WHERE p.name CONTAINS "张"
    # → 返回：张三、张伟、张建国

    # semantic — 语义搜索
    # 用户不记得名字，用描述来找人。
    # 用户说："那个喜欢钓鱼的"
    # → 调embedding API生成向量
    # → 在ChromaDB里搜相似向量
    # → 返回：张三（相似度0.92）、王五（相似度0.78）

    # 提示：
    # 1. 根据 search_mode 调用不同的搜索函数
    # 2. exact → find_person_exact()
    # 3. fuzzy → find_person_fuzzy()
    # 4. semantic → 先 embed_text(query) 再 semantic_search_person()
    query = kwargs.get("query")
    if not query:
        return tool_response(msg="请提供要搜索的关键词", success=False)

    mode = kwargs.get("search_mode", "fuzzy")  # 默认模糊
    limit = kwargs.get("limit", 5)  # 默认 5 条
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少owner_id参数，无法查询", success=False)

    neo4j_driver = None
    try:
        neo4j_driver = get_neo4j_driver()
        if mode == "exact":
            result = neo4j_client.find_person_exact(neo4j_driver, query, owner_id)
        elif mode == "fuzzy":
            result = neo4j_client.find_person_fuzzy(neo4j_driver, query, owner_id , limit)
        elif mode == "semantic":
            emb_client = get_embedding_client()
            chroma_cli = get_chroma_client()
            collection = get_person_collection(chroma_cli)
            vec = embed_text(emb_client, query)
            result = chroma_client.semantic_search_person(collection, vec, owner_id , limit)
        else:
            return tool_response(msg=f"未知搜索模式: {mode}", success=False)
        # TODO(你来实现)：统一不同搜索模式的空结果和多候选语义。
        # - 没找到：success=False, error_code="NOT_FOUND"
        # - 精确查询出现多个候选：success=False, error_code="AMBIGUOUS",
        #   data={"candidates": [...]}
        # - 唯一命中：success=True，并保证data结构能被后续$ref稳定引用。
        # exact 返回一条人物 dict，fuzzy/semantic 返回列表。直接对 dict 取
        # len() 会把人物字段数误判为“候选人数”，导致唯一命中进入 AMBIGUOUS。
        candidates = [result] if isinstance(result, dict) else result
        if not candidates:
            return tool_response(
                msg="没有找到指定人物",
                success=False,
                error_code=ToolErrorCode.NOT_FOUND,
                data={"query": query},
            )
        elif len(candidates) > 1:
            return tool_response(
                msg="找到多个相关人物",
                success=False,
                error_code=ToolErrorCode.AMBIGUOUS,
                data={"candidates": candidates},
            )
        # 唯一命中统一返回 dict，让规划器的 $ref: data.id 稳定可用。
        return tool_response(msg="查询用户信息成功", success=True, data=candidates[0])
    except Exception as e:
        msg = f"查询人物失败: {str(e)}"
        logger.error(msg, exc_info=True)
        return tool_response(msg=msg, success=False)
    finally:
        if neo4j_driver is not None:
            neo4j_driver.close()

if __name__ == "__main__":
    result1 = add_person(
        name="张三",
        owner_id="user_1",
        idempotency_key="user_1:call_a1",
    )

    result2 = add_person(
        name="张三",
        owner_id="user_1",
        idempotency_key="user_1:call_a1",
    )

    print(result1)
    print(result2)
