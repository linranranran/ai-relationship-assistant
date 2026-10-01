"""给当前 .env 指向的 Neo4j 创建一份可重复执行的隔离演示数据。

运行：python scripts/seed_debug_neo4j.py
只核对：python scripts/seed_debug_neo4j.py --verify-only

账号和各人物 ID 保存在被 Git 忽略的 data/neo4j-debug-session.json。
脚本只创建带 debug_dataset 标记的数据，不删除、覆盖其他用户的数据。
"""

import argparse
import json
import logging
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.auth import authenticate_user, hash_password  # noqa: E402
from backend.config import NEO4J_URI  # noqa: E402
from backend.db.neo4j_client import (  # noqa: E402
    find_person_fuzzy,
    get_neo4j_driver,
    get_relation_path,
)
from backend.tools.kinship import get_kinship_title  # noqa: E402


DATASET = "relation-agent-debug-family-v1"
MANIFEST = ROOT / "data" / "neo4j-debug-session.json"

# 键是脚本内部的稳定代号；每个人的真实 id 由本地 manifest 保存，重复运行不变。
PEOPLE = {
    "father": {"name": "张建国", "gender": "男", "birth_year": 1970, "occupation": "退休教师", "hobbies": ["下棋", "钓鱼"], "tags": ["父系亲属"]},
    "mother": {"name": "李梅", "gender": "女", "birth_year": 1972, "occupation": "会计", "hobbies": ["园艺"], "tags": ["母系亲属"]},
    "uncle": {"name": "张建民", "gender": "男", "birth_year": 1974, "occupation": "工程师", "hobbies": ["摄影"], "tags": ["父系亲属"]},
    "paternal_cousin": {"name": "张浩", "gender": "男", "birth_year": 1996, "occupation": "产品经理", "hobbies": ["篮球"], "tags": ["堂兄"]},
    "paternal_aunt": {"name": "张晓兰", "gender": "女", "birth_year": 1976, "occupation": "医生", "hobbies": ["徒步"], "tags": ["父系亲属"]},
    "female_cousin": {"name": "陈雪", "gender": "女", "birth_year": 2001, "occupation": "设计师", "hobbies": ["画画"], "tags": ["表妹"]},
    "grandmother": {"name": "王秀兰", "gender": "女", "birth_year": 1948, "occupation": "退休", "hobbies": ["种花"], "tags": ["母系亲属"]},
    "friend": {"name": "刘洋", "gender": "男", "birth_year": 1998, "occupation": "Java开发", "hobbies": ["钓鱼", "羽毛球"], "tags": ["朋友"]},
    "colleague": {"name": "周宁", "gender": "女", "birth_year": 1994, "occupation": "销售主管", "hobbies": ["跑步"], "tags": ["同事"]},
    "friend_colleague": {"name": "韩梅", "gender": "女", "birth_year": 1997, "occupation": "销售", "hobbies": ["烘焙"], "tags": ["朋友", "同事"]},
    "customer_a": {"name": "赵明", "gender": "男", "birth_year": 1981, "occupation": "制造企业负责人", "hobbies": ["高尔夫"], "tags": ["客户"], "note": "虚构客户；关注交付周期和售后响应"},
    "customer_b": {"name": "孙薇", "gender": "女", "birth_year": 1987, "occupation": "采购经理", "hobbies": ["旅行"], "tags": ["客户"], "note": "虚构客户；由韩梅介绍，关注方案预算"},
}

# 关系方向按图数据库现有约定：A -[父亲]-> B 表示 B 是 A 的父亲。
# 堂兄路径：我 -父亲-> 张建国 -弟弟-> 张建民 <-父亲- 张浩。
RELATIONS = {
    "self_father": ("self", "father", "父亲", None, None),
    "self_mother": ("self", "mother", "母亲", None, None),
    "father_uncle": ("father", "uncle", "弟弟", None, None),
    "cousin_uncle": ("paternal_cousin", "uncle", "父亲", None, None),
    "father_aunt": ("father", "paternal_aunt", "妹妹", None, None),
    "aunt_daughter": ("paternal_aunt", "female_cousin", "女儿", None, None),
    "mother_grandmother": ("mother", "grandmother", "母亲", None, None),
    "self_friend": ("self", "friend", "朋友", "大学同学", None),
    "self_colleague": ("self", "colleague", "同事", "销售团队", None),
    "self_han_friend": ("self", "friend_colleague", "朋友", "认识多年", None),
    "self_han_colleague": ("self", "friend_colleague", "同事", "同一项目组", None),
    "self_customer_a": ("self", "customer_a", "客户", "首次沟通后待跟进", None),
    "self_customer_b": ("self", "customer_b", "客户", "韩梅介绍的客户", "friend_colleague"),
    "customers_colleague": ("customer_a", "customer_b", "同事", "同一家虚构企业", None),
}


