"""Direct 的写入策略：明确新增可自动记录，修改、删除及存疑新增仍确认。

这里不拆 goal、不调用模型，也不使用历史摘要或 Tool 文本授予写权限。
仅对白名单中的常见事实表达检查本次用户原文；匹配不到就保守地请求确认。
它不是完整的自然语言授权证明。扩展表达规则时必须同时检查反例，不能只做
“参数值出现在任意上下文里”的子串匹配，否则会把查询或其他人的资料写错。
"""
import re

from backend.agent.direct_runtime import DIRECT_WRITE_TOOLS, direct_bindings


RELATION_ALIASES = {
    "好朋友": "朋友", "好友": "朋友", "爸爸": "父亲", "妈妈": "母亲",
    "老婆": "妻子", "老公": "丈夫",
}
RELATIONS = (
    "好朋友", "好友", "朋友", "客户", "同事", "合作伙伴", "供应商",
    "亲戚", "父亲", "母亲", "爸爸", "妈妈", "妻子", "老婆", "丈夫", "老公",
    "男朋友", "女朋友", "哥哥", "姐姐", "弟弟", "妹妹", "叔叔", "阿姨",
    "老板", "上司", "下属", "老师", "学生",
)
RELATION_PATTERN = "|".join(RELATIONS)
# 新工具除了进入模型目录，还必须在执行授权策略中明确分类。
# 沟通准备上下文仅读取 owner 下的档案/聊天，不产生写入，因此不需要写操作确认。
# 漏加这里会被下面“未知工具保守确认”拦截，导致模型查到客户后无法继续准备。
READ_TOOLS = frozenset({"find_person", "get_person_detail", "list_relations",
                        "query_relation_path", "get_kinship_title", "get_sales_preparation_context"})
# 否定、查询、假设和转述不能作为免确认的事实来源；出现这些表达就回退确认。
UNCERTAIN = re.compile(
    r"如果|假如|假设|比如|例如|示例|是否|是不是|可能|也许|据说|听说|好像|大概|似乎|貌似|应该是|记错|不太确定|不清楚|"
    r"不要|先别|别(?:新增|添加|记录)|不必|不用|不能|不是|并非|不喜欢|不确定|请勿|禁止|"
    r"查询|查一下|查查|查找|搜索|告诉我|他说|她说|对方说|客户说|转述|原文|引用"
)
SCOPE_BREAK = re.compile(r"另外|另一个|还有|以及|分别|他们|她们|至于")
# 防止多行示例/客户聊天中的“我有个朋友”被误当成本轮用户本人的事实。
REPORTED_CONTEXT = re.compile(r"如果|假如|假设|比如|例如|示例|据说|听说|转述|原文|引用|聊天记录|"
                              r"对方说|客户说|他说|她说|转发|粘贴|发来的|下面是|邮件|文档")
NEGATED_RECORD = re.compile(r"不要|先别|请勿|禁止|(?:不|别)(?:要|用|必|能|许|需要)?"
                            r"(?:新增|添加|记录|录入|保存|创建|建立|记住)")
# 更正可能在人物属性范围之外，不能在截取 scope 时丢掉它，再批准前面的旧事实。
CORRECTION_CONTEXT = re.compile(r"记错|说错|不对|更正|纠正|不叫|不是|并非|不喜欢|"
                                r"不确定|不太确定|不清楚|其实不")
ATTRIBUTE_PREFIX = re.compile(
    r"(?:(?:他|她|其)(?:的)?(?:是|职业|性别|学历|爱好|喜欢|性格|兴趣|工作|出生|生日|毕业|担任|从事|住在|在)|"
    r"职业|性别|学历|爱好|喜欢|性格|兴趣|工作地点|出生|生于|生日|备注|标签|标记|毕业于|担任|从事|住在)"
)
OCCUPATION_ENDING = re.compile(r"(?:销售|业务员|负责人|经理|主管|总监|工程师|程序员|开发者|医生|护士|"
                               r"律师|会计|教师|老师|学生|设计师|司机|老板|客服|员工|服务员|CEO|CTO|COO)$", re.I)
ATTRIBUTE_MARKERS = {
    "occupation": r"职业(?:是|为)?|工作(?:是|为)|(?:他|她|其)是|担任|从事",
    "education": r"学历(?:是|为)?|毕业于",
    "personality": r"性格(?:是|为)?|为人",
    "hobbies": r"爱好(?:是|为)?|喜欢|兴趣(?:是|为)?",
    "tags": r"标签(?:是|为)?|标记(?:是|为)?",
    "gender": r"性别(?:是|为)?",
}


