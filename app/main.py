"""Local API/UI application; importing this module does not load or call models."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.agent import APIError, router
from app.api.runtime import create_configured_runtime
from app.api.schemas.agent import APIErrorResponse, ErrorDTO

WEB = Path(__file__).parent / "web"


def create_app(*, runtime_factory=None):
    @asynccontextmanager
    async def lifespan(application):
        try:
            application.state.runtime = (runtime_factory or create_configured_runtime)()
        except Exception:  # noqa: BLE001 - unavailable resources must leave local UI accessible
            application.state.runtime = None
        try:
            yield
        finally:
            runtime = application.state.runtime
            if runtime is not None:
                runtime.close()

    application = FastAPI(title="Multimodal Research Agent", lifespan=lifespan)
    application.state.runtime = None
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=WEB), name="static")

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", media_type="text/html")

    @application.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        envelope = APIErrorResponse(
            error=ErrorDTO(code=exc.code, message=exc.message, retryable=exc.retryable)
        )
        return JSONResponse(
            status_code=exc.status_code, content=envelope.model_dump(mode="json")
        )

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        envelope = APIErrorResponse(
            error=ErrorDTO(
                code="invalid_request",
                message="请求格式不正确，请输入非空问题（最多 4000 字符）。",
                fields=[
                    "body.question" if "question" in e["loc"] else "body"
                    for e in exc.errors()
                ],
            )
        )
        return JSONResponse(status_code=422, content=envelope.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        envelope = APIErrorResponse(
            error=ErrorDTO(
                code="internal_error", message="服务发生内部错误，请检查本地运行环境。"
            )
        )
        return JSONResponse(status_code=500, content=envelope.model_dump(mode="json"))

    return application


app = create_app()
