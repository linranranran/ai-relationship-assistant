# ==========================================================
# 认证路由 — 注册 / 登录 / 当前用户
# ==========================================================

from fastapi import APIRouter, Depends, HTTPException, status
from backend.auth import authenticate_user, create_user_in_db, get_current_user
from backend.db.neo4j_client import get_neo4j_driver
from backend.models.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/api/auth", tags=["认证"])


# ── 接口 ──

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest):
    """注册新用户。手机号全局唯一。"""
    driver = get_neo4j_driver()
    try:
        user = create_user_in_db(driver, body.phone_number, body.password)
        return {"message": "注册成功", "user_id": user["user_id"], "phone": user["phone"]}
    except HTTPException:
        raise
    finally:
        driver.close()


@router.post("/login")
def login(body: LoginRequest):
    """登录，返回 JWT token（7 天有效）。"""
    driver = get_neo4j_driver()
    try:
        result = authenticate_user(driver, body.phone_number, body.password)
        return {
            "message": "登录成功",
            "token": result["token"],
            "user_id": result["user_id"],
            "phone": result["phone"],
        }
    except HTTPException:
        raise
    finally:
        driver.close()


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    """获取当前登录用户信息（需要 Authorization: Bearer <token>）。"""
    return {"user_id": current_user["user_id"], "phone": current_user["phone"]}
