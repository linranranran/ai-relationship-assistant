"""身份与权限教学骨架：只定义契约，不接入现有应用。

建议落地位置（是建议，不能据此认为这些文件已经存在）：
- backend/models/domain.py：RequestContext、PersonRef、仓储协议。
- backend/services/access.py：本人映射解析、人物归属校验。
- backend/auth.py：认证成功后调用服务；注册时协调本人档案事务。

本文件仅使用 Python 3.11 标准库；导入不会读取环境或连接数据库。
业务函数故意抛出 NotImplementedError，需由学习者实现后再接入。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RequestContext:
    # user_id：已验证的 User.user_id，也是当前请求的数据 owner。
    # self_person_id：属于该 owner 的本人 Person.id，不能用账户 ID 替代。
    # request_id：用于串联日志和错误，不充当权限凭证或操作幂等键。
    user_id: str
    self_person_id: str
    request_id: str


@dataclass(frozen=True)
class PersonRef:
    # 姓名仅用于展示，允许重名；任何写入必须使用明确的 person_id。
    # owner_id 必须来自数据库结果，不能照抄请求参数冒充已验证归属。
    person_id: str
    owner_id: str
    display_name: str


class UserRepository(Protocol):
    # 读取显式保存的本人映射；不得按姓名、手机号相似性猜测本人。
    # 返回 None 表示待绑定或配置缺失，不表示允许随请求自动建档。
    def resolve_self_person_id(self, *, user_id: str) -> str | None: ...


class PersonRepository(Protocol):
    # 实现必须在同一查询中同时匹配 owner_id 与 person_id。
    # 不存在与属于别人均返回 None，避免暴露其他账户的人物是否存在。
    def get_owned(self, *, owner_id: str, person_id: str) -> PersonRef | None: ...


# 上游约束：只有认证依赖或内部可信入口可以构造 RequestContext。
# 不得接受请求体、会话历史或模型提供的 user_id 作为认证结果。
# 只声明 frozen=True 不构成认证；调用边界仍需由应用代码保证。
def resolve_request_context(
    authenticated_user_id: str,
    request_id: str,
    users: UserRepository,
) -> RequestContext:
    # 1. 确认调用来自认证依赖；上游应已验证令牌并回查账户仍存在。
    # 2. 检查两个输入不是空字符串；不要把客户端身份重新带回上下文。
    # 3. 调用 users.resolve_self_person_id(user_id=authenticated_user_id)。
    # 4. 映射缺失时返回领域错误“待绑定本人档案”，由路由转换响应。
    #    账户不存在由认证层处理；仓储异常不能伪装成映射缺失。
    # 5. 不在这里调用注册建档函数，也不要把 user_id 填作 Person.id。
    # 6. 返回 RequestContext，供服务端统一注入 owner 与默认路径起点。
    # 7. 此接口只解析映射；图操作前仍需校验本人 Person 的 owner。
    raise NotImplementedError("请实现：从已认证账户解析显式本人映射")


# 预检查能提供一致错误，但不能代替最终数据库操作的 owner 条件。
def require_owned_person(
    ctx: RequestContext,
    person_id: str,
    people: PersonRepository,
) -> PersonRef:
    # 1. 检查 person_id 非空；不得按姓名自动选第一个候选。
    # 2. 调用 people.get_owned(owner_id=ctx.user_id, person_id=person_id)。
    # 3. None 统一转为“人物不存在或不可访问”，不另查其他 owner。
    # 4. 对返回值核对 person_id、owner_id，发现仓储违约则拒绝继续。
    # 5. 返回 PersonRef；错误类型与路由响应码需在落地时统一定义。
    # 6. 后续读写仍原子匹配 owner；检查后再按裸 ID 写入会留下竞态。
    raise NotImplementedError("请实现：按已认证 owner 校验人物归属")


# driver 暂用 object，避免教学文件导入 Neo4j 或读取任何应用配置。
# 实际落地时明确事务接口；不能复制成两个独立提交的注册步骤。
def ensure_self_person_for_registration(
    driver: object,
    *,
    user_id: str,
    display_name: str,
) -> str:
    # 1. 仅由可信注册流程或显式资料迁移调用，不暴露为模型工具。
    # 2. 确定事务边界：新 User、本人 Person、本人映射应一起提交。
    #    本函数需要参与同一注册写事务，不能先提交账户再另建人物。
    # 3. 先准备并验证唯一约束：User.user_id、User.phone_number、Person.id。
    #    映射需保证每个 User 只有一个本人 ID，且本人归属于该 User。
    # 4. 采用完整随机 ID；不要用截断 UUID，也不要要求姓名唯一。
    # 5. 对同一 User 加写锁或采用等价的事务并发控制，然后重查映射。
    #    若已存在合法映射，返回原 ID，使同一注册操作重试不重复建档。
    # 6. 新账户无映射时创建 Person，owner_id=user_id，记录显式本人映射。
    #    账户 ID 与人物 ID 是不同字段；两者偶然字符串相同也不能互换。
    # 7. 已有账户或旧资料缺映射时暂停自动建档，进入显式绑定流程。
    #    列出该 owner 的候选，由用户选择；不能凭重名或手机号自动合并。
    #    若选择创建新档案，也应是明确的迁移决定，并留下绑定记录。
    # 8. 发现映射指向异主或已删除 Person 时报告配置错误，不偷偷修复。
    # 9. 事务失败须整体回滚；唯一约束冲突需区分并发重试和手机号已注册。
    # 10. 仅在事务结果可靠时向上层返回 self_person_id；异常不得吞掉。
    raise NotImplementedError("请实现：注册事务内建立唯一、可验证的本人映射")


# 数据访问实现清单（由学习者在实际仓储中完成）：
# - 人物查询、更新、删除的最终 Cypher 同时匹配 id 与 owner_id。
# - 关系读取和写入校验边 owner，以及起点、终点、介绍人各自的 owner。
# - 路径不仅校验端点和每条边，还校验每个中间 Person 的 owner。
# - 返回邻接人物前校验该邻居归属；历史错误边不能绕过读权限。
# - 更新和删除检查实际匹配数；零匹配不得报告成功。
# - 向量写入、删除与搜索都使用可信 owner；向量命中后回查图中归属。
# - 任意 forbidden/notfound 对外采用相同文案和约定响应，不泄露存在性。
# - 认证错误、本人待绑定、服务故障分别处理，不混为“人物不存在”。
# - 本人删除策略须明确：可拒绝删除，或事务内重新绑定；不能留悬空映射。

# 两账户验收故事：
# A、B 是两个认证账户；B 的名下有两份同名“张三”档案 B1、B2。
# A 的请求即使提交 B1 的真实 ID、声称自己是 B，也只能使用 A 的上下文。
# B 搜“张三”必须收到两个候选并选择，不能以姓名唯一规则自动合并。
# B 绑定本人时选中 B2 后，默认关系路径起点必须是 B2，而非 B.user_id。

# 建议验收（使用假仓储或独立测试数据库，不调用真实个人数据）：
# 1. 认证 A + 请求体伪造 B：上下文仍为 A；缺认证时不能构造业务上下文。
# 2. A 读取/修改/删除 B1：统一拒绝；最终数据库与向量记录均无变化。
# 3. 无本人映射：明确返回待绑定，断言没有调用创建或合并操作。
# 4. 映射指向 B 的人物或悬空 ID：操作被拒绝，不能自动补建。
# 5. 两次并发注册/重试：最多一个 User、一个本人 Person、一份映射。
# 6. 注册事务中间失败：三项写入一起回滚，不留下半个注册结果。
# 7. 植入 A 边连接 B 中间人物的脏图：列表、详情、路径均不暴露 B。
# 8. 权限预检查后目标被删除或更改归属：最终写入零匹配且不报成功。
