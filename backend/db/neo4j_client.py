# ============================================================
# Neo4j 连接管理
# ============================================================
from neo4j import GraphDatabase, Driver, Query
from backend.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
import uuid

ALLOWED_FIELDS_CN = {"name":"姓名", "gender":"性别", "birth_year":"出生年月", "occupation":"职业","education":"学历", "hobbies":"爱好", "personality":"性格", "tags":"标签", "note":"备注"}

# 动态生成更新CQL
def build_update_cql(properties: dict) -> str:
    set_clauses = [
        f"p.{field} = ${field}"
        for field in ALLOWED_FIELDS_CN.keys()
        if properties is None or field in properties
    ]
    return  ", ".join(set_clauses)

# 动态生成新增CQL
def build_create_cql() -> str:
    set_clauses = [
        f"{field}: ${field}"
        for field in ALLOWED_FIELDS_CN.keys()
    ]
    return  ",".join(set_clauses)

COMMON_PERSON_FIELD_NAME = f"""
    id: $id,
    owner_id: $owner_id,
    created_at: datetime(),{build_create_cql()}
"""

COMMON_PERSON_RETURN_FIELD_NAME = ".id, ." + ", .".join(ALLOWED_FIELDS_CN.keys())

def get_neo4j_driver() -> Driver:
    """
    创建 Neo4j 驱动实例。
    使用完毕后调用 driver.close() 释放资源。
    """
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )
    # 验证连接
    driver.verify_connectivity()
    return driver


# ============================================================
# TO-DO: 你来实现这些 Cypher 操作函数
# 每个函数签名+docstring 已经写好，你只需要实现函数体
# ============================================================

def generate_uuid() -> str:
    return str(uuid.uuid4())[:8]

def create_person(driver: Driver, properties: dict, owner_id: str) -> dict:
    """
    在 Neo4j 中创建一个 Person 节点。

    Args:
        driver: Neo4j 驱动
        properties: 人物属性 dict，包含 name, gender, birth_year, occupation,
                    education, hobbies, personality, tags, note 等
        owner_id: 创建者用户 ID

    Returns:
        {"person_id": "p_xxx", "name": "张三", ...}

    Example:
        create_person(driver, {"name": "张三", "occupation": "金融"}, "user_1")
        {"person_id": "p_089", "name": "张三", "occupation": "金融", ...}
    """
    with driver.session() as session:
        person_id = "p_" +generate_uuid()
        result = session.run(
            f"""
            CREATE ( p:Person {{{COMMON_PERSON_FIELD_NAME}}})
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person""",
            id=person_id,
            name=properties.get("name", ""),
            gender=properties.get("gender", "未知"),
            birth_year=properties.get("birth_year"),
            occupation=properties.get("occupation"),
            education=properties.get("education"),
            hobbies=properties.get("hobbies", []),
            personality=properties.get("personality"),
            tags=properties.get("tags", []),
            note=properties.get("note"),
            owner_id=owner_id,
        )
        return result.single()["person"]