def load_or_create_manifest(driver) -> dict:
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("neo4j_uri") != NEO4J_URI or manifest.get("dataset") != DATASET:
            raise ValueError("演示账号属于另一 Neo4j 地址或数据集，请勿直接复用")
        return manifest

    # 生成一个合法的虚构手机号，并确认没有占用现有账号。
    for _ in range(20):
        phone = "199" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        records, _, _ = driver.execute_query(
            "MATCH (u:User {phone_number: $phone}) RETURN count(u) AS total",
            {"phone": phone},
        )
        if records[0]["total"] == 0:
            break
    else:
        raise RuntimeError("无法生成空闲演示手机号")

    manifest = {
        "dataset": DATASET,
        "neo4j_uri": NEO4J_URI,
        "phone": phone,
        "password": secrets.token_urlsafe(18),
        "user_id": str(uuid4()),
        "person_ids": {key: "p_" + uuid4().hex[:12] for key in ("self", *PEOPLE)},
        "relation_ids": {key: "r_" + uuid4().hex[:12] for key in RELATIONS},
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def seed_transaction(tx, manifest: dict) -> None:
    owner_id = manifest["user_id"]
    existing = tx.run(
        "MATCH (u:User) WHERE u.user_id = $uid OR u.phone_number = $phone OR u.debug_dataset = $dataset "
        "RETURN u.user_id AS uid, u.phone_number AS phone, u.debug_dataset AS dataset",
        uid=owner_id, phone=manifest["phone"], dataset=DATASET,
    ).data()
    if any(row != {"uid": owner_id, "phone": manifest["phone"], "dataset": DATASET} for row in existing):
        raise ValueError("Neo4j 中存在同 ID、同手机号或同演示标记的其他账号，已停止写入")

    tx.run(
        """MERGE (u:User {user_id: $uid})
           ON CREATE SET u.phone_number = $phone,
                         u.phone_numbers = [$phone],
                         u.hashed_password = $hashed,
                         u.self_person_id = $self_id,
                         u.created_at = $created_at,
                         u.debug_dataset = $dataset
           MERGE (me:Person {id: $self_id, owner_id: $uid})
           ON CREATE SET me.name = '我', me.gender = '未知', me.birth_year = 1998,
                         me.occupation = '软件开发', me.hobbies = ['羽毛球'],
                         me.tags = ['演示账号'], me.is_self = true,
                         me.created_at = datetime(), me.debug_dataset = $dataset
           MERGE (u)-[:HAS_IDENTITY]->(me)""",
        uid=owner_id, phone=manifest["phone"], hashed=hash_password(manifest["password"]),
        self_id=manifest["person_ids"]["self"],
        created_at=datetime.now(timezone.utc).isoformat(),
        dataset=DATASET,
    ).consume()

    for key, attributes in PEOPLE.items():
        tx.run(
            """MERGE (p:Person {id: $id, owner_id: $owner_id})
               ON CREATE SET p += $attributes, p.created_at = datetime(),
                             p.debug_dataset = $dataset""",
            id=manifest["person_ids"][key], owner_id=owner_id,
            attributes={"education": "", "personality": "", "note": "", **attributes},
            dataset=DATASET,
        ).consume()

    for key, (source, target, relation_type, note, through) in RELATIONS.items():
        tx.run(
            """MATCH (a:Person {id: $source_id, owner_id: $owner_id}),
                         (b:Person {id: $target_id, owner_id: $owner_id})
               MERGE (a)-[r:RELATION {id: $relation_id, owner: $owner_id}]->(b)
               ON CREATE SET r.type = $relation_type, r.note = $note,
                             r.through = $through_id, r.created_at = datetime(),
                             r.debug_dataset = $dataset""",
            source_id=manifest["person_ids"][source],
            target_id=manifest["person_ids"][target],
            relation_id=manifest["relation_ids"][key], owner_id=owner_id,
            relation_type=relation_type, note=note,
            through_id=manifest["person_ids"].get(through) if through else None,
            dataset=DATASET,
        ).consume()


def verify(driver, manifest: dict) -> None:
    owner_id = manifest["user_id"]
    account = authenticate_user(driver, manifest["phone"], manifest["password"])
    if account["user_id"] != owner_id or account["self_person_id"] != manifest["person_ids"]["self"]:
        raise AssertionError("演示账号与本人节点绑定不一致")

    people, _, _ = driver.execute_query(
        "MATCH (p:Person {owner_id: $owner_id, debug_dataset: $dataset}) RETURN count(p) AS total",
        {"owner_id": owner_id, "dataset": DATASET},
    )
    relations, _, _ = driver.execute_query(
        "MATCH ()-[r:RELATION {owner: $owner_id, debug_dataset: $dataset}]->() RETURN count(r) AS total",
        {"owner_id": owner_id, "dataset": DATASET},
    )
    if people[0]["total"] != len(PEOPLE) + 1 or relations[0]["total"] != len(RELATIONS):
        raise AssertionError("演示人物或关系数量不符")

    path = get_relation_path(
        driver, manifest["person_ids"]["self"],
        manifest["person_ids"]["paternal_cousin"], owner_id, 4,
    )
    expected = [("父亲", "forward"), ("弟弟", "forward"), ("父亲", "reverse")]
    if [(step["relation"], step["direction"]) for step in (path or [])] != expected:
        raise AssertionError(f"堂兄关系路径与预期不符：{path}")
    title = get_kinship_title(relation_path=path)
    if not title.get("success") or title.get("data", {}).get("title") != "堂哥":
        raise AssertionError(f"堂兄称谓结果与预期不符：{title}")
    if len(find_person_fuzzy(driver, "张", owner_id, 10)) < 4:
        raise AssertionError("模糊查询缺少多个张姓人物")

    print(f"Neo4j 演示数据就绪：{people[0]['total']} 个人物，{relations[0]['total']} 条关系")
    print("堂兄路径：我 -> 父亲 -> 叔叔 <- 堂兄；称谓：堂哥")
    print(f"演示账号与 ID 清单：{MANIFEST}")


def main() -> None:
    # 空库首次写入时，Neo4j 对尚不存在的标签和属性会发出大量提示；
    # 这不影响种子数据，实际 Cypher 错误仍会抛出并终止脚本。
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true", help="只核对现有演示数据")
    args = parser.parse_args()
    with get_neo4j_driver() as driver:
        if args.verify_only and not MANIFEST.exists():
            raise FileNotFoundError(f"找不到演示账号清单：{MANIFEST}")
        manifest = load_or_create_manifest(driver)
        if not args.verify_only:
            with driver.session() as session:
                session.execute_write(seed_transaction, manifest)
        verify(driver, manifest)


if __name__ == "__main__":
    main()
