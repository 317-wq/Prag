"""多格式文档解析 Loader 和 DoclingReader 封装。"""

from pathlib import Path
from time import perf_counter

from llama_index.readers.docling import DoclingReader
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Document, DocumentChunk
from app.parsers.schemas import ParsedDocument, ParseResult
from app.parsers.text_chunker import TextChunker

SUPPORTED_PARSE_EXTENSIONS = {"md", "pdf", "doc", "docx", "txt"}
OCR_UNSUPPORTED_MESSAGE = "扫描版 PDF 暂不支持 OCR"



class DocumentParseError(Exception):
    """文档解析失败异常。"""


class DocumentLoader:
    """按扩展名路由 md、pdf、doc、docx、txt 文档解析。"""

    def __init__(self, docling_reader: DoclingReader | None = None) -> None:
        """初始化 Loader，docling_reader 参数用于注入或测试替身。"""
        self.docling_reader = docling_reader or DoclingReader()

    def parse(self, file_path: Path, file_type: str) -> ParseResult:
        """解析文件并返回统一 ParseResult。"""
        normalized_type = file_type.lower().lstrip(".")
        if normalized_type not in SUPPORTED_PARSE_EXTENSIONS:
            raise DocumentParseError("文件类型不支持解析")
        start_time = perf_counter()
        try:
            if normalized_type in {"md", "txt"}:
                parsed_documents = self._parse_text_file(file_path, normalized_type)
            else:
                parsed_documents = self._parse_with_docling(file_path, normalized_type)
        except DocumentParseError:
            raise
        except Exception as error:
            raise DocumentParseError(str(error)) from error
        if not parsed_documents:
            raise DocumentParseError("未解析到有效文本")
        duration_ms = int((perf_counter() - start_time) * 1000)
        return ParseResult(documents=parsed_documents, duration_ms=duration_ms)

    def _parse_text_file(self, file_path: Path, file_type: str) -> list[ParsedDocument]:
        """解析 md 或 txt 文档并提取标题、章节和行号信息。"""
        content = file_path.read_text(encoding="utf-8-sig").strip()
        if not content:
            raise DocumentParseError("未解析到有效文本")
        title = self._extract_title(content, file_path.stem, file_type)
        sections = self._split_markdown_sections(content) if file_type == "md" else [(title, content, 1)]
        return [
            ParsedDocument(
                text=section_text.strip(),
                metadata={"title": title, "section": section_title, "page": None, "line": line_number},
            )
            for section_title, section_text, line_number in sections
            if section_text.strip()
        ]

    def _parse_with_docling(self, file_path: Path, file_type: str) -> list[ParsedDocument]:
        """使用 DoclingReader 解析 pdf、doc 和 docx 文档。"""
        nodes = self.docling_reader.load_data(file_path=file_path)
        parsed_documents: list[ParsedDocument] = []
        for index, node in enumerate(nodes):
            text = getattr(node, "text", "") or ""
            metadata = dict(getattr(node, "metadata", {}) or {})
            if not text.strip():
                continue
            parsed_documents.append(
                ParsedDocument(
                    text=text.strip(),
                    metadata={
                        "title": metadata.get("title") or file_path.stem,
                        "section": metadata.get("section") or metadata.get("heading"),
                        "page": metadata.get("page") or metadata.get("page_number"),
                        "chunk_index": index,
                        "file_type": file_type,
                    },
                )
            )
        if file_type == "pdf" and not parsed_documents:
            raise DocumentParseError(OCR_UNSUPPORTED_MESSAGE)
        return parsed_documents

    def _extract_title(self, content: str, fallback: str, file_type: str) -> str:
        """从文本中提取标题，Markdown 优先使用一级标题，普通文本使用首行。"""
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if file_type == "md" and stripped.startswith("#"):
                return stripped.lstrip("#").strip() or fallback
            return stripped[:100]
        return fallback

    def _split_markdown_sections(self, content: str) -> list[tuple[str, str, int]]:
        """按 Markdown 标题拆出章节，返回章节名、正文和起始行号。"""
        sections: list[tuple[str, list[str], int]] = []
        current_title = "正文"
        current_lines: list[str] = []
        start_line = 1
        for line_number, line in enumerate(content.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                if current_lines:
                    sections.append((current_title, current_lines, start_line))
                current_title = line.lstrip("#").strip() or "未命名章节"
                current_lines = [line]
                start_line = line_number
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_title, current_lines, start_line))
        return [(title, "\n".join(lines), line_number) for title, lines, line_number in sections]


def parse_document_record(db: Session, document_id) -> ParseResult:
    """解析指定文档记录，成功更新状态，失败写入 parse_error。"""
    document = db.get(Document, document_id)
    if document is None:
        raise DocumentParseError("文档不存在")
    loader = DocumentLoader()
    try:
        result = loader.parse(Path(document.storage_path), document.file_type)
    except DocumentParseError as error:
        document.status = "failed"
        document.parse_error = str(error)
        db.commit()
        logger.bind(event="document_parse_failed", document_id=str(document.id), parse_error=str(error)).warning("document parse failed")
        raise
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(synchronize_session=False)
    chunk_result = TextChunker(settings.chunk_max_length, settings.chunk_overlap).chunk(document.id, result.documents)
    for chunk in chunk_result.chunks:
        db.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                user_id=document.user_id,
                domain_label=document.domain_label,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                parent_content=chunk.parent_content,
                token_count=len(chunk.content),
            )
        )
    document.status = "parsed"
    document.parse_error = None
    db.commit()
    logger.bind(
        event="document_parsed",
        document_id=str(document.id),
        duration_ms=result.duration_ms,
        parsed_chunk_count=result.chunk_count,
        chunk_count=chunk_result.chunk_count,
        average_length=chunk_result.average_length,
        chunk_duration_ms=chunk_result.duration_ms,
    ).info("document parsed")
    return result
