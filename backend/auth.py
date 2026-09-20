# ==========================================================
# 认证模块 — JWT + 密码哈希
# ==========================================================

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.config import SECRET_KEY
from backend.db.neo4j_client import get_neo4j_driver

# ── 密码哈希 ──
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── JWT 配置 ──
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 7  # 7 天

# ── HTTP Bearer 认证提取器 ──
bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: str, phone: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "phone": phone,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# ── FastAPI 依赖：从请求头提取当前用户 ──
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """从 Authorization: Bearer <token> 中解析 user_id 和 phone。"""
    try:
        payload = decode_token(credentials.credentials)
        user_id = payload.get("sub")
        phone = payload.get("phone")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效或已过期")

    # 校验数据库中的用户是否仍然存在
    driver = get_neo4j_driver()
    try:
        user = _get_user_from_db(driver, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    finally:
        driver.close()

    return {"user_id": user["user_id"], "phone": user["phone"]}


# ==========================================================
# Neo4j 用户表操作
# ==========================================================

def _find_user_by_phone(driver, phone: str) -> dict | None:
    records, _, _ = driver.execute_query(
        """
        MATCH (u:User {phone_number: $phone})
        RETURN u.user_id AS user_id, u.phone_number AS phone, u.hashed_password AS hashed_password
        """,
        {"phone": phone},
    )
    return records[0] if records else None


def _get_user_from_db(driver, user_id: str) -> dict | None:
    records, _, _ = driver.execute_query(
        """
        MATCH (u:User {user_id: $user_id})
        RETURN u.user_id AS user_id, u.phone_number AS phone
        """,
        {"user_id": user_id},
    )
    return records[0] if records else None


def create_user_in_db(driver, phone: str, password: str) -> dict:
    """在 Neo4j 中创建 :User 节点。phone_number 全局唯一。返回 {user_id, phone}。"""
    existing = _find_user_by_phone(driver, phone)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="手机号已注册")

    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    driver.execute_query(
        """
        CREATE (u:User {
            user_id: $user_id,
            phone_number: $phone,
            hashed_password: $hashed_password,
            phone_numbers: $phone_numbers,
            created_at: $created_at
        })
        """,
        {
            "user_id": user_id,
            "phone": phone,
            "hashed_password": hashed,
            "phone_numbers": [phone],
            "created_at": now,
        },
    )

    return {"user_id": user_id, "phone": phone}


def authenticate_user(driver, phone: str, password: str) -> dict:
    """校验手机号密码，成功返回 {user_id, phone, token}。"""
    user = _find_user_by_phone(driver, phone)
    if not user or not verify_password(password, user["hashed_password"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码错误")

    token = create_token(user["user_id"], user["phone"])
    return {"user_id": user["user_id"], "phone": user["phone"], "token": token}