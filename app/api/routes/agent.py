from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from app.api.runtime import AgentAPIRuntime
from app.api.schemas.agent import AgentRunRequest, AgentRunResponse

router = APIRouter()


class APIError(Exception):
    def __init__(self, status_code, code, message, *, retryable=False):
        self.status_code, self.code, self.message, self.retryable = (
            status_code,
            code,
            message,
            retryable,
        )


def get_runtime(request: Request):
    runtime = request.app.state.runtime
    if runtime is None:
        raise APIError(
            503,
            "service_unavailable",
            "服务尚未就绪，请检查本地模型配置或语料加载。",
            retryable=True,
        )
    return runtime


@router.post("/api/agent/run", response_model=AgentRunResponse)
def run_agent(
    body: AgentRunRequest, runtime: Annotated[AgentAPIRuntime, Depends(get_runtime)]
):
    try:
        return runtime.run(body.question.strip())
    except Exception as exc:
        # HTTP 边界统一处理意外异常，不能把原始异常直接作为 JSON 返回。
        raise APIError(
            500, "internal_error", "运行发生内部错误，请检查服务端配置与运行环境。"
        ) from exc


@router.get("/api/evidence/{evidence_id}/image")
def figure_image(
    evidence_id: str, runtime: Annotated[AgentAPIRuntime, Depends(get_runtime)]
):
    # 只查已登记的证据 ID；客户端不能选择文件系统路径。
    figure = next(
        (e for e in runtime.corpus.figure_evidence if e.evidence_id == evidence_id),
        None,
    )
    if figure is None:
        raise APIError(404, "figure_not_found", "图像证据不在当前语料库中。")
    if not Path(figure.asset_path).is_file():
        raise APIError(503, "asset_unavailable", "图像资源暂时不可用。", retryable=True)
    return FileResponse(
        figure.asset_path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )
