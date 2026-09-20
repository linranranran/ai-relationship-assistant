# ============================================================
# Pydantic 数据模型
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# --- 请求模型 ---

class ChatRequest(BaseModel):
    """Agent 对话请求"""
    message: str = Field(..., description="用户输入的消息")
    user_id: str = Field(..., description="用户 ID")
    user_name: str = Field(default="我", description="用户姓名")
    history: List[dict] = Field(default_factory=list, description="最近对话历史")


class RegisterRequest(BaseModel):
    """用户注册请求"""
    name: str = Field(..., description="用户姓名")
    password: str = Field(..., description="密码")
    gender: str = Field(default="未知", description="性别")


class LoginRequest(BaseModel):
    """用户登录请求"""
    name: str = Field(..., description="用户姓名")
    password: str = Field(..., description="密码")


# --- 响应模型 ---

class ChatResponse(BaseModel):
    """Agent 对话响应"""
    response: str = Field(..., description="Agent 的回复")
    intent: Optional[str] = Field(default=None, description="识别的意图")
    tool_calls: Optional[List[str]] = Field(default=None, description="执行的 Tool 列表")


class PersonResponse(BaseModel):
    """人物信息响应"""
    id: str
    name: str
    gender: Optional[str] = None
    birth_year: Optional[int] = None
    occupation: Optional[str] = None
    education: Optional[str] = None
    hobbies: List[str] = Field(default_factory=list)
    personality: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class RelationResponse(BaseModel):
    """关系信息响应"""
    id: str
    from_person: str
    to_person: str
    relation_type: str
    through: Optional[str] = None
    note: Optional[str] = None


class AuthResponse(BaseModel):
    """认证响应"""
    success: bool
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    token: Optional[str] = None
    message: Optional[str] = None