def requires_direct_confirmation(state: dict, call: dict) -> bool:
    """执行器传入解析后的真实参数；已有确认、查重、ID 和 owner 校验另行保留。

    只新增人物/关系可以走免确认规则；修改现有资料或删除始终逐次确认。
    一个新增含任意无法归属到用户原文的属性，就确认整个操作，不悄悄丢字段。
    """
    tool = call["tool"]
    if tool in READ_TOOLS:
        return False
    if tool not in DIRECT_WRITE_TOOLS:
        return True  # 新工具未分类时不能默认为只读；需补策略和展示模板才可执行。
    bindings = direct_bindings(state)
    if tool == "add_person":
        return not _explicit_person(state, call["args"])
    if tool == "add_relation":
        return not _explicit_relation(state, call["args"], bindings)
    return True


def _current_user_texts(state: dict) -> list[str]:
    """最新用户补充优先，禁止用原文的旧授权覆盖用户后来的更正或撤回。

    只有最新补充能独立证明新增范围时继续免确认；简短补充无法独立证明时
    就确认，不猜测原文中哪些字段仍获授权。历史记忆/模型/Tool 文本均不使用。
    """
    answers = [text for text in state.get("direct_clarifications", []) if isinstance(text, str) and text.strip()]
    text = answers[-1] if answers else state.get("user_input", "")
    return [text] if isinstance(text, str) and text.strip() else []


def _cannot_authorize_create(text: str) -> bool:
    """先检查完整原文的撤回/更正，再截取属性范围；开头的问句不能当作事实。

    例如“我有个朋友叫孙远，我记错了”必须确认；不能只读前半句自动写入。
    后面的独立查询不影响前面明确的新增，因此这里只额外检查首句的问号。
    """
    return bool(REPORTED_CONTEXT.search(text) or NEGATED_RECORD.search(text)
                or CORRECTION_CONTEXT.search(text)
                or re.match(r"[^。；;\n！？!?]*[？?]", text))


def _person_sources(state: dict, name: str):
    """仅接收明确新增指令或当前用户的事实介绍；姓名要求完整匹配。

    例：“我有个好朋友叫孙远，他是职业销售”。后续明确的他/她属性句可以
    沿用同一人物；遇到另一个主体或另一任务即停止，避免串用其他人的属性。
    """
    escaped = re.escape(name)
    # 空格可能仍是姓名的一部分（John Smith），不能把 John 当完整姓名放开。
    boundary = r"(?=$|[，,。；;：:]|\s+(?=$|[，,。；;：:]))"
    relation = rf"(?P<relation>{RELATION_PATTERN})"
    patterns = [
        rf"我(?:有|认识)(?:了)?(?:一个|一位|个|位)?{relation}(?:叫做|叫|名叫|名字是|姓名是)\s*{escaped}{boundary}",
        rf"我的{relation}(?:叫做|叫|名叫|名字是)?\s*{escaped}{boundary}",
        rf"{escaped}(?:是|为)我(?:的)?{relation}{boundary}",
        rf"我(?:和|与){escaped}(?:是|为){relation}{boundary}",
        rf"(?:请帮我|帮我|请|麻烦你)?(?:新增|添加|记录|录入|创建|记住)(?:一个|一位|个|位)?"
        rf"(?:人物|联系人|客户|朋友)?(?:叫做|叫|名叫|名为|：|:)?\s*{escaped}{boundary}",
    ]
    for text in _current_user_texts(state):
        # 用户说“不记录孙远。孙远是我的朋友”时不能忽略前句的禁止。
        if _cannot_authorize_create(text):
            continue
        sentences = [part.strip() for part in re.split(r"[。；;\n！？!?]", text) if part.strip()]
        if not sentences:
            continue
        # 只对白名单开头的陈述放开免确认，不从任意前言/多行粘贴中拾取“我”。
        # 多主体等未覆盖表达走确认；不能拿其他人的职业填到这个人物身上。
        match = next((m for pattern in patterns if (m := re.match(pattern, sentences[0]))), None)
        if match is None:
            continue
        scope = sentences[0][:match.end()]
        body = sentences[0][match.end():] + "，" + "，".join(sentences[1:])
        for clause in re.split(r"[，,]", body):
            clause = clause.strip()
            if not clause:
                continue
            if SCOPE_BREAK.search(clause) or UNCERTAIN.search(clause) or not ATTRIBUTE_PREFIX.match(clause):
                break
            scope += "，" + clause
        if not UNCERTAIN.search(scope):
            yield scope, match.groupdict().get("relation")


