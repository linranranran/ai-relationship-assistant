# ============================================================
# 亲属称谓 Tool
# ============================================================

from backend.tools.person import tool_response
from backend.services.llm import get_llm_client, chat
import logging

logger = logging.getLogger(__name__)


INVERSE_RELATION = {
    "父亲": "儿子", "母亲": "女儿",
    "儿子": "父亲", "女儿": "母亲",
    "哥哥": "弟弟", "弟弟": "哥哥",
    "姐姐": "妹妹", "妹妹": "姐姐",
    "丈夫": "妻子", "妻子": "丈夫",
    "伯伯": "侄子", "叔叔": "侄子", "姑姑": "侄子",
    "舅舅": "外甥", "姨妈": "外甥",
    # 对称关系：key == value
    "朋友": "朋友", "同事": "同事", "同学": "同学",
}


# 基础称谓规则表
# 键：关系路径用「的」连接；值：称谓
# 这是你后面要扩展的核心数据
KINSHIP_RULES = {
    # --- 直系 ---
    "父亲": "爸爸",
    "母亲": "妈妈",
    "儿子": "儿子",
    "女儿": "女儿",

    # --- 父系 ---
    "父亲的父亲": "爷爷",
    "父亲的母亲": "奶奶",
    "父亲的哥哥": "伯伯",
    "父亲的弟弟": "叔叔",
    "父亲的姐姐": "姑姑",
    "父亲的妹妹": "姑姑",
    "父亲的哥哥的儿子": "堂哥/堂弟",
    "父亲的哥哥的女儿": "堂姐/堂妹",
    "父亲的弟弟的儿子": "堂哥/堂弟",
    "父亲的弟弟的女儿": "堂姐/堂妹",
    "父亲的姐姐的儿子": "表哥/表弟",
    "父亲的姐姐的女儿": "表姐/表妹",
    "父亲的妹妹的儿子": "表哥/表弟",
    "父亲的妹妹的女儿": "表姐/表妹",

    # --- 母系 ---
    "母亲的父亲": "外公",
    "母亲的母亲": "外婆",
    "母亲的哥哥": "舅舅",
    "母亲的弟弟": "舅舅",
    "母亲的姐姐": "姨妈",
    "母亲的妹妹": "姨妈",
    "母亲的哥哥的儿子": "表哥/表弟",
    "母亲的哥哥的女儿": "表姐/表妹",
    "母亲的弟弟的儿子": "表哥/表弟",
    "母亲的弟弟的女儿": "表姐/表妹",
    "母亲的姐姐的儿子": "表哥/表弟",
    "母亲的姐姐的女儿": "表姐/表妹",
    "母亲的妹妹的儿子": "表哥/表弟",
    "母亲的妹妹的女儿": "表姐/表妹",

    # --- 兄弟姐妹 ---
    "哥哥": "哥哥",
    "弟弟": "弟弟",
    "姐姐": "姐姐",
    "妹妹": "妹妹",

    # --- 配偶 ---
    "丈夫": "老公",
    "妻子": "老婆",
}

# 年龄排序前缀
# 根据目标人物在兄弟/姐妹中的排行，添加"大"、"二"、"三"、"小"等
# 例如：父亲的弟弟排行第三 → "三叔"


