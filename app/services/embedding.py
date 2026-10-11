"""Qwen Embedding 客户端和文档切片向量化服务。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from time import perf_counter, sleep
from typing import Any
from uuid import UUID

from loguru import logger
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentChunk


class EmbeddingDimensionError(ValueError):
    """表示模型返回的向量维度与配置不一致。"""


class QwenEmbeddingClient:
    """通过 OpenAI 兼容接口调用 Qwen Embedding。"""

    def __init__(self, client: Any | None = None) -> None:
        """初始化客户端，可注入兼容 OpenAI embeddings 接口的 fake client。"""
        self.client = client or OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            timeout=settings.qwen_embedding_timeout_seconds,
            max_retries=0,
        )
        self.retry_count = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """按输入顺序批量生成向量，并校验每个向量为配置维度。"""
        if not texts:
            return []
        started_at = perf_counter()
        last_error: Exception | None = None
        for attempt in range(settings.qwen_embedding_max_retries + 1):
            try:
                response = self.client.embeddings.create(
                    model=settings.qwen_embedding_model,
                    input=list(texts),
                    dimensions=settings.qwen_embedding_dimension,
                    timeout=settings.qwen_embedding_timeout_seconds,
                )
                vectors = [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
                if len(vectors) != len(texts):
                    raise ValueError("Embedding 返回数量与输入数量不一致")
                for vector in vectors:
                    if len(vector) != settings.qwen_embedding_dimension:
                        raise EmbeddingDimensionError(
                            f"Embedding 维度为 {len(vector)}，期望 {settings.qwen_embedding_dimension}"
                        )
                logger.bind(
                    event="embedding_batch",
                    batch_size=len(texts),
                    dimension=settings.qwen_embedding_dimension,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    retry_count=attempt,
                    status="success",
                ).info("embedding batch completed")
                return vectors
            except EmbeddingDimensionError:
                raise
            except Exception as error:
                last_error = error
                if attempt >= settings.qwen_embedding_max_retries:
                    break
                self.retry_count += 1
                sleep(settings.qwen_embedding_retry_delay_seconds)
        logger.bind(
            event="embedding_batch",
            batch_size=len(texts),
            dimension=settings.qwen_embedding_dimension,
            duration_ms=int((perf_counter() - started_at) * 1000),
            retry_count=settings.qwen_embedding_max_retries,
            status="failed",
        ).error("embedding batch failed")
        raise RuntimeError("Embedding 调用失败") from last_error


def embed_document_record(db: Session, document_id: UUID, client: QwenEmbeddingClient | Any | None = None) -> Document:
    """批量向量化文档切片，成功置 ready，失败回滚向量并置 failed。"""
    document = db.scalar(select(Document).where(Document.id == document_id))
    if document is None:
        raise ValueError("文档不存在")
    embedding_client = client or QwenEmbeddingClient()
    try:
        chunks = list(
            db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        document.status = "indexing"
        document.parse_error = None
        for start in range(0, len(chunks), settings.qwen_embedding_batch_size):
            batch = chunks[start : start + settings.qwen_embedding_batch_size]
            vectors = embedding_client.embed([chunk.content for chunk in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                chunk.embedding = vector
        document.status = "ready"
        document.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(document)
        return document
    except Exception as error:
        db.rollback()
        document = db.get(Document, document_id)
        if document is not None:
            document.status = "failed"
            document.parse_error = str(error)
            document.processed_at = None
            db.commit()
        raise
