"""FastAPI 应用入口、统一异常处理、请求 ID 和结构化日志配置。"""

import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.config import ConfigurationError, settings


class InterceptHandler(logging.Handler):
    """将标准库日志转发到 Loguru。"""

    def emit(self, record: logging.LogRecord) -> None:
        """转发一条标准库日志记录。"""
        logger.opt(exception=record.exc_info).log(record.levelname, record.getMessage())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求注入请求 ID，并记录结构化访问日志。"""

    async def dispatch(self, request: Request, call_next):
        """处理请求并在响应头和日志中保留请求 ID。"""
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        request_logger = logger.bind(request_id=request_id, event="http_request")
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            request_logger.exception(
                "request failed",
                method=request.method,
                path=request.url.path,
                status="failed",
            )
            raise
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        request_logger.info(
            "request completed",
            method=request.method,
            path=request.url.path,
            status="success",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


def configure_logging() -> None:
    """配置 Loguru JSON 文件日志，并桥接 Uvicorn 等标准库日志。"""
    settings.prepare_directories()
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level, enqueue=True)
    logger.add(
        settings.log_path,
        level=settings.log_level,
        serialize=True,
        encoding="utf-8",
        enqueue=True,
        rotation="10 MB",
        retention="14 days",
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(logger_name).handlers = [InterceptHandler()]
        logging.getLogger(logger_name).propagate = False


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """在应用启动和关闭时执行基础生命周期逻辑。"""
        settings.validate_required()
        configure_logging()
        logger.bind(event="application_startup", status="success").info("application started")
        yield
        logger.bind(event="application_shutdown", status="success").info("application stopped")

    application = FastAPI(
        title="Prag Knowledge Base API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router)

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        application.mount("/static", StaticFiles(directory=frontend_dir), name="static")
        index_file = frontend_dir / "index.html"

        @application.get("/", include_in_schema=False)
        async def frontend_index():
            """返回前端首页静态文件。"""
            return FileResponse(index_file)

    @application.exception_handler(ConfigurationError)
    async def configuration_error_handler(_: Request, error: ConfigurationError):
        """将配置错误转换为安全的服务端错误响应。"""
        logger.bind(event="configuration_error", status="failed").error(str(error))
        return JSONResponse(status_code=500, content={"detail": "应用配置错误"})

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, error: RequestValidationError):
        """统一处理请求参数校验错误。"""
        return JSONResponse(status_code=422, content={"detail": error.errors()})

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, error: StarletteHTTPException):
        """统一处理 HTTP 异常响应。"""
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, error: Exception):
        """统一处理未捕获异常，响应中不暴露内部细节。"""
        logger.bind(event="unhandled_exception", status="failed").exception(str(error))
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})

    return application


app = create_app()
