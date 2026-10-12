"""基础向量检索服务，负责查询向量生成和知识库切片召回。"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from time import perf_counter
from typing import Any
from uuid import UUID

from loguru import logger
from sqlalchemy import Float, Select, literal, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Document, DocumentChunk
from app.services.embedding import QwenEmbeddingClient


class RetrievalError(RuntimeError):
    """表示查询向量生成或向量检索失败。"""


@dataclass(frozen=True)
class RetrievalItem:
    """单条检索结果及其引用元数据。"""

    chunk_id: UUID
    document_id: UUID
    content: str
    parent_content: str | None
    similarity: float
    document_name: str
    chunk_index: int
    page_number: int | None
    section_title: str | None
    line_number: int | None


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的结果和统计信息。"""

    results: list[RetrievalItem]
    top_k: int
    min_score: float
    hit_count: int
    max_similarity: float | None
    duration_ms: int


class VectorRetriever:
    """按用户和知识库范围执行 pgvector 余弦相似度检索。"""

    def __init__(self, embedding_client: Any | None = None) -> None:
        """初始化检索器，可注入 Embedding 客户端以便测试。"""
        self.embedding_client = embedding_client or QwenEmbeddingClient()

    def search(
        self,
        db: Session,
        user_id: UUID,
        knowledge_base_id: UUID,
        question: str,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> RetrievalResult:
        """生成查询向量并返回满足范围、状态和阈值的切片。"""
        started_at = perf_counter()
        resolved_top_k = settings.retrieval_top_k if top_k is None else top_k
        resolved_min_score = settings.retrieval_min_score if min_score is None else min_score
        self._validate_inputs(question, resolved_top_k, resolved_min_score)
        normalized_question = question.strip()
        try:
            query_vectors = self.embedding_client.embed([normalized_question])
            if len(query_vectors) != 1:
                raise RetrievalError("查询向量生成失败")
            query_vector = query_vectors[0]
            self._validate_vector_dimension(query_vector)
            if db.bind.dialect.name == "postgresql":
                items = self._search_postgresql(
                    db, user_id, knowledge_base_id, query_vector, resolved_top_k, resolved_min_score
                )
            else:
                items = self._search_fallback(
                    db, user_id, knowledge_base_id, query_vector, resolved_top_k, resolved_min_score
                )
        except RetrievalError:
            raise
        except Exception as error:
            logger.bind(
                event="retrieval",
                user_id=str(user_id),
                knowledge_base_id=str(knowledge_base_id),
                top_k=resolved_top_k,
                hit_count=0,
                max_similarity=None,
                duration_ms=int((perf_counter() - started_at) * 1000),
                status="failed",
            ).error("retrieval failed")
            raise RetrievalError("向量检索失败") from error

        result = RetrievalResult(
            results=items,
            top_k=resolved_top_k,
            min_score=resolved_min_score,
            hit_count=len(items),
            max_similarity=items[0].similarity if items else None,
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        logger.bind(
            event="retrieval",
            user_id=str(user_id),
            knowledge_base_id=str(knowledge_base_id),
            top_k=result.top_k,
            hit_count=result.hit_count,
            max_similarity=result.max_similarity,
            duration_ms=result.duration_ms,
            status="success",
        ).info("retrieval completed")
        return result

    def _search_postgresql(
        self,
        db: Session,
        user_id: UUID,
        knowledge_base_id: UUID,
        query_vector: list[float],
        top_k: int,
        min_score: float,
    ) -> list[RetrievalItem]:
        """使用 pgvector cosine distance 运算符查询切片。"""
        from pgvector.sqlalchemy import Vector

        query_vector_value = literal(
            query_vector,
            type_=Vector(settings.qwen_embedding_dimension),
        )
        cosine_distance = DocumentChunk.embedding.op(
            "<=>",
            return_type=Float,
        )(query_vector_value)
        similarity = (1 - cosine_distance).label("similarity")
        statement = (
            select(DocumentChunk, Document.filename, similarity)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.knowledge_base_id == knowledge_base_id,
                DocumentChunk.domain_label == "general",
                Document.status == "ready",
                DocumentChunk.embedding.is_not(None),
                similarity >= min_score,
            )
            .order_by(similarity.desc())
            .limit(top_k)
        )
        rows = db.execute(statement).all()
        return [self._to_item(chunk, filename, float(score)) for chunk, filename, score in rows]

    def _search_fallback(
        self,
        db: Session,
        user_id: UUID,
        knowledge_base_id: UUID,
        query_vector: list[float],
        top_k: int,
        min_score: float,
    ) -> list[RetrievalItem]:
        """在 SQLite 测试环境用相同过滤条件和余弦公式执行检索。"""
        statement: Select[tuple[DocumentChunk, str]] = (
            select(DocumentChunk, Document.filename)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.knowledge_base_id == knowledge_base_id,
                DocumentChunk.domain_label == "general",
                Document.status == "ready",
                DocumentChunk.embedding.is_not(None),
            )
        )
        scored = [
            self._to_item(chunk, filename, self._cosine_similarity(query_vector, chunk.embedding))
            for chunk, filename in db.execute(statement).all()
        ]
        return sorted(
            (item for item in scored if item.similarity >= min_score),
            key=lambda item: item.similarity,
            reverse=True,
        )[:top_k]

    def _to_item(self, chunk: DocumentChunk, filename: str, similarity: float) -> RetrievalItem:
        """将 ORM 查询结果转换为稳定的检索响应对象。"""
        return RetrievalItem(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            content=chunk.content,
            parent_content=chunk.parent_content,
            similarity=similarity,
            document_name=filename,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            section_title=chunk.section_title,
            line_number=chunk.line_number,
        )

    def _validate_inputs(self, question: str, top_k: int, min_score: float) -> None:
        """校验问题、召回数量和最小相似度边界。"""
        if not question.strip():
            raise ValueError("问题不能为空")
        if top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        if not -1 <= min_score <= 1:
            raise ValueError("min_score 必须在 [-1, 1] 范围内")

    def _validate_vector_dimension(self, vector: list[float]) -> None:
        """校验查询向量维度与文档向量维度一致。"""
        if len(vector) != settings.qwen_embedding_dimension:
            raise RetrievalError("查询向量维度错误")

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        """计算测试数据库中的余弦相似度。"""
        left_norm = sqrt(sum(value * value for value in left))
        right_norm = sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
