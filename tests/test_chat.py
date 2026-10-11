"""问答接口的请求校验、链路编排和持久化测试。"""

import os
from pathlib import Path
from types import SimpleNamespace
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
from app.models import QAMessage, QAReference
from app.services.generation import GenerationResult, NO_EVIDENCE_ANSWER
from app.services.retrieval import RetrievalItem, RetrievalResult


def create_test_client(tmp_path: Path):
    """创建使用内存数据库的问答测试客户端。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        """为测试请求提供隔离数据库会话。"""
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application), session_factory


def register_and_login(client: TestClient) -> dict[str, str]:
    """注册测试用户并返回认证请求头。"""
    payload = {"email": f"{uuid4()}@example.com", "password": "password-123"}
    assert client.post("/auth/register", json=payload).status_code == 201
    response = client.post("/auth/login", json=payload)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_item() -> RetrievalItem:
    """创建问答接口使用的检索结果。"""
    return RetrievalItem(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="Prag 是一个知识库项目。",
        parent_content=None,
        similarity=0.9,
        document_name="项目说明.md",
        chunk_index=0,
        page_number=1,
        section_title="项目介绍",
        line_number=1,
    )


def test_chat_returns_answer_citations_and_persists_records(tmp_path, monkeypatch):
    """问答成功时返回回答和引用，并保存消息及引用记录。"""
    from app.api import chat as chat_api

    item = create_item()

    class FakeRetriever:
        """返回固定检索结果的测试检索器。"""

        def search(self, db, user_id, knowledge_base_id, question, top_k=None, min_score=None):
            """返回一条固定命中。"""
            return RetrievalResult([item], top_k or 5, min_score or 0.3, 1, item.similarity, 2)

    class FakeGenerator:
        """返回固定回答的测试生成器。"""

        def generate(self, question, retrieval_items):
            """返回带 [1] 引用的回答。"""
            return GenerationResult("项目说明见 [1]。", "[1] 内容", 2, 3, False, True)

    monkeypatch.setattr(chat_api, "VectorRetriever", FakeRetriever)
    monkeypatch.setattr(chat_api, "AnswerGenerator", FakeGenerator)
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        response = client.post("/chat", headers=headers, json={"question": "项目是什么？"})

    body = response.json()
    assert response.status_code == 200
    assert body["answer"] == "项目说明见 [1]。"
    assert body["retrieval_hit_count"] == 1
    assert body["confidence"] == 0.45
    assert body["citations"][0]["document_name"] == "项目说明.md"
    with session_factory() as session:
        message = session.scalar(select(QAMessage))
        reference = session.scalar(select(QAReference))
        assert message.question == "项目是什么？"
        assert message.answer == "项目说明见 [1]。"
        assert message.answer_source == "knowledge_base"
        assert reference.chunk_id == item.chunk_id


def test_chat_empty_retrieval_returns_no_evidence_and_no_references(tmp_path, monkeypatch):
    """检索为空时返回固定提示，并保存 no_answer 消息但不调用生成器。"""
    from app.api import chat as chat_api

    class FakeRetriever:
        """返回空检索结果的测试检索器。"""

        def search(self, db, user_id, knowledge_base_id, question, top_k=None, min_score=None):
            """返回空结果。"""
            return RetrievalResult([], top_k or 5, min_score or 0.3, 0, None, 1)

    class FakeGenerator:
        """断言空检索不应调用的生成器。"""

        def generate(self, question, retrieval_items):
            """如果被调用则测试失败。"""
            raise AssertionError("空检索不应调用生成器")

    monkeypatch.setattr(chat_api, "VectorRetriever", FakeRetriever)
    monkeypatch.setattr(chat_api, "AnswerGenerator", FakeGenerator)
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        response = client.post("/chat", headers=headers, json={"question": "未知问题"})

    assert response.status_code == 200
    assert response.json()["answer"] == NO_EVIDENCE_ANSWER
    assert response.json()["confidence"] == 0
    with session_factory() as session:
        message = session.scalar(select(QAMessage))
        assert message.answer_source == "no_answer"
        assert list(session.scalars(select(QAReference))) == []


def test_chat_rejects_blank_question_and_requires_auth(tmp_path):
    """空白问题和未认证请求均被拒绝。"""
    client, _ = create_test_client(tmp_path)
    with client:
        missing_auth = client.post("/chat", json={"question": "有效问题"})
        blank = client.post("/chat", json={"question": "   "})

    assert missing_auth.status_code == 401
    assert blank.status_code == 401
