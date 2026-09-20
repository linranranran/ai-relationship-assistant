# ============================================================
# 人物管理 Tools
# 每个函数接收 (**kwargs) 并返回一个 dict
# ============================================================
import logging
from backend.db import neo4j_client
from backend.db import chroma_client
from backend.db.neo4j_client import get_neo4j_driver
from backend.db.chroma_client import get_chroma_client, get_person_collection
from backend.services.llm import get_llm_client
from backend.services.embedding import get_embedding_client, embed_text

logger = logging.getLogger(__name__)

EMBEDDING_FIELDS = {"name", "gender", "birth_year", "occupation",
                    "education", "hobbies", "personality", "tags"}

def tool_response(msg:str , success:bool , data:dict = None) -> dict:
    return {"success": success, "msg": msg , "data": data or {}}

def add_person(**kwargs) -> dict:
    """
    新增一个人物。

    流程：
    1. 在 Neo4j 中创建 Person 节点
    2. 将人物属性文本生成 embedding 存入 ChromaDB

    需要从 kwargs 中获取：
    - name, gender, birth_year, occupation, education, hobbies, personality, tags, note, photo_url
    - 还需要 owner_id（当前用户 ID，由 Agent 注入）

    Returns:
        {"person_id": "p_xxx", "name": "张三", ...}
    """
    # ── 第 1 层：参数校验（零成本，先做） ──
    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属", success=False)

    # 只取允许存入的字段
    param = {}
    for key, value in kwargs.items():
        if key in neo4j_client.ALLOWED_FIELDS_CN:
            param[key] = value

    if not param.get("name"):
        return tool_response(msg="缺少 name，无法创建人物", success=False)

    # ── 第 2 层：按“最易炸 → 最稳定”的顺序编排 ──
    person_id = None  # 用于回滚

    try:
        # 2.1 生成 embedding（外部 API，最可能炸）
        emb_client = get_embedding_client()
        person_text = chroma_client.build_person_text(param)
        embedding = embed_text(emb_client, person_text)

        # 2.2 写入 Neo4j（本地 Docker，基本不炸）
        driver = get_neo4j_driver()
        person = neo4j_client.create_person(driver, param, owner_id)
        person_id = person["id"]

        # 2.3 写入 ChromaDB（本地文件，基本不炸）
        chroma_cli = get_chroma_client()
        collection = get_person_collection(chroma_cli)
        doc_id = chroma_client.embed_and_store_person(
            collection,
            person_id=person_id,
            owner_id=owner_id,
            text=person_text,
            embedding=embedding,
        )

        person["document_id"] = doc_id
        return tool_response(msg="创建成功", success=True, data=person)

    except Exception as e:
        # ── 补偿回滚：清理已写入的脏数据 ──
        if person_id:
            try:
                neo4j_client.delete_person(driver, person_id)
            except Exception:
                # 回滚失败也要继续抛原始错误
                msg = f"创建人物失败,失败原因:{str(e)},但已生成人物id: {person_id},需要根据人物id回滚删除用户"
                logger.error(msg, exc_info=True)
                return tool_response(msg=msg, success=False)
        logger.error(f"创建人物失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"创建人物失败: {str(e)}", success=False)


def update_person(**kwargs) -> dict:
    """
    更新一个人物的信息。

    需要：
    - person_id: 人物 ID
    - 其他要更新的字段

    Returns:
        更新后的人物完整信息
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 调用 backend.db.neo4j_client.update_person()
    # 2. 如果更新了会影响语义搜索的字段（name, hobbies, occupation 等），
    #    需要重新生成 embedding 并更新 ChromaDB

    # 备注：
    # note — 不建议进向量note是自由文本备注：
    # "上次见面说想换工作"
    # "欠我500块，催了三次"
    # "他老婆是我大学同学李丽"
    # 混进embedding文本后："张三，男，金融行业，爱好钓鱼，备注：欠我500块催了三次他老婆是我大学同学李丽"
    # 1.噪音污染搜
    # "谁喜欢钓鱼"时，note里的大量无关文本会稀释"钓鱼"的权重，反而降低搜索精度。
    # 2.误匹配搜
    # "大学同学" → 想找大学同学，但张三只是因为note里写了"他老婆是我大学同学"就命中——张三本人不是大学同学。

    owner_id = kwargs.get("owner_id")
    if not owner_id:
        return tool_response(msg="缺少 owner_id，无法确定数据归属，无法更新人物信息", success=False)

    person_id = kwargs.get("person_id")
    if not person_id:
        return tool_response(msg="缺少 person_id，无法更新人物信息", success=False)
    try:
        need_reindex = any(f in kwargs for f in EMBEDDING_FIELDS)
        # 2.0 需要先查询老数据，在老数据的基础上新增、修改新的数据
        param = {}
        for key, value in kwargs.items():
            if key in neo4j_client.ALLOWED_FIELDS_CN:
                param[key] = value
        driver = get_neo4j_driver()
        current_person = neo4j_client.get_person_detail(driver, person_id)
        if not current_person:
            return tool_response(msg="未获取到人物信息", success=False)
        # 2.0 合并：旧数据打底，新数据覆盖
        param = {**current_person["person"], **param}

        if need_reindex:
            # 2.1 生成 embedding（外部 API，最可能炸）
            emb_client = get_embedding_client()
            person_text = chroma_client.build_person_text(param)
            embedding = embed_text(emb_client, person_text)

        # 2.2 更新 Neo4j（本地 Docker，基本不炸）
        person = neo4j_client.update_person(driver, person_id, param)

        if need_reindex:
            # 2.3 更新 ChromaDB（本地文件，基本不炸）
            chroma_cli = get_chroma_client()
            collection = get_person_collection(chroma_cli)
            doc_id = chroma_client.embed_and_store_person(
                collection,
                person_id=person_id,
                owner_id=owner_id,
                text=person_text,
                embedding=embedding,
            )
            person["document_id"] = doc_id
        return tool_response(msg="更新人物成功", success=True, data=person)

    except Exception as e:
        logger.error(f"更新人物失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"更新人物失败: {str(e)}", success=False)


def delete_person(**kwargs) -> dict:
    """
    删除一个人物。

    需要：
    - person_id: 人物 ID
    - confirmed: 用户是否已确认

    Returns:
        {"success": True, "person_name": "张三"}
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 如果 confirmed 不为 True，返回 {"needs_confirmation": True}
    # 2. 调用 backend.db.neo4j_client.delete_person()
    # 3. 调用 backend.db.chroma_client.delete_person_embedding()
    confirmed = kwargs.get("confirmed")
    if not confirmed:
        return tool_response(msg="需要用户手动确认是否删除人物", success=False)

    person_id = kwargs.get("person_id")
    if not person_id:
        return tool_response(msg="缺少 person_id，无法删除人物信息", success=False)

    try:
        neo4j_driver = get_neo4j_driver()
        neo4j_delete_flag = neo4j_client.delete_person(neo4j_driver, person_id)
        chroma_cli = get_chroma_client()
        collection = get_person_collection(chroma_cli)
        chroma_delete_flag = chroma_client.delete_person_embedding(collection, person_id)

        return tool_response(msg="删除成功", success=True)
    except Exception as e:
        logger.error(f"删除人物失败: {str(e)}", exc_info=True)
        return tool_response(msg=f"删除人物失败: {str(e)}", success=False)


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
        return tool_response(msg="查询用户信息成功", success=True , data = result)
    except Exception as e:
        msg = f"查询人物失败: {str(e)}"
        logger.error(msg, exc_info=True)
        return tool_response(msg=msg, success=False)