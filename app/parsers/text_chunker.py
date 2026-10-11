"""文本清洗与段落优先分块处理。"""

import re
from dataclasses import dataclass, field
from time import perf_counter
from uuid import UUID

from loguru import logger

from app.parsers.schemas import ParsedDocument


@dataclass
class TextChunk:
    """单个文本 chunk，包含父级上下文和位置信息。"""

    document_id: UUID
    chunk_index: int
    content: str
    parent_content: str
    metadata: dict[str, str | int | None] = field(default_factory=dict)


@dataclass
class ChunkResult:
    """文本分块结果，包含 chunk 列表、耗时和平均长度。"""

    chunks: list[TextChunk]
    duration_ms: int

    @property
    def chunk_count(self) -> int:
        """返回 chunk 数量。"""
        return len(self.chunks)

    @property
    def average_length(self) -> int:
        """返回 chunk 平均字符长度。"""
        if not self.chunks:
            return 0
        return int(sum(len(chunk.content) for chunk in self.chunks) / len(self.chunks))


class TextChunker:
    """清洗文本并按段落优先切分为可索引 chunk。"""

    def __init__(self, max_length: int, overlap: int) -> None:
        """初始化分块器，max_length 为最大长度，overlap 为相邻 chunk 重叠长度。"""
        if max_length <= 0:
            raise ValueError("chunk 最大长度必须大于 0")
        if overlap < 0 or overlap >= max_length:
            raise ValueError("chunk overlap 必须大于等于 0 且小于最大长度")
        self.max_length = max_length
        self.overlap = overlap

    def chunk(self, document_id: UUID, parsed_documents: list[ParsedDocument]) -> ChunkResult:
        """将解析结果转换为带 document_id 和 chunk_index 的 chunk 列表。"""
        start_time = perf_counter()
        chunks: list[TextChunk] = []
        for parsed_document in parsed_documents:
            cleaned_text = self.clean_text(parsed_document.text)
            if not cleaned_text:
                continue
            parent_content = self.build_parent_content(parsed_document.metadata)
            for content in self.split_by_paragraphs(cleaned_text):
                chunks.append(
                    TextChunk(
                        document_id=document_id,
                        chunk_index=len(chunks),
                        content=content,
                        parent_content=parent_content,
                        metadata=parsed_document.metadata.copy(),
                    )
                )
        duration_ms = int((perf_counter() - start_time) * 1000)
        result = ChunkResult(chunks=chunks, duration_ms=duration_ms)
        logger.bind(
            event="text_chunked",
            document_id=str(document_id),
            chunk_count=result.chunk_count,
            average_length=result.average_length,
            duration_ms=result.duration_ms,
        ).info("text chunked")
        return result

    def clean_text(self, text: str) -> str:
        """清理无意义空白和重复换行，保留段落边界。"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
        normalized = re.sub(r"[\t ]+", " ", normalized)
        normalized = re.sub(r" *\n *", "\n", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

    def build_parent_content(self, metadata: dict[str, str | int | None]) -> str:
        """根据标题、章节、页码等元数据构建父级上下文。"""
        parts: list[str] = []
        title = metadata.get("title")
        section = metadata.get("section")
        page = metadata.get("page")
        if title:
            parts.append(f"标题：{title}")
        if section and section != title:
            parts.append(f"章节：{section}")
        if page is not None:
            parts.append(f"页码：{page}")
        return " | ".join(parts)

    def split_by_paragraphs(self, text: str) -> list[str]:
        """按段落优先切分，超长段落再按最大长度和 overlap 切分。"""
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            paragraph_parts = self.split_long_text(paragraph)
            for part in paragraph_parts:
                if not current:
                    current = part
                elif len(current) + 2 + len(part) <= self.max_length:
                    current = f"{current}\n\n{part}"
                else:
                    chunks.append(current)
                    current = self.add_overlap(chunks[-1], part)
        if current:
            chunks.append(current)
        return chunks

    def split_long_text(self, text: str) -> list[str]:
        """将超过最大长度的单段文本按长度切分并保留 overlap。"""
        if len(text) <= self.max_length:
            return [text]
        parts: list[str] = []
        step = self.max_length - self.overlap
        start = 0
        while start < len(text):
            parts.append(text[start : start + self.max_length])
            if start + self.max_length >= len(text):
                break
            start += step
        return parts

    def add_overlap(self, previous: str, current: str) -> str:
        """在新 chunk 前添加上一 chunk 的尾部 overlap 内容。"""
        if self.overlap == 0:
            return current
        overlap_text = previous[-self.overlap :].strip()
        if not overlap_text:
            return current
        candidate = f"{overlap_text}\n\n{current}"
        if len(candidate) <= self.max_length:
            return candidate
        return candidate[-self.max_length :].lstrip()
