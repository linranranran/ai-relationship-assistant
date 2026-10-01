"""常见亲属称谓：供学习者逐步实现的独立教学骨架。

本文件不接入 backend，不创建数据库连接，不调用模型或其他服务。
五个业务函数均故意未实现；数据类型和常量用于先约定输入、输出。
完成并验证后，再考虑替换 backend/tools/kinship.py 的对应逻辑，
以及调整 backend/db/neo4j_client.py 的亲属候选查询。

统一存储语义：X --父亲--> Y 表示“Y 是 X 的父亲”。
存储方向与遍历方向分别记录；不能仅根据箭头反转一个称谓字符串。
owner_id 是数据归属标识，source_id 是人物标识，两者不可互换。

验收示例：A --父亲--> B --弟弟--> C <--父亲-- D。
从 A 走到 D 时，最后一步是 C 到 D，原始边仍然是 D 到 C。
这条边说明 C 为男性，不能说明 D 的性别；D 可能为儿子或女儿。
D 男且出生年小于 A：堂哥；D 女且出生年大于 A：堂妹。
同年或出生年缺失：保留长幼候选；不能默认哥哥或姐姐。
即使 A 与 D 还有朋友直连，也必须保留这条三步亲属候选。
这里不实现完整家谱、自动排行前缀或模型兜底。
"""

from dataclasses import dataclass
from typing import Literal

Gender = Literal["male", "female", "unknown"]
AgeOrder = Literal["older", "younger", "unknown"]
RelativeAge = Literal["older", "younger", "unknown", "conflict"]
KinshipRole = Literal["parent", "child", "sibling", "spouse"]
ResultStatus = Literal[
    "resolved", "ambiguous", "unsupported", "no_path", "conflict", "incomplete"
]

# 本轮只枚举基础亲属边；其他直接称谓留给后续明确扩展。
BASIC_RELATION_TYPES = frozenset(
    {"父亲", "母亲", "儿子", "女儿", "哥哥", "弟弟", "姐姐", "妹妹", "丈夫", "妻子"}
)
MAX_KINSHIP_DEPTH = 3
MAX_CANDIDATE_PATHS = 64


@dataclass(frozen=True)
class PersonNode:
    person_id: str
    owner_id: str
    gender: Gender
    birth_year: int | None


@dataclass(frozen=True)
class RelationEdge:
    edge_id: str
    owner_id: str
    stored_from_id: str
    stored_to_id: str
    relation_type: str


@dataclass(frozen=True)
class TraversalStep:
    traverse_from: PersonNode
    traverse_to: PersonNode
    edge: RelationEdge


@dataclass(frozen=True)
class CandidatePaths:
    paths: tuple[tuple[TraversalStep, ...], ...]
    truncated: bool


@dataclass(frozen=True)
class NormalizedStep:
    role: KinshipRole
    target_gender: Gender
    age_order: AgeOrder
    evidence_edge_id: str


@dataclass(frozen=True)
class KinshipResult:
    status: ResultStatus
    titles: tuple[str, ...]
    missing_fields: tuple[str, ...]
    evidence_paths: tuple[tuple[str, ...], ...]
    explanation: str