def get_kinship_title(**kwargs) -> dict:
    """
    根据关系路径计算称呼。

    需要：
    - relation_path: 关系路径，格式为 [{"relation": "父亲", "direction": "forward"}, ...]

    Returns:
        {"title": "三叔", "confidence": "rule_match" | "llm_inferred", "explanation": "..."}
    """

    # TODO 2026-9-16发现还存在的问题：例如：A--父亲-->B--哥哥-->C<--父亲--D。用户提问A和D是什么关系，还需要比对A和D的年龄，谁大谁是哥哥

    relation_path = kwargs.get("relation_path", [])
    if not relation_path:
        return tool_response(msg="缺少 relation_path，无法计算称谓", success=False)

    birth_order = kwargs.get("birth_order")
    total_siblings = kwargs.get("total_siblings")

    # ── 第一步：方向归一化 ──
    # 把 reverse 的关系翻转成 forward 语义
    normalized = []
    for step in relation_path:
        rel_type = step["relation"]
        if step.get("direction") == "reverse":
            rel_type = INVERSE_RELATION.get(rel_type, rel_type)
        normalized.append(rel_type)

    # ── 第二步：拼成 "父亲的弟弟" 格式 ──
    path_str = "的".join(normalized)

    # ── 第三步：查规则表 ──
    title = KINSHIP_RULES.get(path_str)
    if title:
        # 加上年龄前缀
        if "/" in title:  # "堂哥/堂弟" 这种需要结合性别+排行
            title = _resolve_ambiguous_title(title, relation_path, kwargs)
        elif birth_order is not None:
            title = resolve_age_prefix(title, birth_order, total_siblings)

        return tool_response(msg="称谓计算成功", success=True, data={
            "title": title,
            "confidence": "rule_match",
            "path": path_str,
        })

    # ── 第四步：LLM 兜底推理 ──
    try:
        llm_client = get_llm_client()
        prompt = f"关系路径：我的{path_str}。请告诉我我应该怎么称呼这个人？只要回答称谓本身，不要解释。"
        response = chat(llm_client, "", [{"role": "user", "content": prompt}])
        title = response.strip().strip("。，\"\"''")

        if birth_order is not None:
            title = resolve_age_prefix(title, birth_order, total_siblings)

        return tool_response(msg="称谓计算成功（LLM推理）", success=True, data={
            "title": title,
            "confidence": "llm_inferred",
            "path": path_str,
        })
    except Exception as e:
        logger.error(f"LLM 称谓推理失败: {str(e)}")
        return tool_response(msg=f"无法计算称谓: {str(e)}", success=False)


def _resolve_ambiguous_title(title: str, relation_path: list, kwargs: dict) -> str:
    """
    处理模糊称谓，如 "堂哥/堂弟" → "堂哥"。
    需要结合目标人物的性别和相对年龄判断。
    当前策略：返回默认值（第一个选项）。
    """
    # 如果 Agent 提供了目标人物性别
    gender = kwargs.get("target_gender")
    if gender == "男" and "哥" in title:
        return title.split("/")[0]  # "堂哥/堂弟" → "堂哥"
    elif gender == "男" and "弟" in title:
        return title.split("/")[-1] if "/" in title else title
    elif gender == "女" and "姐" in title:
        return title.split("/")[0]
    elif gender == "女" and "妹" in title:
        return title.split("/")[-1] if "/" in title else title
    # 默认返回第一个
    return title.split("/")[0] if "/" in title else title



def resolve_age_prefix(title: str, birth_order: int | None = None, total_siblings: int | None = None) -> str:
    """
    根据年龄排行给称谓添加前缀。

    Args:
        title: 基础称谓，如 "叔叔"
        birth_order: 排行，1=最大
        total_siblings: 同辈兄弟姐妹总数（用于判断"最小"）

    Returns:
        "三叔"、"大舅"、"小姑" 等。如果不知道排行，返回原始称谓。

    Examples:
        >>> resolve_age_prefix("叔叔", 3, 4)     # 四个兄弟里排第三
        "三叔"
        >>> resolve_age_prefix("叔叔", 1)         # 排第一
        "大叔"
        >>> resolve_age_prefix("姑姑", 4, 4)      # 四个姐妹里排第四 = 最小的
        "小姑"
    """
    if birth_order is None:
        return title

    # 排行 → 中文前缀
    ORDER_PREFIX = {1: "大", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}

    # 如果总共 N 个兄弟/姐妹，且排行 = N，则用"小"而不是数字
    if total_siblings and birth_order == total_siblings and total_siblings > 2:
        prefix = "小"
    else:
        prefix = ORDER_PREFIX.get(birth_order, str(birth_order))

    # 叠字称谓去重：叔叔→叔、伯伯→伯、哥哥→哥、弟弟→弟
    # "三" + "叔叔" → "三叔"，而不是 "三叔叔"
    if len(title) == 2 and title[0] == title[1]:
        title = title[0]

    return f"{prefix}{title}"


