"""数据库引擎、ORM 基类和请求级会话依赖。"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """应用 ORM 模型基类。"""


@lru_cache
def get_engine():
    """按当前配置创建并缓存 SQLAlchemy 引擎。"""
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory():
    """创建并缓存数据库会话工厂。"""
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def ensure_schema_compatibility() -> None:
    """补齐旧 PostgreSQL 数据卷缺失的文档切片元数据列。"""
    if get_engine().dialect.name != "postgresql":
        return
    statements = (
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_number INTEGER",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_title VARCHAR(500)",
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS line_number INTEGER",
    )
    with get_engine().begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    """为单个请求提供数据库会话，并在请求结束后关闭。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
