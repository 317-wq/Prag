"""知识库问答接口，串联检索、回答生成和引用持久化。"""

from __future__ import annotations

from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import CurrentUser
from app.core.database import get_db
from app.models import KnowledgeBase, QAMessage
from app.services.citations import CitationError, map_citations, persist_citations
from app.services.generation import AnswerGenerator, GenerationResult, NO_EVIDENCE_ANSWER
from app.services.retrieval import RetrievalError, VectorRetriever

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """问答请求。"""

    question: str = Field(min_length=1, max_length=10_000)
    knowledge_base_id: UUID | None = None
    top_k: int | None = Field(default=None, gt=0)
    min_score: float | None = Field(default=None, ge=-1, le=1)

    @property
    def normalized_question(self) -> str:
        """返回去除首尾空白的问题。"""
        return self.question.strip()


class CitationResponse(BaseModel):
    """问答响应中的引用。"""

    number: int
    source_type: str
    document_id: UUID
    chunk_id: UUID
    document_name: str
    chunk_index: int
    snippet: str
    relevance_score: float
    page_number: int | None
    section_title: str | None
    line_number: int | None


class ChatResponse(BaseModel):
    """问答接口响应。"""

    message_id: UUID
    answer: str
    citations: list[CitationResponse]
    confidence: float
    retrieval_hit_count: int
    duration_ms: int


def calculate_confidence(max_similarity: float | None, answer: str, citation_count: int) -> float:
    """根据最高相似度、回答状态和引用数量计算 0 到 1 的置信度。"""
    if max_similarity is None or answer == "文档中没有足够依据":
        return 0.0
    citation_factor = min(1.0, citation_count / 2) if citation_count else 0.5
    return round(max(0.0, min(1.0, max_similarity)) * citation_factor, 2)


def get_knowledge_base(db: Session, user_id: UUID, knowledge_base_id: UUID | None) -> KnowledgeBase:
    """获取当前用户指定或默认知识库，防止跨用户访问。"""
    statement = select(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
    if knowledge_base_id is not None:
        statement = statement.where(KnowledgeBase.id == knowledge_base_id)
    knowledge_base = db.scalar(statement)
    if knowledge_base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return knowledge_base


@router.post("", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatResponse:
    """执行一次知识库问答并保存问答及其引用。"""
    question = payload.normalized_question
    if not question:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="问题不能为空")
    started_at = perf_counter()
    knowledge_base = get_knowledge_base(db, user.id, payload.knowledge_base_id)
    try:
        retrieval = VectorRetriever().search(
            db,
            user.id,
            knowledge_base.id,
            question,
            top_k=payload.top_k,
            min_score=payload.min_score,
        )
        if retrieval.results:
            generation = AnswerGenerator().generate(question, retrieval.results)
        else:
            generation = GenerationResult(
                answer=NO_EVIDENCE_ANSWER,
                context="",
                context_token_count=0,
                duration_ms=0,
                retried=False,
                used_context=False,
            )
        mapping = map_citations(generation.answer, retrieval.results)
    except (RetrievalError, CitationError) as error:
        logger.bind(event="chat_failed", request_id=getattr(request.state, "request_id", None), status="failed").warning(str(error))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="问答服务暂时不可用") from error

    confidence = calculate_confidence(retrieval.max_similarity, generation.answer, len(mapping.citations))
    message = QAMessage(
        user_id=user.id,
        knowledge_base_id=knowledge_base.id,
        question=question,
        answer=generation.answer,
        confidence_score=confidence,
        answer_source="no_answer" if not retrieval.results else "knowledge_base",
    )
    db.add(message)
    db.flush()
    persist_citations(db, message.id, mapping.citations)
    db.commit()
    duration_ms = int((perf_counter() - started_at) * 1000)
    logger.bind(
        event="chat_completed",
        request_id=getattr(request.state, "request_id", None),
        retrieval_hit_count=retrieval.hit_count,
        confidence=confidence,
        no_evidence=generation.answer == "文档中没有足够依据",
        duration_ms=duration_ms,
        status="success",
    ).info("chat completed")
    return ChatResponse(
        message_id=message.id,
        answer=generation.answer,
        citations=[CitationResponse(**citation.__dict__) for citation in mapping.citations],
        confidence=confidence,
        retrieval_hit_count=retrieval.hit_count,
        duration_ms=duration_ms,
    )
