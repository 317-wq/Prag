"""回答引用映射和持久化测试。"""

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, DocumentChunk, KnowledgeBase, QAReference, User
from app.services.citations import CitationError, map_citations, persist_citations
from app.services.retrieval import RetrievalItem


def create_item(index: int) -> RetrievalItem:
    """创建带唯一 ID 的检索结果。"""
    return RetrievalItem(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content=f"片段 {index}",
        parent_content=None,
        similarity=0.9 - index / 10,
        document_name=f"文档 {index}.md",
        chunk_index=index,
        page_number=index,
        section_title=f"章节 {index}",
        line_number=index * 10,
    )


def test_map_citations_uses_one_based_result_order_and_deduplicates():
    """引用编号按检索结果 1-based 映射，并保留回答中的首次顺序。"""
    result = map_citations("结论见 [2]，补充见 [1]，再次见 [2]。", [create_item(0), create_item(1)])

    assert result.referenced_numbers == [2, 1]
    assert [citation.number for citation in result.citations] == [2, 1]
    assert result.citations[0].document_name == "文档 1.md"
    assert result.citations[0].chunk_index == 1
    assert result.citations[0].snippet == "片段 1"
    assert result.citations[0].relevance_score == 0.8
    assert result.source_type_counts == {"document_chunk": 2}


def test_map_citations_rejects_unmatched_reference_number():
    """回答引用不存在的编号时拒绝映射，防止伪造引用。"""
    with pytest.raises(CitationError, match=r"引用编号 \[3\]"):
        map_citations("没有依据 [3]。", [create_item(0), create_item(1)])


def test_map_citations_without_references_returns_empty_result():
    """回答没有引用时返回空引用，不凭空创建引用记录。"""
    result = map_citations("没有显式引用。", [create_item(0)])

    assert result.citations == []
    assert result.referenced_numbers == []
    assert result.source_type_counts == {}


def test_persist_citations_writes_qa_references():
    """引用对象可以写入 qa_references 并保留来源字段。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as session:
        user = User(email=f"{uuid4()}@example.com", password_hash="hash")
        session.add(user)
        session.flush()
        knowledge_base = KnowledgeBase(user_id=user.id, name="默认知识库")
        session.add(knowledge_base)
        session.flush()
        document = Document(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            filename="来源.md",
            file_type="md",
            file_size=10,
            storage_path="/tmp/source.md",
            status="ready",
        )
        session.add(document)
        session.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            chunk_index=0,
            content="来源内容",
            embedding=[0.0] * 1024,
        )
        session.add(chunk)
        session.flush()
        citation = create_item(0)
        citation = citation.__class__(
            **{**citation.__dict__, "document_id": document.id, "chunk_id": chunk.id}
        )
        references = persist_citations(session, uuid4(), [
            map_citations("[1]", [citation]).citations[0]
        ])

        assert len(references) == 1
        assert references[0].citation_number == 1
        assert references[0].source_type == "document_chunk"
        assert references[0].document_id == document.id
        assert references[0].chunk_id == chunk.id
        assert references[0].snippet == "片段 0"
        assert references[0].relevance_score == 0.9
