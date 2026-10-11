"""健康检查 API。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """返回应用进程可用状态。"""
    return {"status": "ok"}
