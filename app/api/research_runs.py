"""多论文研究专用 API；旧单问题 Agent API 不变。"""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response

from app.research.run_events import sanitize_public
from app.research.run_manager import MainAgentRunRequest

router = APIRouter(prefix="/api/research", tags=["research"])


def manager(request):
    return request.app.state.research_manager


@router.get("/papers")
def papers(request: Request):
    try:
        available = manager(request).dependencies.papers()
    except Exception:  # noqa: BLE001 - 无语料时仍可打开工作台并回放历史
        return {"papers": [], "availability": "unavailable"}
    # 目录中的论文不等于已具备正文证据；默认可选集合仍只取 Prepared Corpus。
    from app.papers.catalog import load_paper_catalog

    try:
        catalog = load_paper_catalog()
        prepared_ids = {paper["paper_id"] for paper in available}
        registered = [
            {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "availability": "prepared"
                if paper.paper_id in prepared_ids
                else "metadata_only",
            }
            for paper in catalog.documents.values()
        ]
    except Exception:  # noqa: BLE001 - 目录缺失不妨碍已有 Prepared 列表的只读展示
        registered = []
    return {
        "papers": sanitize_public(available),
        "availability": "prepared",
        "catalog": sanitize_public(registered),
    }


@router.get("/health")
def health(request: Request):
    return sanitize_public(manager(request).dependencies.health())


@router.post("/runs", status_code=202)
async def create_run(
    body: MainAgentRunRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    replay_only: Annotated[bool, Header(alias="Idempotency-Replay-Only")] = False,
):
    result = await manager(request).create(
        body, idempotency_key=idempotency_key, replay_only=replay_only
    )
    if isinstance(result, dict) and result.get("replayed"):
        # 重放不是新建：返回 200 并标记，避免调用方误以为又启动了一次研究。
        response.status_code = 200
        response.headers["Idempotency-Replayed"] = "true"
    return result


@router.get("/runs/{run_id}")
async def run_status(run_id: str, request: Request):
    # 持久化模式下以数据库为权威；禁用持久化时行为与内存版完全一致。
    return await manager(request).read_status(run_id)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0
):
    return await manager(request).read_events(run_id, after)


@router.get("/runs/{run_id}/report")
async def report(run_id: str, request: Request, response: Response):
    body, meta = await manager(request).read_report(run_id)
    if meta:
        # 版本与内容哈希走响应头：不改变既有报告 DTO，仍可离线复核。
        response.headers["X-Report-Revision"] = str(meta["revision"])
        response.headers["X-Report-Schema-Version"] = str(meta["schema_version"])
        response.headers["X-Report-Snapshot-Kind"] = meta["snapshot_kind"]
        response.headers["X-Report-Payload-SHA256"] = meta["payload_sha256"]
    return body


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request):
    return await manager(request).cancel(run_id)


@router.get("/replays")
def replays(request: Request):
    return {"cases": sanitize_public(manager(request).replays.list_cases())}


@router.post("/replays/{case_id}", status_code=201)
async def load_replay(case_id: str, request: Request):
    return await manager(request).replay(case_id)
