"""测试基础配置、应用启动、健康检查和静态资源托管。"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:password@localhost:5432/prag")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from fastapi.testclient import TestClient

from app.core.config import ConfigurationError, Settings
from app.main import create_app


def test_required_environment_validation_reports_missing_values():
    """缺少必填环境变量时返回明确变量名。"""
    settings = Settings(database_url="", qwen_api_key="", gpt_api_key="")

    try:
        settings.validate_required()
    except ConfigurationError as error:
        message = str(error)
    else:
        raise AssertionError("缺少必填配置时应抛出 ConfigurationError")

    assert "DATABASE_URL" in message
    assert "QWEN_API_KEY" in message
    assert "GPT_API_KEY" in message


def test_health_endpoint_returns_ok_and_request_id():
    """健康检查返回成功状态，并在响应头写入请求 ID。"""
    with TestClient(create_app()) as client:
        response = client.get("/health", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_frontend_index_is_served():
    """根路径返回前端静态首页。"""
    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Prag 知识库" in response.text
