"""文件上传、文档管理和校验测试。"""

import os
from pathlib import Path
from uuid import UUID, uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import create_app
from app.models import Document, DocumentChunk, KnowledgeBase, User


def create_test_client(upload_dir: Path):
    """创建使用内存数据库和临时上传目录的测试客户端。"""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings.upload_dir = upload_dir

    def override_get_db():
        """为测试请求提供隔离数据库会话。"""
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application = create_app()
    application.dependency_overrides[get_db] = override_get_db
    return TestClient(application), session_factory


def register_and_login(client: TestClient) -> dict[str, str]:
    """注册测试用户并返回认证请求头。"""
    payload = {"email": f"{uuid4()}@example.com", "password": "password-123"}
    client.post("/auth/register", json=payload)
    response = client.post("/auth/login", json=payload)
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_upload_list_detail_and_saved_file(tmp_path):
    """单文件上传后可查询列表和详情，原始文件保存到上传目录。"""
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        response = client.post(
            "/documents/upload",
            headers=headers,
            files={"file": ("../unsafe name.txt", b"hello prag", "text/plain")},
        )
        document_id = UUID(response.json()["id"])
        list_response = client.get("/documents", headers=headers)
        detail_response = client.get(f"/documents/{document_id}", headers=headers)

    assert response.status_code == 201
    assert response.json()["filename"] == "unsafe name.txt"
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert detail_response.status_code == 200
    with session_factory() as session:
        document = session.get(Document, document_id)
        knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
        assert Path(document.storage_path).read_bytes() == b"hello prag"
        assert knowledge_base.document_count == 1


def test_upload_validation_rejects_type_empty_and_size(tmp_path):
    """上传校验拒绝非法扩展名、空内容和超出大小限制的文件。"""
    client, _ = create_test_client(tmp_path)
    original_limit = settings.max_upload_size_mb
    settings.max_upload_size_mb = 0
    try:
        with client:
            headers = register_and_login(client)
            invalid_type = client.post(
                "/documents/upload",
                headers=headers,
                files={"file": ("bad.exe", b"content", "application/octet-stream")},
            )
            empty_file = client.post(
                "/documents/upload",
                headers=headers,
                files={"file": ("empty.txt", b"", "text/plain")},
            )
            too_large = client.post(
                "/documents/upload",
                headers=headers,
                files={"file": ("large.txt", b"content", "text/plain")},
            )
    finally:
        settings.max_upload_size_mb = original_limit

    assert invalid_type.status_code == 400
    assert empty_file.status_code == 400
    assert too_large.status_code == 413


def test_batch_upload_keeps_success_when_one_file_fails(tmp_path):
    """批量上传中单个文件失败不阻塞其他文件成功入库。"""
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        response = client.post(
            "/documents/batch-upload",
            headers=headers,
            files=[
                ("files", ("ok.txt", b"ok", "text/plain")),
                ("files", ("bad.exe", b"bad", "application/octet-stream")),
            ],
        )

    results = response.json()["results"]
    assert response.status_code == 200
    assert [item["success"] for item in results] == [True, False]
    with session_factory() as session:
        assert len(list(session.scalars(select(Document)))) == 1


def test_delete_removes_document_file_and_chunks(tmp_path):
    """删除文档时清理数据库记录、切片和原始文件。"""
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        upload = client.post(
            "/documents/upload",
            headers=headers,
            files={"file": ("delete.txt", b"delete me", "text/plain")},
        )
        document_id = UUID(upload.json()["id"])

    with session_factory() as session:
        document = session.get(Document, document_id)
        storage_path = Path(document.storage_path)
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                user_id=document.user_id,
                chunk_index=0,
                content="chunk",
            )
        )
        session.commit()

    with client:
        response = client.delete(f"/documents/{document_id}", headers=headers)

    assert response.status_code == 204
    assert not storage_path.exists()
    with session_factory() as session:
        assert session.get(Document, document_id) is None
        assert len(list(session.scalars(select(DocumentChunk)))) == 0


def test_reprocess_clears_chunks_and_resets_status(tmp_path):
    """重新处理文档时删除旧切片并将状态重置为 uploaded。"""
    client, session_factory = create_test_client(tmp_path)
    with client:
        headers = register_and_login(client)
        upload = client.post(
            "/documents/upload",
            headers=headers,
            files={"file": ("retry.txt", b"retry", "text/plain")},
        )
        document_id = UUID(upload.json()["id"])

    with session_factory() as session:
        document = session.get(Document, document_id)
        document.status = "failed"
        document.parse_error = "parse failed"
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                user_id=document.user_id,
                chunk_index=0,
                content="chunk",
            )
        )
        session.commit()

    with client:
        response = client.post(f"/documents/{document_id}/reprocess", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "uploaded"
    assert response.json()["parse_error"] is None
    with session_factory() as session:
        assert len(list(session.scalars(select(DocumentChunk)))) == 0
