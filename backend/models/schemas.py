"""HTTP request and response schemas.

Identity fields deliberately do not appear in browser-controlled request models.
The backend derives them from the authenticated user.
"""

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)


PHONE_PATTERN = r"^1[3-9]\d{9}$"
MAX_CHAT_MESSAGE_LENGTH = 4000

PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=PHONE_PATTERN)]
Password = Annotated[str, StringConstraints(min_length=6, max_length=64)]
ChatMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHAT_MESSAGE_LENGTH),
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictRequest):
    """Agent request. User identity always comes from the bearer token."""

    message: ChatMessage = Field(description="用户输入的消息")


class ResumeAgentRequest(StrictRequest):
    """恢复被 interrupt 暂停的 Agent；只提交当前交互需要的答案字段。"""

    interrupt_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
    ]
    confirmed: Optional[StrictBool] = Field(
        default=None,
        description="确认交互使用，必须是 JSON 布尔值 true/false",
    )
    candidate_id: Optional[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    ] = Field(default=None, description="候选选择澄清使用")
    answer: Optional[ChatMessage] = Field(default=None, description="自由文本澄清使用")

    @model_validator(mode="after")
    def exactly_one_answer(self):
        supplied = sum(
            value is not None
            for value in (self.confirmed, self.candidate_id, self.answer)
        )
        if supplied != 1:
            raise ValueError("confirmed、candidate_id、answer 必须且只能提交一个")
        return self


class AuthRequest(StrictRequest):
    phone_number: PhoneNumber = Field(description="中国大陆手机号")
    password: Password = Field(description="密码")

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 编码后不能超过 72 字节")
        return value


class RegisterRequest(AuthRequest):
    """用户注册请求。"""


class LoginRequest(AuthRequest):
    """用户登录请求。"""


class ConfirmationPrompt(BaseModel):
    interrupt_id: str
    summary: str
    tool_names: list[str] = Field(default_factory=list)


class ClarificationCandidate(BaseModel):
    id: str
    label: str
    description: str = ""


class ClarificationPrompt(BaseModel):
    interrupt_id: str
    clarification_type: Literal["CANDIDATE_SELECTION", "FREE_TEXT"]
    question: str
    candidates: list[ClarificationCandidate] = Field(default_factory=list)


class ChatResponse(BaseModel):
    request_id: str = Field(description="本次请求的幂等键，重试和查询必须复用")
    response: str = Field(description="Agent 的回复")
    intent: Optional[str] = Field(default=None, description="识别的意图")
    tool_calls: Optional[list[str]] = Field(default=None, description="执行的 Tool 列表")
    status: Literal[
        "RUNNING",
        "COMPLETED",
        "CONFIRMATION_REQUIRED",
        "CLARIFICATION_REQUIRED",
        "FAILED",
    ] = "COMPLETED"
    confirmation: Optional[ConfirmationPrompt] = None
    clarification: Optional[ClarificationPrompt] = None


class PersonResponse(BaseModel):
    id: str
    name: str
    gender: Optional[str] = None
    birth_year: Optional[int] = None
    occupation: Optional[str] = None
    education: Optional[str] = None
    hobbies: list[str] = Field(default_factory=list)
    personality: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class RelationResponse(BaseModel):
    id: str
    from_person: str
    to_person: str
    relation_type: str
    through: Optional[str] = None
    note: Optional[str] = None


class AuthResponse(BaseModel):
    success: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    token: Optional[str] = None
    message: Optional[str] = None
