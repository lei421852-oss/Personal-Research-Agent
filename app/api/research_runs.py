"""多论文研究专用 API；旧单问题 Agent API 不变。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

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
async def create_run(body: MainAgentRunRequest, request: Request):
    return await manager(request).create(body)


@router.get("/runs/{run_id}")
async def run_status(run_id: str, request: Request):
    return manager(request).status(run_id)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0
):
    return manager(request).events(run_id, after)


@router.get("/runs/{run_id}/report")
async def report(run_id: str, request: Request):
    return manager(request).report(run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request):
    return await manager(request).cancel(run_id)


@router.get("/replays")
def replays(request: Request):
    return {"cases": sanitize_public(manager(request).replays.list_cases())}


@router.post("/replays/{case_id}", status_code=201)
async def load_replay(case_id: str, request: Request):
    return await manager(request).replay(case_id)
