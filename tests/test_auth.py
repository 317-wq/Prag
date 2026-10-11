"""认证接口和密码安全测试。"""

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import create_app
from app.models import KnowledgeBase, User


def create_test_client():
    """创建使用独立 SQLite 数据库的测试客户端。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application), engine, session_factory


def test_register_creates_bcrypt_user_and_default_knowledge_base():
    """注册成功后密码为 bcrypt 哈希且自动创建默认知识库。"""
    client, _, session_factory = create_test_client()
    with client:
        response = client.post(
            "/auth/register",
            json={"email": "User@Example.com", "password": "password-123"},
        )

    assert response.status_code == 201
    assert response.json()["user"]["email"] == "user@example.com"
    with session_factory() as session:
        user = session.scalar(select(User).where(User.email == "user@example.com"))
        knowledge_base = session.scalar(select(KnowledgeBase).where(KnowledgeBase.user_id == user.id))
        assert user.password_hash.startswith("$2")
        assert knowledge_base.name == "默认知识库"


def test_duplicate_email_and_invalid_login_are_rejected():
    """重复邮箱和错误密码均返回认证错误，且不暴露敏感信息。"""
    client, _, _ = create_test_client()
    with client:
        payload = {"email": "user@example.com", "password": "password-123"}
        assert client.post("/auth/register", json=payload).status_code == 201
        duplicate = client.post("/auth/register", json=payload)
        invalid = client.post("/auth/login", json={**payload, "password": "wrong-password"})

    assert duplicate.status_code == 409
    assert invalid.status_code == 401
    assert "password" not in invalid.text.lower()


def test_change_password_succeeds_and_old_password_is_rejected():
    """认证用户可以修改密码，旧密码和不合规新密码会被拒绝。"""
    client, _, _ = create_test_client()
    with client:
        payload = {"email": f"{uuid4()}@example.com", "password": "password-123"}
        registration = client.post("/auth/register", json=payload)
        token = registration.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        changed = client.put(
            "/auth/password",
            headers=headers,
            json={"current_password": "password-123", "new_password": "new-password-456"},
        )
        wrong_old = client.put(
            "/auth/password",
            headers=headers,
            json={"current_password": "password-123", "new_password": "another-password"},
        )
        short_new = client.put(
            "/auth/password",
            headers=headers,
            json={"current_password": "new-password-456", "new_password": "short"},
        )
        new_login = client.post("/auth/login", json={**payload, "password": "new-password-456"})

    assert changed.status_code == 200
    assert wrong_old.status_code == 400
    assert short_new.status_code == 422
    assert new_login.status_code == 200


def test_login_logout_and_authenticated_dependency():
    """登录返回 JWT，认证接口可访问，退出接口要求有效认证。"""
    client, _, _ = create_test_client()
    with client:
        payload = {"email": f"{uuid4()}@example.com", "password": "password-123"}
        assert client.post("/auth/register", json=payload).status_code == 201
        login_response = client.post("/auth/login", json=payload)
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/auth/me", headers=headers)
        logout = client.post("/auth/logout", headers=headers)
        missing_auth = client.get("/auth/me")

    assert login_response.status_code == 200
    assert me.status_code == 200
    assert logout.status_code == 200
    assert missing_auth.status_code == 401
