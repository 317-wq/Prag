"""用户注册、登录、退出和认证依赖。"""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import KnowledgeBase, User

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    """用户注册请求。"""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    nickname: str | None = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def validate_bcrypt_length(cls, value: str) -> str:
        """拒绝超过 bcrypt 72 字节限制的密码。"""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码长度不能超过 72 字节")
        return value


class LoginRequest(BaseModel):
    """用户登录请求。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_bcrypt_length(cls, value: str) -> str:
        """拒绝超过 bcrypt 72 字节限制的密码。"""
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码长度不能超过 72 字节")
        return value


class UserResponse(BaseModel):
    """安全的用户响应，不包含密码哈希。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    nickname: str | None


class AuthResponse(BaseModel):
    """注册或登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码是否匹配已有哈希。"""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user: User) -> str:
    """创建包含用户 ID 和过期时间的 JWT。"""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user.id), "email": user.email, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """解析 Bearer JWT 并加载当前启用用户。"""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证失败",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise unauthorized from None

    user = db.scalar(select(User).where(User.id == user_id, User.status == "active"))
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    """注册用户并自动创建默认知识库。"""
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已注册")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        nickname=payload.nickname,
    )
    db.add(user)
    try:
        db.flush()
        db.add(KnowledgeBase(user_id=user.id, name="默认知识库"))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已注册") from None

    db.refresh(user)
    return AuthResponse(access_token=create_access_token(user), user=user)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> AuthResponse:
    """校验邮箱和密码并返回 JWT，不记录密码。"""
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.status != "active" or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    return AuthResponse(access_token=create_access_token(user), user=user)


@router.post("/logout")
def logout(_: CurrentUser) -> dict[str, str]:
    """确认客户端可以丢弃当前 JWT。JWT 为无状态令牌，服务端不保存密码或令牌内容。"""
    return {"message": "退出成功"}


@router.get("/me", response_model=UserResponse)
def current_user(user: CurrentUser) -> User:
    """返回当前认证用户，用于验证认证依赖。"""
    return user
