"""基础向量检索服务测试。"""

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models import Document, DocumentChunk, KnowledgeBase, User
from app.services.retrieval import VectorRetriever


class FakeEmbeddingClient:
    """返回固定查询向量并记录问题文本。"""

    def __init__(self, vector: list[float]) -> None:
        """保存查询向量。"""
        self.vector = vector
        self.questions: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """记录输入并返回固定向量。"""
        self.questions.extend(texts)
        return [self.vector for _ in texts]


def create_session_factory():
    """创建共享内存 SQLite 测试会话。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def create_user_and_kb(session, email: str) -> tuple[User, KnowledgeBase]:
    """创建测试用户和默认知识库。"""
    user = User(email=email, password_hash="hash")
    session.add(user)
    session.flush()
    knowledge_base = KnowledgeBase(user_id=user.id, name="默认知识库")
    session.add(knowledge_base)
    session.flush()
    return user, knowledge_base


def add_document(
    session,
    user: User,
    knowledge_base: KnowledgeBase,
    name: str,
    vector: list[float],
    status: str = "ready",
    domain_label: str = "general",
) -> DocumentChunk:
    """创建带向量和引用元数据的测试文档切片。"""
    document = Document(
        user_id=user.id,
        knowledge_base_id=knowledge_base.id,
        filename=name,
        file_type="txt",
        file_size=10,
        storage_path=f"/tmp/{name}",
        status=status,
        domain_label=domain_label,
    )
    session.add(document)
    session.flush()
    chunk = DocumentChunk(
        document_id=document.id,
        user_id=user.id,
        knowledge_base_id=knowledge_base.id,
        domain_label=domain_label,
        chunk_index=0,
        content=f"内容 {name}",
        parent_content="标题：测试",
        page_number=2,
        section_title="章节",
        line_number=10,
        embedding=vector,
    )
    session.add(chunk)
    session.flush()
    return chunk


def test_retrieval_returns_filtered_results_in_similarity_order():
    """检索只返回当前用户、知识库、领域和 ready 文档，并按相似度排序。"""
    session_factory = create_session_factory()
    query_vector = [1.0] + [0.0] * 1023
    with session_factory() as session:
        user, knowledge_base = create_user_and_kb(session, f"{uuid4()}@example.com")
        best = add_document(session, user, knowledge_base, "best.txt", query_vector)
        add_document(session, user, knowledge_base, "low.txt", [0.0, 1.0] + [0.0] * 1022)
        add_document(session, user, knowledge_base, "not-ready.txt", query_vector, status="indexing")
        add_document(session, user, knowledge_base, "other-domain.txt", query_vector, domain_label="finance")
        other_user, other_kb = create_user_and_kb(session, f"{uuid4()}@example.com")
        add_document(session, other_user, other_kb, "other-user.txt", query_vector)
        session.commit()

        client = FakeEmbeddingClient(query_vector)
        result = VectorRetriever(client).search(session, user.id, knowledge_base.id, "  测试问题  ", top_k=5, min_score=0.3)

    assert client.questions == ["测试问题"]
    assert result.hit_count == 1
    assert result.results[0].chunk_id == best.id
    assert result.results[0].similarity == 1.0
    assert result.results[0].document_name == "best.txt"
    assert result.results[0].page_number == 2
    assert result.results[0].section_title == "章节"
    assert result.results[0].line_number == 10
    assert result.max_similarity == 1.0


def test_retrieval_applies_top_k_and_min_score():
    """检索数量受 top_k 限制，低于 min_score 的结果被过滤。"""
    session_factory = create_session_factory()
    query_vector = [1.0] + [0.0] * 1023
    with session_factory() as session:
        user, knowledge_base = create_user_and_kb(session, f"{uuid4()}@example.com")
        add_document(session, user, knowledge_base, "first.txt", query_vector)
        add_document(session, user, knowledge_base, "second.txt", [0.8, 0.6] + [0.0] * 1022)
        add_document(session, user, knowledge_base, "third.txt", [0.0, 1.0] + [0.0] * 1022)
        session.commit()

        result = VectorRetriever(FakeEmbeddingClient(query_vector)).search(
            session, user.id, knowledge_base.id, "问题", top_k=1, min_score=0.7
        )

    assert result.hit_count == 1
    assert result.results[0].document_name == "first.txt"


def test_retrieval_returns_empty_result_without_llm_for_no_match():
    """没有满足阈值的切片时返回空结果，检索服务不触发任何 LLM 调用。"""
    session_factory = create_session_factory()
    query_vector = [1.0] + [0.0] * 1023
    with session_factory() as session:
        user, knowledge_base = create_user_and_kb(session, f"{uuid4()}@example.com")
        add_document(session, user, knowledge_base, "unrelated.txt", [0.0, 1.0] + [0.0] * 1022)
        session.commit()

        result = VectorRetriever(FakeEmbeddingClient(query_vector)).search(
            session, user.id, knowledge_base.id, "问题", min_score=0.9
        )

    assert result.results == []
    assert result.hit_count == 0
    assert result.max_similarity is None


def test_retrieval_rejects_empty_question_and_invalid_parameters():
    """问题、top_k 和最小相似度参数不合法时明确拒绝。"""
    session_factory = create_session_factory()
    with session_factory() as session:
        user, knowledge_base = create_user_and_kb(session, f"{uuid4()}@example.com")
        session.commit()
        retriever = VectorRetriever(FakeEmbeddingClient([1.0] + [0.0] * 1023))

        for kwargs in ({"question": "  "}, {"question": "问题", "top_k": 0}, {"question": "问题", "min_score": 2}):
            try:
                retriever.search(session, user.id, knowledge_base.id, **kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError("应拒绝非法检索参数")