def update_person(driver: Driver, person_id: str, properties: dict, owner_id: str) -> dict:
    """
    更新一个 Person 节点的属性。只更新传入的字段。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID
        properties: 要更新的字段 dict，只包含需要修改的字段

    Returns:
        更新后的完整人物信息

    Raises:
        ValueError: 人物不存在
    """
    check = get_person_detail(driver, person_id, owner_id)
    if check is None:
        raise ValueError(f"人物不存在或无权修改: {person_id}")

    allowed_properties = {
        key: value for key, value in properties.items() if key in ALLOWED_FIELDS_CN
    }
    if not allowed_properties:
        return check["person"]

    params = {**allowed_properties, "id": person_id, "owner_id": owner_id}
    set_str = build_update_cql(allowed_properties)
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:Person {{id: $id}})
            WHERE p.owner_id = $owner_id
            SET {set_str}
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            """,
            **params,
        )
        record = result.single()
        if record is None:
            raise ValueError(f"人物不存在或无权修改: {person_id}")
        return record["person"]


def delete_person(driver: Driver, person_id: str, owner_id: str) -> bool:
    """
    删除一个人物节点及其所有关系。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID

    Returns:
        True 表示删除成功

    Raises:
        ValueError: 人物不存在
    """
    check = get_person_detail(driver, person_id, owner_id)
    if check is None:
        raise ValueError(f"delete_person — 未找到该用户,id={person_id}")
    if check["person"].get("is_self"):
        raise ValueError("不能删除当前账号绑定的本人节点")
    else:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (p:Person {id: $id, owner_id: $owner_id})
                DETACH DELETE p
                RETURN count(p) AS deleted
                """,
                id=person_id,
                owner_id=owner_id,
            )
            record = result.single()
            if record is not None and record.get("deleted", 1) == 0:
                raise ValueError(f"人物不存在或无权删除: {person_id}")
            return True


def find_person_exact(driver: Driver, name: str, owner_id: str) -> dict | None:
    """
    精确查找人物（按姓名）。

    Args:
        driver: Neo4j 驱动
        name: 精确姓名
        owner_id: 当前用户 ID

    Returns:
        人物信息 dict，如果没找到返回 None
    """
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:Person)
            WHERE p.name = $name and p.owner_id = $owner_id
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            """
            ,
            name = name,
            owner_id = owner_id)
        result_data = result.single()
        if result_data is None:
            return None
        else:
            return result_data["person"]


def find_person_fuzzy(driver: Driver, query: str, owner_id: str, limit: int = 5) -> list[dict]:
    """
    模糊查找人物（按姓名模糊匹配）。

    Args:
        driver: Neo4j 驱动
        query: 搜索关键词
        owner_id: 当前用户 ID
        limit: 返回数量上限

    Returns:
        匹配的人物列表，按相似度排序
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 用 Cypher 的 CONTAINS 或 =~ 正则做模糊匹配
    # 2. 比如 "小张" 应该能匹配到 "张伟"
    # 3. 只返回当前用户的关系网中的人

    with driver.session() as session:
        result = session.run(
            f"""
            MATCH (p:Person)
            WHERE p.name CONTAINS $search_term AND p.owner_id = $owner_id
            RETURN p {{{COMMON_PERSON_RETURN_FIELD_NAME}}} AS person
            ORDER BY p.name
            LIMIT $limit
            """,
            search_term = query,
            owner_id = owner_id,
            limit = limit)
        return [record["person"] for record in result]


def add_relation(
    driver: Driver,
    from_person_id: str,
    to_person_id: str,
    relation_type: str,
    owner_id: str,
    through_person_id: str | None = None,
    note: str | None = None,
) -> dict:
    """
    在两个人之间创建关系边。

    Args:
        driver: Neo4j 驱动
        from_person_id: 起点人物 ID
        to_person_id: 终点人物 ID
        relation_type: 关系类型，如 "父亲"、"朋友"、"同事"
        owner_id: 当前用户 ID
        through_person_id: 通过谁认识的（可选）
        note: 关系备注（可选）

    Returns:
        {"relation_id": "r_xxx", "from": "p_xxx", "to": "p_yyy", "type": "朋友"}
    """
    # TO-DO: 实现
    #
    # 提示：
    # 1. 先检查两个人物是否存在
    # 2. 检查是否已存在相同的关系（避免重复）
    # 3. Cypher 创建关系：
    #    MATCH (a:Person {id: $from_id}), (b:Person {id: $to_id})
    #    CREATE (a)-[:RELATION {
    #        id: $rel_id, type: $type, owner: $owner_id,
    #        through: $through, note: $note, created_at: datetime()
    #    }]->(b)
    #    RETURN ...
    from_person = get_person_detail(driver, from_person_id, owner_id)
    if from_person is None:
        raise ValueError(f"add_relation — 未找到指定用户,id={from_person_id}")

    to_person = get_person_detail(driver, to_person_id, owner_id)
    if to_person is None:
        raise ValueError(f"add_relation — 未找到指定用户,id={to_person_id}")

    #查询关系
    with driver.session() as session:
        rel_id = "r_" +generate_uuid()
        result = session.run("""
            MATCH (a:Person {id: $from_person_id, owner_id: $owner_id}),
                  (b:Person {id: $to_person_id, owner_id: $owner_id})
            CREATE (a)-[:RELATION {
               id: $rel_id, type: $type, owner: $owner_id,
               through: $through, note: $note, created_at: datetime()
            }]->(b)
            RETURN a.name AS from_name, b.name AS to_name, $rel_id AS relation_id
            """,
            from_person_id = from_person_id,
            to_person_id = to_person_id,
            rel_id = rel_id,
            type = relation_type,
            owner_id = owner_id,
            through = through_person_id,
            note = note
        )
        return result.single()