def get_kinship_candidates(
    driver: object,
    *,
    owner_id: str,
    source_id: str,
    target_id: str,
    max_depth: int = 3,
    max_candidates: int = 64,
) -> CandidatePaths:
    """获取当前归属内、有数量和深度限制的基础亲属候选路径。"""
    # TODO 1：校验非空 ID；起终点相同不属于本轮亲属查询。
    # 深度与数量必须是整数且不是 bool，分别限定为 1..3、1..64。
    # owner_id 由受信任的调用层提供，不能信任模型自行填写的归属。
    # TODO 2：使用传入 driver；本函数不创建连接，不导入应用配置。
    # ID 与归属使用查询参数；若深度必须写入查询文本，先严格验证。
    # TODO 3：在数据库查询内先限制所有节点 owner_id 和所有边 owner。
    # 既查首尾也查中间节点；缺少归属的节点或边不能参与候选。
    # 原始存储边的 owner 字段映射成 RelationEdge.owner_id。
    # TODO 4：先按 BASIC_RELATION_TYPES 过滤边，再枚举合法路径。
    # 不可先对含朋友边的全图 shortestPath，再过滤得到的唯一结果。
    # 枚举长度 1..max_depth 的简单路径；同一路径不重复人物。
    # 同时保留反向遍历和多条候选，不在此处根据称谓强选最短路径。
    # TODO 5：最多取 max_candidates + 1 条，设置查询执行时间上限。
    # 多取的一条只用于识别截断；不能仅凭恰好达到上限就判定完整。
    # 超时或查询失败应明确抛出错误，不能伪装成没有亲属路径。
    # TODO 6：把节点、边映射成上述类型，保留 ID、性别和出生年。
    # traverse_from/to 始终随遍历排列；stored_from/to 始终保留存储方向。
    # TODO 7：返回截取后的候选与 truncated；无路径为 paths=()。
    # “完整查询无路径”与“只扫描了部分候选”必须由调用层区分。
    raise NotImplementedError("待实现：查询有界的亲属候选路径")


def normalize_step(step: TraversalStep) -> NormalizedStep:
    """把一条原始边转换成当前遍历视角下的结构角色与事实约束。"""
    # TODO 1：验证遍历两端确实对应存储两端，且不是人物自环。
    # 三个 owner_id 应相等；这只是防御检查，不能替代数据库授权过滤。
    # 不支持的关系可抛出带明确原因的 ValueError，供分类层转 unsupported。
    # 结构错误或事实矛盾也需明确区分原因，不能静默沿用原关系。
    # TODO 2：先解析原始关系，不立即反转汉字称谓。
    # 父亲/母亲 -> parent，儿子/女儿 -> child，兄弟姐妹 -> sibling，
    # 丈夫/妻子 -> spouse；称谓中的男女约束属于原存储终点。
    # 原存储终点性别未知时可使用明确关系约束；已知且矛盾则报冲突。
    # TODO 3：正向遍历保留角色；反向时 parent 与 child 互换。
    # sibling 与 spouse 反向仍是同一结构角色，但目标人物已经改变。
    # 例 D --父亲--> C 反向走 C 到 D，应读取 D.gender 判断子女性别。
    # 不能因为 C 是父亲就把 D 设成男性，也不能默认母亲的孩子是女儿。
    # TODO 4：兄弟姐妹要同时处理目标性别与长幼约束。
    # X --哥哥--> Y 意味 Y 比 X 大；反向则目标 X 比当前人物 Y 小。
    # 此时 X.gender 决定弟弟/妹妹；X 性别未知则保留未知，不照抄 Y 性别。
    # 配偶反向也读取新的目标性别，不自动假定原存储起点的性别。
    # TODO 5：age_order 仅描述当前这两人的兄弟姐妹长幼关系。
    # 不把父亲的弟弟较年轻推导为其子女较年轻；其他角色设 unknown。
    # 兄弟姐妹称谓与这两人的已知出生年矛盾时，应报告冲突。
    # TODO 6：返回归一化结果，并用 edge_id 保存原始证据来源。
    raise NotImplementedError("待实现：按正确端点归一化关系方向")


def compare_relative_age(source: PersonNode, target: PersonNode) -> RelativeAge:
    """只根据出生年判断目标相对于起点的长幼，不接收排行推测。"""
    # TODO 1：检查出生年类型；有效值为合理整数或 None，bool 不算整数。
    # 明显无效的年份或同一人物的相互矛盾数据返回 conflict。
    # 数据缺失不能作为冲突；本函数也不根据姓名或性别猜年龄。
    # TODO 2：任一出生年缺失，返回 unknown。
    # TODO 3：target.birth_year 小于 source.birth_year，返回 older。
    # target.birth_year 大于 source.birth_year，返回 younger。
    # TODO 4：同年返回 unknown；这里只保存年份，不能判断同年谁先出生。
    # TODO 5：不读取父辈长幼、不同家庭的排行，也不调用模型。
    # 如以后加入生日或用户明确的长幼事实，应另行约定证据与冲突规则。
    raise NotImplementedError("待实现：比较目标与起点的相对年龄")


