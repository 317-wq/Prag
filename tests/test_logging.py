"""精简日志格式测试。"""

import re
from datetime import datetime

from app.core.logging import _compact_format


def test_compact_format_uses_second_precision_and_short_context():
    """日志只保留年月日时分秒、级别、消息和必要上下文。"""
    output = _compact_format(
        {
            "time": datetime(2026, 10, 11, 12, 34, 56),
            "level": type("Level", (), {"name": "INFO"})(),
            "message": "request completed",
            "extra": {
                "event": "http_request",
                "request_id": "request-1",
                "status": "success",
                "duration_ms": 12.3,
                "path": "/health",
            },
        }
    )

    assert re.match(r"^2026-10-11 12:34:56 \| INFO     \| request completed", output)
    assert "event" not in output
    assert "duration_ms" not in output
    assert "path" not in output
    assert "request_id=request-1" in output
    assert "status=success" in output
    assert "{" not in output
