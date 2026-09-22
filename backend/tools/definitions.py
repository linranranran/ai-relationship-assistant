# ============================================================
# Tool 定义（OpenAI Function Calling 格式）
# 这些定义会传给 LLM，让它知道有哪些工具、每个工具的参数
# ============================================================

ADD_PERSON_TOOL = {
    "type": "function",
    "function": {
        "name": "add_person",
        "description": "在用户的关系网中新增一个人物。如果人物可能已存在，先调用 find_person 确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "人物姓名"},
                "gender": {
                    "type": "string",
                    "enum": ["男", "女", "未知"],
                    "description": "性别，能从称呼推断就填，否则填未知",
                },
                "birth_year": {"type": "integer", "description": "出生年份，如 1985。不知道就不填"},
                "occupation": {"type": "string", "description": "职业/行业，如'金融'、'教师'"},
                "education": {"type": "string", "description": "毕业院校或学历"},
                "hobbies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "爱好标签列表，如 ['钓鱼', '茅台', '高尔夫']",
                },
                "personality": {"type": "string", "description": "性格特点，如'开朗健谈'、'内向严谨'"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "其他自定义标签"},
                "note": {"type": "string", "description": "备注信息，不属于以上字段的信息放这里"},
                "photo_url": {"type": "string", "description": "照片的临时路径或 URL"},
            },
            "required": ["name"],
        },
    },
}

UPDATE_PERSON_TOOL = {
    "type": "function",
    "function": {
        "name": "update_person",
        "description": "更新一个已有人物的信息。只传需要修改的字段，没传的字段保持不变。",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "人物 ID，通过 find_person 获取"},
                "name": {"type": "string", "description": "新姓名"},
                "gender": {"type": "string", "enum": ["男", "女", "未知"]},
                "birth_year": {"type": "integer"},
                "occupation": {"type": "string"},
                "education": {"type": "string"},
                "hobbies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "完整爱好列表。如果用户说'他还喜欢XX'，合并已有爱好后传入",
                },
                "personality": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
            "required": ["person_id"],
        },
    },
}

DELETE_PERSON_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_person",
        "description": "删除一个人物及其所有关系。⚠️ 不可逆，执行前必须向用户确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "要删除的人物 ID"},
            },
            # confirmed 不暴露给模型；只能由 request_confirmation 节点注入。
            "required": ["person_id"],
        },
    },
}

ADD_RELATION_TOOL = {
    "type": "function",
    "function": {
        "name": "add_relation",
        "description": "在两个人之间建立关系。支持亲属关系、社交关系等。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_person_id": {"type": "string", "description": "起点人物 ID"},
                "to_person_id": {"type": "string", "description": "终点人物 ID"},
                "relation_type": {
                    "type": "string",
                    "description": "关系类型：父亲/母亲/哥哥/弟弟/姐姐/妹妹/儿子/女儿/丈夫/妻子/爷爷/奶奶/外公/外婆/叔叔/伯伯/舅舅/姑姑/姨妈/堂哥/表哥/堂姐/表姐/侄子/侄女/外甥/外甥女/朋友/同事/同学/邻居/客户/导师/学生/老板/下属/合伙人",
                },
                "through_person_id": {"type": "string", "description": "通过谁认识的，可选"},
                "note": {"type": "string", "description": "关系备注，可选"},
            },
            "required": ["from_person_id", "to_person_id", "relation_type"],
        },
    },
}

UPDATE_RELATION_TOOL = {
    "type": "function",
    "function": {
        "name": "update_relation",
        "description": "修改已有的关系类型或备注。",
        "parameters": {
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系边 ID"},
                "new_relation_type": {"type": "string", "description": "新关系类型"},
                "note": {"type": "string", "description": "新备注"},
            },
            "required": ["relation_id"],
        },
    },
}

DELETE_RELATION_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_relation",
        "description": "删除两个人之间的关系。不可逆，执行前必须向用户确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "relation_id": {"type": "string", "description": "关系边 ID"},
            },
            "required": ["relation_id"],
        },
    },
}

FIND_PERSON_TOOL = {
    "type": "function",
    "function": {
        "name": "find_person",
        "description": "在用户关系网中查找人物。支持精确姓名和模糊搜索。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，姓名或别名"},
                "search_mode": {
                    "type": "string",
                    "enum": ["exact", "fuzzy", "semantic"],
                    "description": "exact=精确姓名, fuzzy=模糊姓名, semantic=语义搜索",
                },
                "limit": {"type": "integer", "description": "返回数量上限，默认 5"},
            },
            "required": ["query"],
        },
    },
}

QUERY_RELATION_PATH_TOOL = {
    "type": "function",
    "function": {
        "name": "query_relation_path",
        "description": "查询当前用户到目标人物之间的完整关系路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "target_person_id": {"type": "string", "description": "目标人物 ID"},
                "max_depth": {"type": "integer", "description": "最大搜索深度，默认 7"},
            },
            "required": ["target_person_id"],
        },
    },
}

GET_KINSHIP_TITLE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_kinship_title",
        "description": "根据关系路径计算当前用户应该怎么称呼目标人物。返回如'三叔'、'四舅'、'表姑'。",
        "parameters": {
            "type": "object",
            "properties": {
                "target_person_id": {"type": "string", "description": "目标人物 ID"},
                "relation_path": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "关系路径，如 ['父亲', '弟弟'] 表示父亲的弟弟",
                },
            },
            "required": ["target_person_id"],
        },
    },
}

GET_PERSON_DETAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "get_person_detail",
        "description": "获取一个人的完整信息：属性、所有关系、关系路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "人物 ID"},
            },
            "required": ["person_id"],
        },
    },
}

LIST_RELATIONS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_relations",
        "description": "列出某个人物的所有直接关系。如'张三认识哪些人'。",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string", "description": "人物 ID"},
                "relation_type": {"type": "string", "description": "可选筛选，只返回特定类型"},
            },
            "required": ["person_id"],
        },
    },
}

# 所有工具定义列表（会传给 LLM）
ALL_TOOL_DEFINITIONS = [
    ADD_PERSON_TOOL,
    UPDATE_PERSON_TOOL,
    DELETE_PERSON_TOOL,
    ADD_RELATION_TOOL,
    UPDATE_RELATION_TOOL,
    DELETE_RELATION_TOOL,
    FIND_PERSON_TOOL,
    QUERY_RELATION_PATH_TOOL,
    GET_KINSHIP_TITLE_TOOL,
    GET_PERSON_DETAIL_TOOL,
    LIST_RELATIONS_TOOL,
]
