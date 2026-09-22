"""Tool 参数契约。

这里只描述允许大模型规划的业务参数。owner_id、self_person_id、confirmed 等
可信参数由 Agent 运行时注入，不能出现在这些模型中。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AddPersonArgs(ToolArgs):
    name: str = Field(min_length=1, max_length=100)
    gender: Literal["男", "女", "未知"] = "未知"
    birth_year: int | None = Field(default=None, ge=1800, le=2100)
    occupation: str | None = Field(default=None, max_length=200)
    education: str | None = Field(default=None, max_length=200)
    hobbies: list[str] = Field(default_factory=list, max_length=50)
    personality: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=50)
    note: str | None = Field(default=None, max_length=2000)
    photo_url: str | None = Field(default=None, max_length=2000)


class UpdatePersonArgs(ToolArgs):
    person_id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    gender: Literal["男", "女", "未知"] | None = None
    birth_year: int | None = Field(default=None, ge=1800, le=2100)
    occupation: str | None = Field(default=None, max_length=200)
    education: str | None = Field(default=None, max_length=200)
    hobbies: list[str] | None = Field(default=None, max_length=50)
    personality: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=50)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_at_least_one_change(self):
        if not self.model_fields_set - {"person_id"}:
            raise ValueError("update_person 至少需要一个待修改字段")
        return self


class DeletePersonArgs(ToolArgs):
    person_id: str = Field(min_length=1, max_length=128)


class AddRelationArgs(ToolArgs):
    from_person_id: str = Field(min_length=1, max_length=128)
    to_person_id: str = Field(min_length=1, max_length=128)
    relation_type: str = Field(min_length=1, max_length=100)
    through_person_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class UpdateRelationArgs(ToolArgs):
    relation_id: str = Field(min_length=1, max_length=128)
    new_relation_type: str | None = Field(default=None, min_length=1, max_length=100)
    through_person_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_at_least_one_change(self):
        if not self.model_fields_set - {"relation_id"}:
            raise ValueError("update_relation 至少需要一个待修改字段")
        return self


class DeleteRelationArgs(ToolArgs):
    relation_id: str = Field(min_length=1, max_length=128)


class FindPersonArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=500)
    search_mode: Literal["exact", "fuzzy", "semantic"] = "fuzzy"
    limit: int = Field(default=5, ge=1, le=20)


class QueryRelationPathArgs(ToolArgs):
    target_person_id: str = Field(min_length=1, max_length=128)
    max_depth: int = Field(default=7, ge=1, le=7)


class GetKinshipTitleArgs(ToolArgs):
    target_person_id: str = Field(min_length=1, max_length=128)
    relation_path: list[dict[str, Any]] | None = None
    birth_order: int | None = Field(default=None, ge=1)
    total_siblings: int | None = Field(default=None, ge=1)
    target_gender: Literal["男", "女", "未知"] | None = None


class GetPersonDetailArgs(ToolArgs):
    person_id: str = Field(min_length=1, max_length=128)


class ListRelationsArgs(ToolArgs):
    person_id: str = Field(min_length=1, max_length=128)
    relation_type: str | None = Field(default=None, min_length=1, max_length=100)