def classify_common_path(steps: tuple[TraversalStep, ...]) -> KinshipResult:
    """用有限的常见规则分类一条路径，同时保留未知事实和证据。"""
    # TODO 1：验证路径非空、连续、节点不重复，并符合本轮深度上限。
    # 空输入在本层视为无路径；不把它当作“自己”或任意亲属称谓。
    # 同一个 person_id 在多步中出现时，其归属、性别与出生年应一致。
    # TODO 2：逐步调用 normalize_step；关系不支持返回 unsupported，
    # 结构、性别或年龄事实矛盾返回 conflict，并说明是哪条证据冲突。
    # TODO 3：编写小规则表，只覆盖直系、兄弟姐妹、配偶、祖辈、
    # 伯叔姑舅姨以及堂表亲；超出范围明确 unsupported，不模型兜底。
    # 三步 parent -> sibling -> child 需要中间人物性别区分堂亲/表亲。
    # 父亲的兄弟子女为堂亲；父亲的姐妹或母亲的兄弟姐妹子女为表亲。
    # 中间人物性别未知时保留可能分类，不能默认为父系男性分支。
    # TODO 4：目标性别使用最后一步的归一化约束，并与人物事实核对。
    # 堂表亲长幼比较的是 steps[0].traverse_from 与 steps[-1].traverse_to。
    # 必须调用 compare_relative_age，不能沿用中间兄弟姐妹的 age_order。
    # TODO 5：男且年长/年幼 -> 堂哥/堂弟；女则 -> 堂姐/堂妹。
    # 表亲同理；只缺长幼时保留对应两项，只缺性别时保留性别候选。
    # 两者都未知时可用中性“堂亲/表亲”，状态仍为 ambiguous。
    # missing_fields 用人物 ID 说明缺谁的 gender 或 relative_age，
    # 同年导致不确定时说明需要更精确的生日或明确长幼事实。
    # TODO 6：确定唯一称谓才为 resolved；事实不足为 ambiguous。
    # 不自动添加“大/三/小”等排行前缀；本接口没有明确的排行分组。
    # TODO 7：evidence_paths 保存该路径依次经过的 edge_id。
    # explanation 应解释称谓依据和未知项，不把“不支持”写成“无亲属”。
    raise NotImplementedError("待实现：分类常见亲属并保留不确定性")


def select_kinship_result(
    results: tuple[KinshipResult, ...], *, truncated: bool
) -> KinshipResult:
    """合并多条候选的判断，最短路径只用于选择一致结论的展示依据。"""
    # TODO 1：先检查 truncated；候选不完整时总体返回 incomplete。
    # 可展示已找到的候选与冲突，但 explanation 必须说明未完成搜索，
    # 不得声称称谓唯一，也不得把未找到候选断言成无路径。
    # TODO 2：完整搜索且 results 为空时返回 no_path。
    # 所有路径都不在规则范围内则 unsupported，不能改写为 no_path。
    # TODO 3：保留明确的数据冲突，返回 conflict 并附相应证据路径。
    # 候选称谓不一致时不能用最短路径消除歧义；若可能是多重亲属身份，
    # 应解释为需要澄清的不同结论，不声称图中的某一条事实必然错误。
    # TODO 4：对一致或兼容的称谓集合合并，保留尚缺事实和所有关键证据。
    # 唯一且证据充分才 resolved；缺事实或存在多种合法称谓则 ambiguous。
    # 存在未支持的候选时须保留限制，不能以忽略它为前提宣称全局唯一。
    # TODO 5：只有结论一致后，才按路径长度选较短的展示解释。
    # 相同长度可按边 ID 稳定排序；不能根据数据库偶然返回顺序决定称谓。
    # TODO 6：返回可直接解释给用户的状态、称谓、缺失项与证据；
    # 生成回复的一层只能表达这些事实，不得把 ambiguous 改写成 resolved。
    raise NotImplementedError("待实现：合并候选结论并保留冲突或搜索限制")
