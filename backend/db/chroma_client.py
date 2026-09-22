# ============================================================
# ChromaDB 连接管理
# ============================================================

import chromadb
from backend.config import CHROMA_PERSIST_DIR
from backend.db.neo4j_client import  ALLOWED_FIELDS_CN


def get_chroma_client() -> chromadb.PersistentClient:
    """
    获取 ChromaDB 持久化客户端。
    数据存储在本地 chroma_data/ 目录。
    """
    return chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)


def get_person_collection(client: chromadb.PersistentClient) -> chromadb.Collection:
    """
    获取或创建「人物属性」向量集合。
    每个用户的每个人物，其属性文本会生成一个 embedding 存入此集合。
    metadata 中包含 person_id 和 owner_id，用于过滤。
    """
    return client.get_or_create_collection(
        name="person_attributes",
        metadata={"description": "人物属性语义搜索"},
    )


# ============================================================
# TO-DO: 你来实现这些向量操作函数
# ============================================================


def embed_and_store_person(
    collection: chromadb.Collection,
    person_id: str,
    owner_id: str,
    text: str,
    embedding: list[float],
) -> str:
    """
    将人物属性文本的 embedding 存入 ChromaDB。

    Args:
        collection: ChromaDB 集合
        person_id: 人物 ID
        owner_id: 用户 ID
        text: 人物属性文本（用于后续展示匹配片段）
        embedding: LLM 生成的向量（list of float）

    Returns:
        存入的 document ID
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 用 collection.add() 存入向量
    # 2. document_id 用 person_id（一个 person 一个向量，更新时先删再增）
    # 3. metadata 中存 owner_id 和 person_id，用于过滤
    # 4. 如果 person_id 已存在，先 collection.delete(ids=[person_id])
    try:
        collection.delete(ids=[person_id])
        collection.add(
            ids=[person_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[{"person_id": person_id, "owner_id": owner_id}],
        )
        return person_id
    except Exception:
        # add 失败了 → person_id 对应的向量丢失了
        # 但这个函数是被 add_person 或 update_person 调用的
        # 调用方知道失败了，可以重试整个流程
        raise


def semantic_search_person(
    collection: chromadb.Collection,
    query_embedding: list[float],
    owner_id: str,
    limit: int = 5,
) -> list[dict]:
    """
    语义搜索人物。

    Args:
        collection: ChromaDB 集合
        query_embedding: 查询文本的向量
        owner_id: 当前用户 ID（只搜索该用户的数据）
        limit: 返回数量上限

    Returns:
        [{"person_id": "p_xxx", "text": "...", "similarity": 0.87}, ...]
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. collection.query() 时用 where={"owner_id": owner_id} 过滤
    # 2. 返回结果中的 distance 是余弦距离，可以转为相似度 similarity = 1 - distance
    results = collection.query(
        query_embeddings=query_embedding,  # 搜索词的向量
        n_results=limit,  # 返回top_k
        where={"owner_id": owner_id},  # 只搜当前用户的数据
    )
    if not results["ids"][0]:
        return []

    persons = []
    for i, pid in enumerate(results["ids"][0]):
        persons.append({
            "person_id": pid,
            "similarity": round(1 - results["distances"][0][i], 2),
            "text": results["documents"][0][i],
        })
    return persons


def delete_person_embedding(
    collection: chromadb.Collection, person_id: str, owner_id: str
) -> bool:
    """删除一个人物的向量。"""
    # TO-DO: 实现
    collection.delete(ids=[person_id], where={"owner_id": owner_id})
    return True


def build_person_text(properties: dict) -> str:
    """
    将人物属性字典拼接为一段自然语言文本，用于生成 embedding。

    Args:
        properties: 人物属性 dict

    Returns:
        "张三，男，1985年出生，金融行业，清华大学毕业，爱好钓鱼、茅台，性格开朗"
    """

    parts = []
    for key, value in properties.items():
        if key not in ALLOWED_FIELDS_CN.keys():
            continue
        if not value:
            continue
        if isinstance(value, list):
            parts.append("、".join(value))  # 钓鱼、茅台
        elif key == "birth_year":
            parts.append(f"{value}年出生")  # 1985年出生
        else:
            parts.append(str(value))  # 金融、开朗
    return "，".join(parts)

if __name__ == "__main__":
    import backend.services.embedding as embedding_client

    # map = dict()
    # map["name"] = "林志达"
    # map["gender"] = "男"
    # map["birth_year"] = "1998-08-05"
    # map["occupation"] = "程序员"
    # map["education"] = "本科"
    # map["hobbies"] = ["篮球", "电子游戏"]
    # map["personality"] = "幽默,随和"
    # map["tags"] = ["帅气", "强壮"]
    # map["note"] = ""
    #
    # text = build_person_text(map)
    # text_embedding = embedding_client.embed_text(embedding_client.get_embedding_client() , text)
    # collection = get_person_collection(get_chroma_client())
    # embed_and_store_person(collection = collection , person_id = "p_a8423730",owner_id="1" ,text = text , embedding = text_embedding)

    # map = dict()
    # map["name"] = "段汶狄"
    # map["gender"] = "男"
    # map["birth_year"] = "1998-05-15"
    # map["occupation"] = "高级程序员"
    # map["education"] = "本科"
    # map["hobbies"] = ["羽毛球","健身","电子游戏"]
    # map["personality"] = "幽默,随和"
    # map["tags"] = ["强壮","精干"]
    # map["note"] = ""
    # text = build_person_text(map)
    # text_embedding = embedding_client.embed_text(embedding_client.get_embedding_client() , text)
    # collection = get_person_collection(get_chroma_client())
    # embed_and_store_person(collection = collection , person_id = "p_65d18bce",owner_id="1" ,text = text , embedding = text_embedding)

    # map = dict()
    # map["name"] = "林建强1"
    # map["gender"] = "男"
    # map["birth_year"] = "1960-09-15"
    # map["occupation"] = "个体户"
    # map["education"] = "高中"
    # map["hobbies"] = ["钓鱼","开车"]
    # map["personality"] = "善良,随和"
    # map["tags"] =["老实","文化人"]
    # map["note"] = ""
    # text = build_person_text(map)
    # text_embedding = embedding_client.embed_text(embedding_client.get_embedding_client() , text)
    # collection = get_person_collection(get_chroma_client())
    # embed_and_store_person(collection = collection , person_id = "p_e2997a7d1",owner_id="1" ,text = text , embedding = text_embedding)

    # text = "搜一下有喜欢钓鱼的人"
    # text_embedding = embedding_client.embed_text(embedding_client.get_embedding_client() , text)
    # collection = get_person_collection(get_chroma_client())
    # print(semantic_search_person(collection , text_embedding , owner_id = "1"))
    #
    # text = "搜一下喜欢运动的人"
    # text_embedding = embedding_client.embed_text(embedding_client.get_embedding_client(), text)
    # collection = get_person_collection(get_chroma_client())
    # print(semantic_search_person(collection, text_embedding, owner_id="1"))

    collection = get_person_collection(get_chroma_client())
    print(delete_person_embedding(collection, person_id="p_e2997a7d1", owner_id="1"))