def delete_relation(driver: Driver, relation_id: str, owner_id: str) -> bool:
    """删除一条关系边。"""
    with driver.session() as session:
        # 删除一条关系边（仅限自己的数据）
        result = session.run(
            """MATCH (a:Person)-[r:RELATION {id: $id}]->(b:Person)
               WHERE r.owner = $owner_id
                 AND a.owner_id = $owner_id
                 AND b.owner_id = $owner_id
               DELETE r
               RETURN count(r) AS deleted""",
            id=relation_id,
            owner_id=owner_id,
        )
        if result.single()["deleted"] == 0:
            raise ValueError(f"关系不存在或无权删除: {relation_id}")
        return True


def update_relation(driver: Driver,relation_id: str,new_type:str, owner_id: str , new_note:str , new_through:str) -> bool:
    with driver.session() as session:
        # SET 也能改——type 只是一个普通属性，不是 Cypher 的关系类型。但你改了 type 之后，add_relation 里那份数据就跟它不一致了。
        result = session.run(
            """MATCH (a:Person)-[r:RELATION {id: $id}]->(b:Person)
            WHERE r.owner = $owner_id
              AND a.owner_id = $owner_id
              AND b.owner_id = $owner_id
            SET
            r.type = $new_type, r.note = $new_note, r.through = $new_through
            RETURN count(r) AS updated""" , id=relation_id, owner_id=owner_id,new_type=new_type,new_note = new_note,new_through=new_through)
        if result.single()["updated"] == 0:
            raise ValueError(f"关系不存在或无权修改: {relation_id}")
        return True

def get_relation_by_id(driver: Driver, relation_id: str, owner_id: str) -> dict | None:
    # 1. 查出旧边信息（拿到 from / to / 旧属性）
    with driver.session() as session:
        old = session.run(
            """MATCH (a:Person)-[r:RELATION {id: $rid}]->(b:Person)
               WHERE r.owner = $owner_id
                 AND a.owner_id = $owner_id
                 AND b.owner_id = $owner_id
               RETURN a.id AS from_id, b.id AS to_id,
                      r.type AS type, r.owner AS owner,
                      r.through AS through, r.note AS note""",
            rid=relation_id,
            owner_id=owner_id,
        ).single()
        if old is None:
            return None
        return old.data() if hasattr(old, "data") else dict(old)


