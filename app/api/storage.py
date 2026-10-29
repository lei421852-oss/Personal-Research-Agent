"""独立连接诊断，不表示业务持久化、Schema或迁移已就绪。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.persistence.dependencies import get_database_resource

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("/health")
async def storage_health(resource: Annotated[object, Depends(get_database_resource)]):
    if resource.state == "disabled":
        return {"status": "disabled", "scope": "connection_only"}
    await resource.check_connection()
    return {"status": "ready", "scope": "connection_only"}
