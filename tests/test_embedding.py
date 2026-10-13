"""Embedding 客户端、向量持久化和文档状态更新测试。"""

import os
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models import Document, DocumentChunk, KnowledgeBase, User
from app.services.embedding import EmbeddingDimensionError, QwenEmbeddingClient, embed_document_record


class FakeEmbeddings:
    """模拟 OpenAI Embedding 接口并记录输入。"""

    def __init__(self, vectors: list[list[float]], failures: int = 0) -> None:
        """保存返回向量和需要模拟的失败次数。"""
        self.vectors = vectors
        self.failures = failures
        self.inputs: list[list[str]] = []
        self.calls: list[dict] = []

    def create(self, **kwargs):
        """按输入生成带 index 的模拟响应。"""
        self.calls.append(kwargs)
        self.inputs.append(kwargs["input"])
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary failure")
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=vector)
                for index, vector in enumerate(self.vectors[: len(kwargs["input"])])
            ]
        )


class FakeClient:
    """模拟 OpenAI 客户端。"""

    def __init__(self, embeddings: FakeEmbeddings) -> None:
        """保存模拟 embeddings 资源。"""
        self.embeddings = embeddings


def create_session_factory():
    """创建可跨会话读取的 SQLite 测试数据库。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def create_document(session, chunk_count: int = 3) -> Document:
    """创建包含指定数量文档切片的测试文档。"""
    user = User(email=f"{uuid4()}@example.com", password_hash="hash")
    session.add(user)
    session.flush()
    knowledge_base = KnowledgeBase(user_id=user.id, name="默认知识库")
    session.add(knowledge_base)
    session.flush()
    document = Document(
        knowledge_base_id=knowledge_base.id,
        user_id=user.id,
        filename="embedding.txt",
        file_type="txt",
        file_size=10,
        storage_path="/tmp/embedding.txt",
        status="uploaded",
    )
    session.add(document)
    session.flush()
    for index in range(chunk_count):
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=knowledge_base.id,
                user_id=user.id,
                chunk_index=index,
                content=f"文本 {index}",
                parent_content="标题：测试",
            )
        )
    session.commit()
    return document


def test_embedding_client_uses_flash_model_and_keeps_input_order():
    """客户端使用 flash 向量模型，并按返回 index 恢复输入顺序。"""
    original_batch_size = settings.qwen_embedding_batch_size
    settings.qwen_embedding_batch_size = 2
    try:
        embeddings = FakeEmbeddings([[1.0] * 1024, [2.0] * 1024])
        client = QwenEmbeddingClient(FakeClient(embeddings))
        vectors = client.embed(["first", "second"])
    finally:
        settings.qwen_embedding_batch_size = original_batch_size

    assert embeddings.inputs == [["first", "second"]]
    assert embeddings.calls[0]["model"] == settings.qwen_embedding_model_flash
    assert vectors[0][0] == 1.0
    assert len(vectors[0]) == settings.qwen_embedding_dimension


def test_embedding_client_retries_and_rejects_wrong_dimension():
    """客户端按配置重试临时错误，并拒绝错误向量维度。"""
    original_retries = settings.qwen_embedding_max_retries
    original_delay = settings.qwen_embedding_retry_delay_seconds
    settings.qwen_embedding_max_retries = 2
    settings.qwen_embedding_retry_delay_seconds = 0
    try:
        embeddings = FakeEmbeddings([[1.0] * 1024], failures=1)
        client = QwenEmbeddingClient(FakeClient(embeddings))
        client.embed(["retry"])
        assert len(embeddings.inputs) == 2
        assert client.retry_count == 1
        wrong = QwenEmbeddingClient(FakeClient(FakeEmbeddings([[1.0] * 3])))
        with pytest.raises(EmbeddingDimensionError):
            wrong.embed(["wrong dimension"])
    finally:
        settings.qwen_embedding_max_retries = original_retries
        settings.qwen_embedding_retry_delay_seconds = original_delay


def test_embed_document_writes_vectors_metadata_and_survives_new_session():
    """文档向量化写入向量和元数据，ready 状态可跨新会话读取。"""
    original_batch_size = settings.qwen_embedding_batch_size
    settings.qwen_embedding_batch_size = 2
    session_factory = create_session_factory()
    try:
        with session_factory() as session:
            document = create_document(session)
            embeddings = FakeEmbeddings([[float(index)] * 1024 for index in range(2)])
            ready = embed_document_record(session, document.id, QwenEmbeddingClient(FakeClient(embeddings)))
            assert ready.status == "ready"
            assert ready.processed_at is not None
        with session_factory() as session:
            chunks = list(session.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)))
            assert [chunk.embedding[0] for chunk in chunks] == [0.0, 1.0, 0.0]
            assert chunks[0].parent_content == "标题：测试"
            assert session.get(Document, document.id).status == "ready"
    finally:
        settings.qwen_embedding_batch_size = original_batch_size


def test_embed_document_marks_failed_and_rolls_back_vectors():
    """Embedding 失败时清理未提交向量并写入 failed 和错误信息。"""
    session_factory = create_session_factory()
    with session_factory() as session:
        document = create_document(session, chunk_count=2)
        failing = FakeEmbeddings([[1.0] * 1024], failures=99)
        with pytest.raises(RuntimeError):
            embed_document_record(session, document.id, QwenEmbeddingClient(FakeClient(failing)))
        failed = session.get(Document, document.id)
        chunks = list(session.scalars(select(DocumentChunk)))
        assert failed.status == "failed"
        assert failed.parse_error == "Embedding 调用失败"
        assert all(chunk.embedding is None for chunk in chunks)
