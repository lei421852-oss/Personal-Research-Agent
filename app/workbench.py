"""独立研究工作台入口；不修改被冻结的单问题应用。"""

from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

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
                created = ResearchRunManager(
                    runtime,
                    dependencies,
                    ReplayRepository(Path(__file__).resolve().parents[1]),
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
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

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
