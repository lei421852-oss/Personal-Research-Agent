"""项目记忆只读 API；作用范围由宿主配置，禁止请求指定。"""

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.memory.errors import MemoryFailure

router = APIRouter(prefix="/api/memories")
TYPES = {
    "preference",
    "project_decision",
    "project_fact",
    "workflow_lesson",
    "research_context",
}
STATUSES = {"all", "active", "superseded", "expired", "deleted"}


def error(status, code, message):
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def service(request):
    return getattr(request.app.state, "memory_service", None)


def valid_params(request, allowed):
    keys = list(request.query_params.keys())
    return all(
        key in allowed and len(request.query_params.getlist(key)) == 1 for key in keys
    )


@router.get("")
async def list_memories(request: Request):
    if not valid_params(
        request, {"query", "memory_type", "status", "page", "page_size"}
    ):
        return error(422, "invalid_arguments", "记忆查询参数无效。")
    args = request.query_params
    query = args.get("query", "").strip()
    memory_type = args.get("memory_type", "").strip()
    status = args.get("status", "active")
    try:
        raw_page, raw_size = args.get("page", "1"), args.get("page_size", "20")
        if not re.fullmatch(r"[0-9]{1,5}", raw_page) or not re.fullmatch(
            r"[0-9]{1,3}", raw_size
        ):
            raise ValueError
        page, page_size = int(raw_page), int(raw_size)
        if not (1 <= page <= 10000 and 1 <= page_size <= 100):
            raise ValueError
        if len(query) > 200 or any(ord(c) < 32 for c in query):
            raise ValueError
        if status not in STATUSES or (memory_type and memory_type not in TYPES):
            raise ValueError
    except ValueError:
        return error(422, "invalid_arguments", "记忆查询参数无效。")
    current = service(request)
    if current is None:
        return error(503, "memory_unavailable", "记忆功能未启用或不可用。")
    try:
        items, total = await current.list_for_display(
            query=query,
            memory_type=memory_type,
            status=status,
            page=page,
            page_size=page_size,
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except MemoryFailure:
        return error(503, "memory_unavailable", "记忆数据暂时不可用。")


@router.get("/stats")
async def memory_stats(request: Request):
    if not valid_params(request, set()):
        return error(422, "invalid_arguments", "记忆查询参数无效。")
    current = service(request)
    if current is None:
        return error(503, "memory_unavailable", "记忆功能未启用或不可用。")
    try:
        return await current.display_stats()
    except MemoryFailure:
        return error(503, "memory_unavailable", "记忆数据暂时不可用。")


@router.get("/{memory_id}")
async def memory_detail(memory_id: str, request: Request):
    if not valid_params(request, set()):
        return error(422, "invalid_arguments", "记忆查询参数无效。")
    if not re.fullmatch(r"mem_[0-9a-f]{32}", memory_id):
        return error(404, "memory_not_found", "未找到该记忆。")
    current = service(request)
    if current is None:
        return error(503, "memory_unavailable", "记忆功能未启用或不可用。")
    try:
        result = await current.display_detail(memory_id)
        if result is None:
            return error(404, "memory_not_found", "未找到该记忆。")
        return result
    except MemoryFailure as exc:
        if exc.code == "memory_scope_forbidden":
            return error(404, "memory_not_found", "未找到该记忆。")
        return error(503, "memory_unavailable", "记忆数据暂时不可用。")
