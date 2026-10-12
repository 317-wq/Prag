"""应用日志配置、标准库日志桥接和精简日志格式。"""

import logging
import sys
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.core.config import settings


class InterceptHandler(logging.Handler):
    """将标准库日志转发到 Loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        """转发一条标准库日志记录。"""
        logger.opt(exception=record.exc_info, depth=6).log(record.levelname, record.getMessage())


def _compact_format(record: dict[str, Any]) -> str:
    """生成短格式日志，保留时间、级别、事件和必要关联信息。"""
    extra = record["extra"]
    source = record["file"].name
    line = record["line"]
    fields = [
        f"event={extra['event']}" if extra.get("event") else "",
        f"request_id={extra['request_id']}" if extra.get("request_id") else "",
        f"status={extra['status']}" if extra.get("status") else "",
    ]
    context = " ".join(field for field in fields if field)
    message = str(record["message"]).replace("\n", " ")
    if len(message) > 160:
        message = message[:157] + "..."
    suffix = f" | {context}" if context else ""
    timestamp = record["time"].astimezone(ZoneInfo(settings.timezone))
    return f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} {record['level'].name:<8} {source}:{line} {message}{suffix}\n"


def configure_logging() -> None:
    """配置控制台和文件日志，统一使用年月日时分秒。"""
    settings.prepare_directories()
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=_compact_format,
        enqueue=True,
    )
    logger.add(
        settings.log_path,
        level=settings.log_level,
        format=_compact_format,
        encoding="utf-8",
        enqueue=True,
        rotation="00:00",
        retention="14 days",
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(logger_name).handlers = [InterceptHandler()]
        logging.getLogger(logger_name).propagate = False
