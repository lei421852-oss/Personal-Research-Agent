"""只获取应用资源；Session不会自动进入现有研究执行链。"""

from fastapi import Request

from app.persistence.errors import StorageError


def get_database_resource(request: Request):
    resource = getattr(request.app.state, "database", None)
    if resource is None:
        raise StorageError("storage_not_initialized")
    return resource


async def get_db_session(request: Request):
    resource = get_database_resource(request)
    async with resource.session() as session:
        yield session
