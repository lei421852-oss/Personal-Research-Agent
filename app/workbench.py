"""独立研究工作台入口；不修改被冻结的单问题应用。"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.research_runs import router
from app.api.storage import router as storage_router
from app.persistence.config import DatabaseSettings
from app.persistence.database import DatabaseResource
from app.persistence.errors import StorageError
from app.research.run_manager import ResearchRunManager, RunManagerError

WEB = Path(__file__).parent / "workbench_web"
LOGGER = logging.getLogger(__name__)


async def _shutdown_persistence(coordinator):
    """Manager 关闭后收口协调器；未完成收尾只记录，不静默丢弃。"""

    unfinished = await coordinator.shutdown()
    if unfinished:
        LOGGER.warning("persistence finalize unfinished: %s", ",".join(unfinished))


def create_app(*, manager_factory=None, database_settings=None, database_factory=None):
    @asynccontextmanager
    async def lifespan(application):
        # 后登记的Manager先关闭；即使构造或关闭失败，DB回调仍会执行。
        async with AsyncExitStack() as cleanup:
            settings = (
                DatabaseSettings.from_environment()
                if database_settings is None
                else database_settings
            )
            database = (database_factory or DatabaseResource)(settings)
            cleanup.push_async_callback(database.close)
            await database.start()
            application.state.database = database
            if manager_factory is None:
                from app.research.react_runtime import ReActWorkbenchRuntime
                from app.research.run_replay import ReplayRepository
                from app.research.workbench_dependencies import WorkbenchDependencies

                dependencies = WorkbenchDependencies()
                runtime = ReActWorkbenchRuntime(dependencies)
                lifecycle = None
                if settings.enabled:
                    # 只在启用时导入持久化栈：禁用模式不得把 SQLAlchemy 带进运行时。
                    from app.persistence.coordinator import (
                        PersistenceLatch,
                        TerminalCoordinator,
                    )
                    from app.persistence.lifecycle import RunPersistence
                    from app.persistence.repository import ResearchRepository

                    repository = ResearchRepository(database)
                    latch = PersistenceLatch()
                    coordinator = TerminalCoordinator(repository, latch=latch)
                    lifecycle = RunPersistence(
                        repository,
                        owner_instance_id=uuid4().hex,
                        coordinator=coordinator,
                        latch=latch,
                    )
                    # 先登记 database.close，再登记本回调：收尾在 Manager 之后、关库之前。
                    cleanup.push_async_callback(_shutdown_persistence, coordinator)
                    application.state.persistence = coordinator
                    application.state.persistence_lifecycle = lifecycle
                created = ResearchRunManager(
                    runtime,
                    dependencies,
                    ReplayRepository(Path(__file__).resolve().parents[1]),
                    lifecycle=lifecycle,
                )
            else:
                created = manager_factory()
            cleanup.push_async_callback(created.close)
            application.state.research_manager = created
            yield

    application = FastAPI(title="多模态论文研究工作台", lifespan=lifespan)
    application.include_router(router)
    application.include_router(storage_router)
    application.mount(
        "/workbench-static", StaticFiles(directory=WEB), name="workbench-static"
    )

    @application.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEB / "index.html", media_type="text/html")

    @application.exception_handler(StorageError)
    async def storage_error(request: Request, exc: StorageError):
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "error": exc.public_error()},
        )

    @application.exception_handler(RunManagerError)
    async def managed_error(request: Request, exc: RunManagerError):
        error = {"code": exc.code, "message": exc.message}
        # 只白名扩展两个字段：调用方据此判断能否原样重试，或只能核对。
        if exc.retry_mode is not None:
            error["retry_mode"] = exc.retry_mode
        if exc.run_id is not None:
            error["run_id"] = exc.run_id
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # 不回显 Pydantic 的 input/ctx，避免错误请求携带路径、凭据或异常对象。
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "请输入非空问题（最多4000字符）；可选API范围为1–3篇已登记论文，模型调用预算为1–200。界面只需输入问题。",
                }
            },
        )

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "工作台发生内部错误，未返回内部日志或凭据。",
                }
            },
        )

    return application