def _normalized(value) -> str:
    return re.sub(r"\s+", "", str(value)).casefold()


def _property_matches(scope: str, field: str, value) -> bool:
    """属性必须属于已定位人物的属性句；职业比较完整值，不截成更宽泛的职业。"""
    if field == "note":
        # 备注保留原文，例如“工作地点在浙江省嘉兴市”；改写或推测内容需确认。
        return bool(value) and _normalized(value) in _normalized(scope)
    if field == "birth_year":
        # 与职业一样只看人物本身的属性句，不读取备注里其他人的出生年份。
        expression = (rf"(?:(?:他|她|其)(?:的)?)?(?:生日(?:是|为)?|出生年份(?:是|为)?|出生于|生于)"
                      rf"\s*{re.escape(str(value))}(?:年|(?=\D|$))")
        return any(re.match(expression, clause.strip()) for clause in re.split(r"[，,。；;]", scope))
    marker = ATTRIBUTE_MARKERS.get(field)
    if not marker:
        return False  # 照片等未实现归属规则的字段，不能默认免确认。
    fragments = []
    for clause in re.split(r"[，,。；;]", scope):
        clause = clause.strip()
        match = re.match(rf"(?:他|她|其)?(?:的)?(?:{marker})", clause)
        if match is None:
            continue  # 备注里面的“李四职业销售”不是孙远的职业字段。
        fragment = clause[match.end():].strip(" ：:")
        if field == "occupation":
            explicit_label = "职业" in match.group() or fragment.startswith("职业")
            fragment = re.sub(r"^(?:一个|一名|一位)?职业", "", fragment)
            if not explicit_label and "是" in match.group() and not OCCUPATION_ENDING.search(fragment):
                continue  # “他是北京人/销售的朋友”不能证明职业。
        fragments.append(_normalized(fragment))
    if isinstance(value, list):
        # 中文顿号列表要求完整值匹配；不能把“钓鱼”截成“钓”，也不漏掉一项。
        expected = {_normalized(item) for item in value}
        return bool(value) and any(expected == set(fragment.split("、")) for fragment in fragments)
    return _normalized(value) in fragments


def _explicit_person(state: dict, args: dict) -> bool:
    name = args.get("name")
    if not isinstance(name, str) or not name:
        return False
    for scope, _ in _person_sources(state, name):
        meaningful = {key: value for key, value in args.items()
                      if key not in {"name", "confirmed"} and value is not None and value != ""
                      and not (key == "gender" and value == "未知")
                      and not (key in {"hobbies", "tags"} and value == [])}
        if all(_property_matches(scope, key, value) for key, value in meaningful.items()):
            return True
    return False


def _relation_key(value: str) -> str:
    return RELATION_ALIASES.get(value, value)


def _explicit_relation(state: dict, args: dict, bindings: dict) -> bool:
    # 附加介绍人、备注等字段没有在此模板中验证，仍展示给用户确认。
    if args.get("through_person_id") or args.get("note"):
        return False
    source, target = args.get("from_person_id"), args.get("to_person_id")
    relation = args.get("relation_type", "")
    people = bindings["people"]
    self_id = state.get("self_person_id")
    person_id = target if self_id and source == self_id else (
        source if self_id and target == self_id and _relation_key(relation) == "朋友" else None
    )
    for name, ids in people.items():
        if person_id is None or len(ids) != 1 or person_id not in ids:
            continue
        if any(label and _relation_key(label) == _relation_key(relation)
               for _, label in _person_sources(state, name)):
            return True
    # “张三是李四的朋友”：唯一绑定后的关系双方、方向、类型都来自当前原文。
    sources = [name for name, ids in people.items() if len(ids) == 1 and source in ids and not name.startswith("@")]
    targets = [name for name, ids in people.items() if len(ids) == 1 and target in ids and not name.startswith("@")]
    for text in _current_user_texts(state):
        if UNCERTAIN.search(text) or _cannot_authorize_create(text):
            continue
        for left in sources:
            for right in targets:
                expression = rf"{re.escape(right)}(?:是|为){re.escape(left)}的{re.escape(relation)}(?=$|[\s，,。；;])"
                if re.match(expression, text.strip()):
                    return True
    return False