if __name__ == "__main__":
    # ─── 测试 1：方向归一化 ───
    # 模拟：A→父亲→B→哥哥→C（顺箭头），D→父亲→C（逆箭头，即C的儿子是D）
    # 预期：路径归一化为 [父亲, 哥哥, 儿子]
    path_with_reverse = [
        {"step": 1, "from": "A", "to": "B", "relation": "父亲", "direction": "forward"},
        {"step": 2, "from": "B", "to": "C", "relation": "哥哥", "direction": "forward"},
        {"step": 3, "from": "D", "to": "C", "relation": "父亲", "direction": "reverse"},
    ]

    print("=" * 50)
    print("测试 1：方向归一化（含逆向关系）")
    result = get_kinship_title(relation_path=path_with_reverse)
    print(f"路径: 父亲 → 哥哥 → 父亲(逆)")
    print(f"结果: {result}")
    print()

    # ─── 测试 2：规则表直接命中 ───
    path_simple = [
        {"step": 1, "from": "我", "to": "张大", "relation": "父亲", "direction": "forward"},
        {"step": 2, "from": "张大", "to": "张三", "relation": "弟弟", "direction": "forward"},
    ]

    print("=" * 50)
    print("测试 2：规则表直接命中")
    result = get_kinship_title(relation_path=path_simple)
    print(f"路径: 父亲 → 弟弟")
    print(f"结果: {result}")
    print()

    # ─── 测试 3：规则表命中 + 年龄前缀 ───
    print("=" * 50)
    print("测试 3：叔叔 + 排行（三叔）")
    result = get_kinship_title(relation_path=path_simple, birth_order=3, total_siblings=4)
    print(f"路径: 父亲 → 弟弟, 排行=3/4")
    print(f"结果: {result}")
    print()

    # ─── 测试 4：最小排行用"小" ───
    print("=" * 50)
    print("测试 4：最小的叔叔（小叔）")
    result = get_kinship_title(relation_path=path_simple, birth_order=4, total_siblings=4)
    print(f"路径: 父亲 → 弟弟, 排行=4/4")
    print(f"结果: {result}")
    print()

    # ─── 测试 5：模糊称谓消歧（堂哥/堂弟 → 堂哥） ───
    path_cousin = [
        {"step": 1, "from": "我", "to": "张大", "relation": "父亲", "direction": "forward"},
        {"step": 2, "from": "张大", "to": "张二", "relation": "哥哥", "direction": "forward"},
        {"step": 3, "from": "张二", "to": "张堂", "relation": "儿子", "direction": "forward"},
    ]

    print("=" * 50)
    print("测试 5：性别消歧（堂哥/堂弟 → 堂哥）")
    result = get_kinship_title(relation_path=path_cousin, target_gender="男")
    print(f"路径: 父亲 → 哥哥 → 儿子, 性别=男")
    print(f"结果: {result}")
    print()

    # ─── 测试 6：直接关系（一步） ───
    path_one = [
        {"step": 1, "from": "我", "to": "妈妈", "relation": "母亲", "direction": "forward"},
    ]

    print("=" * 50)
    print("测试 6：直接关系")
    result = get_kinship_title(relation_path=path_one)
    print(f"路径: 母亲")
    print(f"结果: {result}")
    print()

    # ─── 测试 7：resolve_age_prefix 叠字处理 ───
    print("=" * 50)
    print("测试 7：resolve_age_prefix（叠字去重）")
    print(f'叔叔 + 三 → {resolve_age_prefix("叔叔", 3)}')
    print(f'伯伯 + 大 → {resolve_age_prefix("伯伯", 1)}')
    print(f'哥哥 + 二 → {resolve_age_prefix("哥哥", 2)}')
    print(f'姑姑 + 小 → {resolve_age_prefix("姑姑", 4, 4)}')
    print(f'舅舅 + 大 → {resolve_age_prefix("舅舅", 1)}')
    print()

    print("=" * 50)
    print("全部测试完成 ✅")