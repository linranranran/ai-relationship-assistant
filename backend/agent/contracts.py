"""模型边界的数据契约。合法 JSON 不等于合法业务，执行前仍须校验权限和依赖。"""

from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class GoalStatus(StrEnum):
    """业务目标状态。SUCCEEDED 必须有确定性的工具证据，不能由模型自评。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # 前置目标失败或预算不足，当前目标没有完成。
    CANCELLED = "CANCELLED"


IntentKind = Literal[
    "ADD_PERSON",
    "UPDATE_PERSON",
    "DELETE_PERSON",
    "ADD_RELATION",
    "UPDATE_RELATION",
    "DELETE_RELATION",
    "QUERY_PERSON",
    "QUERY_KINSHIP",
    "SEMANTIC_SEARCH",
    "LIST_RELATIONS",
    "CHITCHAT",
    "UNCLEAR",
    "UNSUPPORTED",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonExtraction(Contract):
    name: str | None = None
    gender: str | None = None
    # 与 Tool 参数、Neo4j 属性使用同一类型，避免 '1990' 与 1990 比较失败。
    birth_year: int | None = Field(default=None, ge=1800, le=2100)
    occupation: str | None = None
    education: str | None = None
    hobbies: list[str] | None = None
    personality: str | None = None
    tags: list[str] | None = None
    note: str | None = None


class RelationExtraction(Contract):
    person: str = Field(description="角色持有者 B；例如张是杨的上司时填张。对应图边终点。")
    related_to: str = Field(description="被描述的人 A；例如张是杨的上司时填杨。图边为 A-[type]->B。")
    type: str
    through: str | None = None


class ExtractionResult(Contract):
    primary_person: PersonExtraction = Field(default_factory=PersonExtraction)
    relations: list[RelationExtraction] = Field(default_factory=list, max_length=16)
    mentioned_people: list[str] = Field(default_factory=list, max_length=16)


class QuerySpec(Contract):
    """查询需求在初次理解时固定；规划模型不能自行改变验收条件。"""
    kind: Literal["person_detail", "relation_path", "kinship_title", "semantic_matches", "relation_list"]
    target_name: str | None = Field(default=None, min_length=1, max_length=100)
    requested_fields: list[Literal["name", "gender", "birth_year", "occupation", "education",
                                  "hobbies", "personality", "tags", "note"]] = Field(default_factory=list, max_length=9)


class GoalDraft(Contract):
    intent: IntentKind
    description: str = Field(min_length=1, max_length=1000)
    source_quote: str = Field(min_length=1, max_length=1000)
    depends_on: list[StrictInt] = Field(default_factory=list, max_length=3)
    # description 是给人阅读的，不能可靠地用字符串匹配判断人物身份和属性。
    # 分类器同时保留结构化业务字段，后续提取节点也能检查出生年份等是否丢失。
    # 默认 None 用于读取旧的 Checkpoint；新请求由 build_goals 要求对应字段。
    person: PersonExtraction | None = None
    relation: RelationExtraction | None = None
    query_spec: QuerySpec | None = None  # None 用于兼容既有检查点。


class IntentBatch(Contract):
    goals: list[GoalDraft] = Field(min_length=1, max_length=4)


class ToolStep(Contract):
    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=16)


class ToolPlan(Contract):
    tool_calls: list[ToolStep] = Field(default_factory=list, max_length=16)


class StructuredOutputError(ValueError):
    """模型协议错误；不是用户缺少业务信息，也不能绕过契约进入执行器。"""


class ModelBusinessValidationError(StructuredOutputError):
    """可给模型修复的业务契约错误；feedback/metadata 不含客户原文或参数值。"""

    def __init__(self, code: str, feedback: str, **metadata):
        super().__init__(feedback)
        self.code = code
        self.feedback = feedback
        self.metadata = metadata
