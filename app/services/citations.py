"""回答引用映射和 qa_references 持久化服务。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Iterable
from uuid import UUID

from loguru import logger
from sqlalchemy.orm import Session

from app.models import QAReference
from app.services.retrieval import RetrievalItem

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


class CitationError(ValueError):
    """表示回答中的引用编号无法关联到检索结果。"""


@dataclass(frozen=True)
class Citation:
    """一个可返回给客户端并可写入数据库的文档引用。"""

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


@dataclass(frozen=True)
class CitationMappingResult:
    """引用映射结果和统计信息。"""

    citations: list[Citation]
    referenced_numbers: list[int]
    source_type_counts: dict[str, int]
    duration_ms: int


def extract_citation_numbers(answer: str) -> list[int]:
    """提取回答中的引用编号并去重，保留首次出现顺序。"""
    numbers: list[int] = []
    seen: set[int] = set()
    for value in CITATION_PATTERN.findall(answer):
        number = int(value)
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    return numbers


def map_citations(answer: str, retrieval_items: list[RetrievalItem]) -> CitationMappingResult:
    """将回答中的 1-based 引用编号映射到实际检索结果。"""
    started_at = perf_counter()
    numbers = extract_citation_numbers(answer)
    if any(number < 1 or number > len(retrieval_items) for number in numbers):
        invalid = next(number for number in numbers if number < 1 or number > len(retrieval_items))
        raise CitationError(f"引用编号 [{invalid}] 没有对应的检索结果")

    citations = [
        _to_citation(number, retrieval_items[number - 1])
        for number in numbers
    ]
    source_type_counts = dict(Counter(citation.source_type for citation in citations))
    result = CitationMappingResult(
        citations=citations,
        referenced_numbers=numbers,
        source_type_counts=source_type_counts,
        duration_ms=int((perf_counter() - started_at) * 1000),
    )
    logger.bind(
        event="citation_mapping",
        citation_count=len(result.citations),
        source_type_counts=result.source_type_counts,
        duration_ms=result.duration_ms,
        status="success",
    ).info("citation mapping completed")
    return result


def persist_citations(
    db: Session,
    qa_message_id: UUID,
    citations: Iterable[Citation],
) -> list[QAReference]:
    """将文档引用写入 qa_references，并返回已 flush 的 ORM 对象。"""
    references = [
        QAReference(
            qa_message_id=qa_message_id,
            source_type=citation.source_type,
            document_id=citation.document_id,
            chunk_id=citation.chunk_id,
            snippet=citation.snippet,
            relevance_score=citation.relevance_score,
        )
        for citation in citations
    ]
    db.add_all(references)
    db.flush()
    logger.bind(
        event="citation_persistence",
        qa_message_id=str(qa_message_id),
        citation_count=len(references),
        source_type_counts=dict(Counter(reference.source_type for reference in references)),
        status="success",
    ).info("citations persisted")
    return references


def _to_citation(number: int, item: RetrievalItem) -> Citation:
    """将检索结果转换为引用对象。"""
    return Citation(
        number=number,
        source_type="document_chunk",
        document_id=item.document_id,
        chunk_id=item.chunk_id,
        document_name=item.document_name,
        chunk_index=item.chunk_index,
        snippet=item.content,
        relevance_score=item.similarity,
        page_number=item.page_number,
        section_title=item.section_title,
        line_number=item.line_number,
    )