def get_relation_path(
    driver: Driver, from_person_id: str, to_person_id: str,owner_id: str, max_depth: int = 7
) -> list[dict] | None:
    """
    查询两个人之间的最短关系路径。

    Args:
        driver: Neo4j 驱动
        from_person_id: 起点人物 ID（通常是当前用户）
        to_person_id: 终点人物 ID
        owner_id: 所属人物id，数据隔离
        max_depth: 最大搜索深度（跳数）

    Returns:
        路径列表，每一步包含 {from, to, relation_type, step}
        如果找不到路径返回 None

    实例：查询“我->三叔“
    [
        {"step": 1, "from": "我",  "to": "张大", "relation": "父亲", "note": "我爸"},
        {"step": 2, "from": "张大", "to": "张三", "relation": "弟弟", "note": None},
    ]
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH path = shortestPath(
                (a:Person {id: $from_id})-[r:RELATION*1..""" + str(max_depth) + """]-(b:Person {id: $to_id})
            )
            WHERE a.owner_id = $owner_id
              AND b.owner_id = $owner_id
              AND ALL(node IN nodes(path) WHERE node.owner_id = $owner_id)
              AND ALL(rel IN relationships(path) WHERE rel.owner = $owner_id)
            RETURN path
            """,
            from_id=from_person_id,
            to_id=to_person_id,
            owner_id = owner_id
        )

        record = result.single()
        if record is None:
            return None

        # 解析路径：path 里有交替的节点和关系
        # 结构：node1 → rel1 → node2 → rel2 → node3 → ...
        path = record["path"]
        steps = []

        for i, rel in enumerate(path.relationships):
            forward = (path.nodes[i]["id"] == rel.start_node["id"])
            steps.append({
                "step": i + 1,
                "from": path.nodes[i]["name"],
                "to": path.nodes[i + 1]["name"],
                "relation": rel["type"],
                "direction": "forward" if forward else "reverse",
                "note": rel.get("note"),
            })

        return steps


def get_person_detail(driver: Driver, person_id: str,owner_id: str) -> dict | None:
    """
    获取一个人的完整信息：属性 + 所有直接关系。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID
        owner_id: 所属ID，数据隔离

    Returns:
        {
            "person": {...},
            "relations": [{"to": {...}, "type": "朋友", "note": "..."}, ...]
        }
    """
    with driver.session() as session:
        check = session.run(
            """
            MATCH (p:Person {id: $person_id})
            WHERE p.owner_id = $owner_id
            OPTIONAL MATCH (p)-[r:RELATION]-(other:Person)
            WHERE r.owner = $owner_id AND other.owner_id = $owner_id
            RETURN p {.*} AS person,
               [rel IN collect(DISTINCT {
                   relation_id: r.id, type: r.type, note: r.note,
                   through: r.through, other_name: other.name, other_id: other.id
               }) WHERE rel.relation_id IS NOT NULL] AS relations
            """
            ,
            person_id=person_id,owner_id=owner_id)
        check_result = check.single()
        if check_result is None:
            return None
        else:
            return {"person": check_result["person"], "relations": check_result["relations"]}

def list_person_relations(
    driver: Driver, person_id: str, owner_id: str, relation_type: str | None = None):
    """
    列出某人的所有直接关系。

    Args:
        driver: Neo4j 驱动
        person_id: 人物 ID
        relation_type: 可选筛选，只返回特定类型

    Returns:
        关系列表
    """
    # 根据是否传了 relation_type，选不同的 Cypher
    if relation_type:
        cypher = """
                MATCH (p:Person {id: $id})-[r:RELATION {type: $type}]-(other:Person)
                WHERE p.owner_id = $owner_id
                  AND other.owner_id = $owner_id
                  AND r.owner = $owner_id
                RETURN r.id AS relation_id, r.type AS type,
                       other.name AS person_name, other.id AS person_id,
                       r.note AS note
            """
        params = {"id": person_id, "type": relation_type, "owner_id": owner_id}
    else:
        cypher = """
                MATCH (p:Person {id: $id})-[r:RELATION]-(other:Person)
                WHERE p.owner_id = $owner_id
                  AND other.owner_id = $owner_id
                  AND r.owner = $owner_id
                RETURN r.id AS relation_id, r.type AS type,
                       other.name AS person_name, other.id AS person_id,
                       r.note AS note
            """
        params = {"id": person_id,"owner_id":owner_id}

    with driver.session() as session:
        result = session.run(cypher, **params)
        return result.data()  # data() 返回所有行，每行是一个 dict


