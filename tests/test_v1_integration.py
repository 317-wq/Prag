"""V1 集成验收测试，覆盖真实文件上传、索引、问答和重启读取。"""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import create_app
from app.models import Document, DocumentChunk
from app.services.embedding import QwenEmbeddingClient
from app.parsers.schemas import ParsedDocument, ParseResult
from app.services.generation import GenerationResult
from app.services.retrieval import RetrievalResult


def create_pdf(path: Path) -> None:
    """写入包含一段真实文本的最小 PDF 文件。"""
    text = "Prag integration fact"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(text) + 44} >>\nstream\nBT /F1 18 Tf 20 80 Td ({text}) Tj ET\nendstream".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode())
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    content.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    content.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(content)


def create_docx(path: Path, text: str = "Prag integration fact") -> None:
    """写入包含一段真实文本的最小 DOCX 文件。"""
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:sectPr/></w:body></w:document>""",
    }
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def create_test_client(database_path: Path, upload_dir: Path):
    """创建连接持久化 SQLite 文件的测试客户端。"""
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings.upload_dir = upload_dir

    def override_get_db():
        """为集成测试请求提供数据库会话。"""
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application), session_factory


def register_and_login(client: TestClient) -> dict[str, str]:
    """注册并登录测试用户。"""
    payload = {"email": f"{uuid4()}@example.com", "password": "password-123"}
    assert client.post("/auth/register", json=payload).status_code == 201
    response = client.post("/auth/login", json=payload)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def fake_external_services(monkeypatch):
    """注入本地 embedding 和生成器，禁止集成测试访问外部 API。"""
    from app.api import chat as chat_api
    from app.api import documents as documents_api
    from app.services.embedding import embed_document_record
    from app.services import retrieval as retrieval_service
    from app.parsers.document_loader import DocumentLoader

    original_parse_with_docling = DocumentLoader._parse_with_docling

    def parse_doc_fixture(self, file_path, file_type):
        """仅为无法生成的 DOC 提供 fixture，PDF 和 DOCX 使用真实 Docling 解析。"""
        if file_type == "doc":
            return [ParsedDocument(text="Prag integration fact", metadata={"title": file_path.stem})]
        return original_parse_with_docling(self, file_path, file_type)

    class FakeEmbeddings:
        """按文本内容生成可检索的固定维度向量。"""

        def create(self, **kwargs):
            """为事实文本和事实问题返回相同向量。"""
            vectors = []
            for text in kwargs["input"]:
                value = 0.0 if "不存在" in text else 1.0
                vector = [value] + [0.0] * (settings.qwen_embedding_dimension - 1)
                vectors.append(SimpleNamespace(index=len(vectors), embedding=vector))
            return SimpleNamespace(data=vectors)

    class FakeClient:
        """提供 fake embeddings 资源。"""

        embeddings = FakeEmbeddings()

    def embed_with_fake(db, document_id):
        """使用 fake embedding 完成上传后的索引。"""
        return embed_document_record(db, document_id, QwenEmbeddingClient(FakeClient()))

    class FakeGenerator:
        """返回带引用标记的固定事实回答。"""

        def generate(self, question, retrieval_items):
            """根据检索结果生成带 [1] 的回答。"""
            assert retrieval_items
            return GenerationResult("Prag 的集成事实见 [1]。", "", 0, 0, False, True)

    monkeypatch.setattr(documents_api, "embed_document_record", embed_with_fake)
    monkeypatch.setattr(retrieval_service, "QwenEmbeddingClient", lambda: QwenEmbeddingClient(FakeClient()))
    monkeypatch.setattr(DocumentLoader, "_parse_with_docling", parse_doc_fixture)
    monkeypatch.setattr(chat_api, "AnswerGenerator", FakeGenerator)


def test_v1_upload_all_supported_files_and_read_after_restart(tmp_path, fake_external_services):
    """上传五种支持格式，验证 ready、切片、向量和应用重启后的可读性。"""
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "fact.md").write_text("# Fact\nPrag integration fact", encoding="utf-8")
    (files_dir / "fact.txt").write_text("Prag integration fact", encoding="utf-8")
    create_pdf(files_dir / "fact.pdf")
    create_docx(files_dir / "fact.docx")
    (files_dir / "fact.doc").write_bytes(b"Prag integration fact")
    database_path = tmp_path / "prag.sqlite"
    upload_dir = tmp_path / "uploads"
    client, session_factory = create_test_client(database_path, upload_dir)

    with client:
        headers = register_and_login(client)
        responses = [
            client.post(
                "/documents/upload",
                headers=headers,
                files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
            )
            for path in sorted(files_dir.iterdir())
        ]

    assert all(response.status_code == 201 for response in responses)
    document_ids = [UUID(response.json()["id"]) for response in responses]
    with session_factory() as session:
        documents = list(session.scalars(select(Document).where(Document.id.in_(document_ids))))
        assert {document.status for document in documents} == {"ready"}, [
            (document.filename, document.status, document.parse_error) for document in documents
        ]
        chunks = list(session.scalars(select(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids))))
        assert len(chunks) == 5
        assert all(chunk.embedding is not None for chunk in chunks)

    client, _ = create_test_client(database_path, upload_dir)
    with client:
        listed = client.get("/documents", headers=headers)
    assert listed.status_code == 200
    assert {item["status"] for item in listed.json()} == {"ready"}


def test_v1_chat_returns_citation_and_empty_search_skips_generator(tmp_path, fake_external_services, monkeypatch):
    """事实问题返回引用，未知问题返回无依据且不调用生成器。"""
    from app.api import chat as chat_api

    client, _ = create_test_client(tmp_path / "chat.sqlite", tmp_path / "uploads")
    with client:
        headers = register_and_login(client)
        upload = client.post(
            "/documents/upload",
            headers=headers,
            files={"file": ("fact.md", b"# Fact\nPrag integration fact", "text/markdown")},
        )
        assert upload.status_code == 201
        supported = client.post("/chat", headers=headers, json={"question": "Prag 的集成事实是什么？"})
        assert supported.status_code == 200
        assert supported.json()["citations"]

        class EmptyRetriever:
            """返回空检索结果的替身。"""

            def search(self, db, user_id, knowledge_base_id, question, top_k=None, min_score=None):
                """返回空结果，验证问答短路。"""
                return RetrievalResult([], top_k or 5, min_score or 0.3, 0, None, 0)

        class FailingGenerator:
            """空检索时若被调用则使测试失败。"""

            def generate(self, question, retrieval_items):
                """不应被调用。"""
                raise AssertionError("空检索不应调用 GPT")

        monkeypatch.setattr(chat_api, "VectorRetriever", EmptyRetriever)
        monkeypatch.setattr(chat_api, "AnswerGenerator", FailingGenerator)
        unsupported = client.post("/chat", headers=headers, json={"question": "不存在的内容"})

    assert unsupported.status_code == 200
    assert unsupported.json()["answer"] == "文档中没有足够依据"
    assert unsupported.json()["citations"] == []
