"""文件上传、文档查询、删除和重新处理接口。"""

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from loguru import logger
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth import CurrentUser
from app.config import settings
from app.database import get_db
from app.models import Document, DocumentChunk, KnowledgeBase, User
from app.parsers.document_loader import DocumentParseError, parse_document_record

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentResponse(BaseModel):
    """文档响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    file_type: str
    file_size: int
    status: str
    domain_label: str
    parse_error: str | None


class UploadItemResponse(BaseModel):
    """单个文件上传结果。"""

    filename: str
    success: bool
    document: DocumentResponse | None = None
    error: str | None = None


class BatchUploadResponse(BaseModel):
    """批量上传响应。"""

    results: list[UploadItemResponse]


def sanitize_filename(filename: str) -> str:
    """提取安全文件名，去掉路径并替换危险字符。"""
    safe_name = Path(filename).name.strip().replace("\\", "_").replace("/", "_")
    safe_name = "".join(char if char.isalnum() or char in {".", "_", "-", " ", "（", "）"} else "_" for char in safe_name)
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件名不合法")
    return safe_name


def validate_file_bytes(filename: str, content: bytes) -> tuple[str, int]:
    """校验扩展名、大小和内容，并返回文件类型与字节大小。"""
    safe_name = sanitize_filename(filename)
    file_type = Path(safe_name).suffix.lower().lstrip(".")
    file_size = len(content)
    max_size = settings.max_upload_size_mb * 1024 * 1024
    if file_type not in settings.allowed_extensions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件类型不支持")
    if file_size > max_size:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件大小超过限制")
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件内容不能为空")
    return file_type, file_size


def get_user_knowledge_base(db: Session, user: User) -> KnowledgeBase:
    """获取当前用户默认知识库。"""
    knowledge_base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.user_id == user.id))
    if knowledge_base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="默认知识库不存在")
    return knowledge_base


def build_storage_path(user_id: UUID, document_id: UUID, filename: str) -> Path:
    """按用户和文档 ID 生成原始文件存储路径。"""
    directory = settings.upload_dir / str(user_id) / str(document_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def enqueue_document_processing(document_id: UUID) -> None:
    """触发文档解析处理链，失败时写入 parse_error。"""
    from app.database import get_session_factory

    logger.bind(event="document_processing_enqueued", document_id=str(document_id)).info("document processing enqueued")
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            parse_document_record(session, document_id)
        except DocumentParseError:
            return
        except SQLAlchemyError as error:
            logger.bind(event="document_processing_failed", document_id=str(document_id)).warning(str(error))
            return
        except Exception as error:
            logger.bind(event="document_processing_failed", document_id=str(document_id)).warning(str(error))
            return


def save_upload_file(file: UploadFile, content: bytes, db: Session, user: User, background_tasks: BackgroundTasks) -> Document:
    """保存上传文件、创建文档记录并触发后台处理。"""
    safe_name = sanitize_filename(file.filename or "")
    file_type, file_size = validate_file_bytes(safe_name, content)
    knowledge_base = get_user_knowledge_base(db, user)
    document = Document(
        knowledge_base_id=knowledge_base.id,
        user_id=user.id,
        filename=safe_name,
        file_type=file_type,
        file_size=file_size,
        storage_path="",
        status="uploaded",
        domain_label="general",
    )
    db.add(document)
    db.flush()
    storage_path = build_storage_path(user.id, document.id, safe_name)
    storage_path.write_bytes(content)
    document.storage_path = str(storage_path)
    knowledge_base.document_count += 1
    db.commit()
    db.refresh(document)
    background_tasks.add_task(enqueue_document_processing, document.id)
    logger.bind(event="document_uploaded", filename=safe_name, file_size=file_size, status="success").info("document uploaded")
    return document


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    """上传单个文件并创建 uploaded 状态文档记录。"""
    content = await file.read()
    return save_upload_file(file, content, db, user, background_tasks)


@router.post("/batch-upload", response_model=BatchUploadResponse)
async def batch_upload_documents(
    files: Annotated[list[UploadFile], File()],
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> BatchUploadResponse:
    """批量上传文件，单个失败不影响其他文件。"""
    results: list[UploadItemResponse] = []
    for file in files:
        filename = sanitize_filename(file.filename or "")
        try:
            content = await file.read()
            document = save_upload_file(file, content, db, user, background_tasks)
            results.append(UploadItemResponse(filename=filename, success=True, document=document))
        except HTTPException as error:
            db.rollback()
            logger.bind(event="document_upload_failed", filename=filename, status="failed").warning(error.detail)
            results.append(UploadItemResponse(filename=filename, success=False, error=str(error.detail)))
    return BatchUploadResponse(results=results)


@router.get("", response_model=list[DocumentResponse])
def list_documents(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> list[Document]:
    """查询当前用户文档列表。"""
    return list(db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.uploaded_at.desc())))


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> Document:
    """查询当前用户单个文档详情。"""
    document = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> None:
    """删除文档记录、原始文件和关联切片。"""
    document = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    storage_path = Path(document.storage_path)
    db.delete(document)
    knowledge_base = get_user_knowledge_base(db, user)
    knowledge_base.document_count = max(0, knowledge_base.document_count - 1)
    db.commit()
    if storage_path.exists():
        storage_path.unlink()
    logger.bind(event="document_deleted", document_id=str(document_id), status="success").info("document deleted")


@router.post("/{document_id}/reprocess", response_model=DocumentResponse)
def reprocess_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> Document:
    """清理旧切片并重新触发文档处理链。"""
    document = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.id))
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(synchronize_session=False)
    document.status = "uploaded"
    document.parse_error = None
    document.processed_at = None
    db.commit()
    db.refresh(document)
    background_tasks.add_task(enqueue_document_processing, document.id)
    logger.bind(event="document_reprocess", document_id=str(document_id), status="success").info("document reprocess requested")
    return document