def person_exists(driver: Driver, person_id: str, owner_id: str) -> bool:
    """检查人物是否存在。"""
    return get_person_detail(driver, person_id, owner_id) is not None

if __name__ == "__main__":
    driver = get_neo4j_driver()
    owner_id = "1"

    #p_47a7b055
    # map = dict()
    # map["name"] = "朱珠"
    # map["gender"] = "女"
    # map["birth_year"] = "1998-01-05"
    # map["occupation"] = "上市公司CEO"
    # map["education"] = "本科"
    # map["hobbies"] = ["电视剧","游泳"]
    # map["personality"] = "自信,随和"
    # map["tags"] = ["年轻","漂亮"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    # map = dict()
    # map["occupation"] = "CEO"
    # map["education"] = "研究生"
    # update_person(driver  ,"p_8b8b9e1a" , map)

    # delete_person(driver , "p_8b8b9e1a")

    #p_a8423730
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
    # create_person(driver, map, "1")

    # p_65d18bce
    # map = dict()
    # map["name"] = "段汶狄"
    # map["gender"] = "男"
    # map["birth_year"] = "1998-05-15"
    # map["occupation"] = "高级程序员"
    # map["education"] = "本科"
    # map["hobbies"] = ["羽毛球","健身"]
    # map["personality"] = "幽默,随和"
    # map["tags"] = ["强壮","精干"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    #p_e2997a7d
    # map = dict()
    # map["name"] = "林建强"
    # map["gender"] = "男"
    # map["birth_year"] = "1960-09-15"
    # map["occupation"] = "个体户"
    # map["education"] = "高中"
    # map["hobbies"] = ["钓鱼","开车"]
    # map["personality"] = "善良,随和"
    # map["tags"] = ["老实","文化人"]
    # map["note"] = ""
    # create_person(driver , map , "1")

    #p_e39afa55
    # map = dict()
    # map["name"] = "林建伟"
    # map["gender"] = "男"
    # map["birth_year"] = "1963-10-25"
    # map["occupation"] = "厂长"
    # map["education"] = "高中"
    # map["hobbies"] = ["摄影", "旅游"]
    # map["personality"] = "传统"
    # map["tags"] = ["商人", "精明"]
    # map["note"] = ""
    # create_person(driver, map, "1")

    #p_c96a3986
    # map = dict()
    # map["name"] = "林翰星"
    # map["gender"] = "男"
    # map["birth_year"] = "1997-09-25"
    # map["occupation"] = "个体"
    # map["education"] = "高中"
    # map["hobbies"] = ["赚钱", "旅游"]
    # map["personality"] = "传统"
    # map["tags"] = ["规矩", "勤勉"]
    # map["note"] = ""
    # create_person(driver, map, "1")

    # 建立关系
    # print(add_relation(driver , "p_47a7b055" , "p_a8423730" , "夫妻",owner_id))
    # print(add_relation(driver, "p_47a7b055", "p_65d18bce", "弟弟", owner_id))

    # print(add_relation(driver, "p_a8423730", "p_e2997a7d", "父亲", owner_id))
    # print(add_relation(driver, "p_e2997a7d", "p_e39afa55", "弟弟", owner_id))
    # print(add_relation(driver, "p_e39afa55", "p_c96a3986", "儿子", owner_id))

    #查询关系
    # print(find_person_exact(driver , "朱",owner_id))
    # print(find_person_fuzzy(driver, "朱", owner_id , 1))
    # print(get_relation_path(driver, "p_65d18bce","p_a8423730",5))
    # print(get_relation_path(driver, "p_a8423730", "p_65d18bce", 5))

    # print(get_relation_path(driver, "p_47a7b055", "p_c96a3986", 5))
