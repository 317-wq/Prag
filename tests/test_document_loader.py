"""多格式文档解析 Loader 测试。"""

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Document, DocumentChunk, KnowledgeBase, User
from app.parsers.document_loader import DocumentLoader, DocumentParseError, OCR_UNSUPPORTED_MESSAGE, parse_document_record


class FakeNode:
    """模拟 DoclingReader 返回节点。"""

    def __init__(self, text: str, metadata: dict | None = None) -> None:
        """保存测试文本和元数据。"""
        self.text = text
        self.metadata = metadata or {}


class FakeDoclingReader:
    """模拟 DoclingReader，避免测试依赖真实二进制文档。"""

    def __init__(self, nodes: list[FakeNode]) -> None:
        """保存待返回节点列表。"""
        self.nodes = nodes

    def load_data(self, file_path):
        """返回预设解析节点，file_path 参数模拟真实接口。"""
        return self.nodes


def create_session_factory():
    """创建内存数据库会话工厂。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def create_document(session, file_path, file_type: str) -> Document:
    """创建带默认用户和知识库的文档记录。"""
    user = User(email=f"{uuid4()}@example.com", password_hash="hash")
    session.add(user)
    session.flush()
    knowledge_base = KnowledgeBase(user_id=user.id, name="默认知识库")
    session.add(knowledge_base)
    session.flush()
    document = Document(
        knowledge_base_id=knowledge_base.id,
        user_id=user.id,
        filename=file_path.name,
        file_type=file_type,
        file_size=file_path.stat().st_size,
        storage_path=str(file_path),
        status="uploaded",
    )
    session.add(document)
    session.commit()
    return document


def test_parse_md_extracts_title_and_sections(tmp_path):
    """Markdown 解析提取标题、章节和行号信息。"""
    file_path = tmp_path / "sample.md"
    file_path.write_text("# 主标题\n正文\n## 子章节\n更多内容", encoding="utf-8")
    result = DocumentLoader().parse(file_path, "md")

    assert result.chunk_count == 2
    assert result.documents[0].metadata["title"] == "主标题"
    assert result.documents[1].metadata["section"] == "子章节"
    assert result.documents[1].metadata["line"] == 3


def test_parse_txt_extracts_title_and_line(tmp_path):
    """TXT 解析使用首行作为标题并保留行号信息。"""
    file_path = tmp_path / "sample.txt"
    file_path.write_text("第一行标题\n第二行内容", encoding="utf-8")
    result = DocumentLoader().parse(file_path, "txt")

    assert result.chunk_count == 1
    assert result.documents[0].text == "第一行标题\n第二行内容"
    assert result.documents[0].metadata["title"] == "第一行标题"
    assert result.documents[0].metadata["line"] == 1


@pytest.mark.parametrize("file_type", ["pdf", "doc", "docx"])
def test_docling_formats_are_routed_by_extension(tmp_path, file_type):
    """PDF、DOC、DOCX 均通过 DoclingReader 路由并提取位置信息。"""
    file_path = tmp_path / f"sample.{file_type}"
    file_path.write_bytes(b"fake binary")
    reader = FakeDoclingReader([FakeNode("正文", {"title": "标题", "page_number": 2, "heading": "章节"})])
    result = DocumentLoader(docling_reader=reader).parse(file_path, file_type)

    assert result.chunk_count == 1
    assert result.documents[0].metadata["title"] == "标题"
    assert result.documents[0].metadata["page"] == 2
    assert result.documents[0].metadata["section"] == "章节"


def test_scanned_pdf_returns_ocr_unsupported(tmp_path):
    """扫描版 PDF 无文本时返回暂不支持 OCR。"""
    file_path = tmp_path / "scan.pdf"
    file_path.write_bytes(b"fake scan")
    loader = DocumentLoader(docling_reader=FakeDoclingReader([]))

    with pytest.raises(DocumentParseError, match=OCR_UNSUPPORTED_MESSAGE):
        loader.parse(file_path, "pdf")


def test_parse_document_record_writes_chunks(tmp_path):
    """解析成功时将文本分块写入 document_chunks。"""
    session_factory = create_session_factory()
    file_path = tmp_path / "chunk.md"
    file_path.write_text("# 标题\n第一段\n\n第二段", encoding="utf-8")
    with session_factory() as session:
        document = create_document(session, file_path, "md")
        parse_document_record(session, document.id)
        chunks = list(session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index)))
        session.refresh(document)
        assert document.status == "parsed"
        assert len(chunks) == 1
        assert chunks[0].document_id == document.id
        assert chunks[0].chunk_index == 0
        assert chunks[0].parent_content == "标题：标题"
        assert chunks[0].content == "# 标题\n第一段\n\n第二段"


def test_parse_failure_writes_parse_error(tmp_path):
    """解析失败时文档状态为 failed 并写入 parse_error。"""
    session_factory = create_session_factory()
    file_path = tmp_path / "empty.txt"
    file_path.write_text("", encoding="utf-8")
    with session_factory() as session:
        document = create_document(session, file_path, "txt")
        with pytest.raises(DocumentParseError):
            parse_document_record(session, document.id)
        session.refresh(document)
        assert document.status == "failed"
        assert document.parse_error == "未解析到有效文本"
