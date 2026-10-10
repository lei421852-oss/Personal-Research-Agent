"""Web 工作台薄适配：真实 Planner 与原 G2.1 研究链，所有新状态留在外层。"""

import asyncio
import threading
from time import perf_counter

from app.llm.provider import OpenAICompatibleLLMClient
from app.papers.catalog import load_paper_catalog
from app.research.async_tools import AsyncLLMAdapter
from app.research.planner import ResearchPlanner, ResearchPlannerError
from app.research.protocol_runtime import run_protocol_case
from app.research.retry import DependencyCallError
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.workbench_dependencies import LIMITS, WorkbenchUnavailable
from app.tools.redaction import public_value
from app.vision.provider import OpenAICompatibleVisionClient


class _RequestBudget:
    """每次物理请求开始前原子扣减，覆盖所有角色和 Adapter 的重试。"""

    def __init__(self, maximum):
        if type(maximum) is not int or not 1 <= maximum <= LIMITS["max_model_calls"]:
            raise ValueError("invalid_model_budget")
        self.maximum = maximum
        self.used = 0
        self.llm_used = 0
        self.vlm_used = 0
        self.rejected = False
        self.stopped = False
        self._lock = threading.Lock()

    def reserve(self, kind="llm"):
        if kind not in {"llm", "vlm"}:
            raise ValueError("invalid_model_resource")
        with self._lock:
            if self.stopped or self.used >= self.maximum:
                self.rejected = True
                raise DependencyCallError(code="budget_exhausted")
            self.used += 1
            if kind == "llm":
                self.llm_used += 1
            else:
                self.vlm_used += 1

    def stop(self):
        with self._lock:
            self.stopped = True


class _ObservedClient:
    """透明记录角色级真实调用；不读取隐藏推理，也不猜测 Worker 论文身份。"""

    def __init__(self, client, role, budget, emit):
        self.client, self.role, self.budget, self.emit = client, role, budget, emit

    @property
    def calls(self):
        return self.client.calls

    def complete(self, *, messages, tools):
        self.budget.reserve()
        role = {
            "planner": "Planner",
            "reviewer": "Reviewer",
            "synthesis": "Synthesis",
            "worker": "Worker",
            "recovery_worker": "Recovery",
        }[self.role]
        node = "planner" if role == "Planner" else "aggregate:" + self.role
        identity = _worker_schema_identity(tools)
        context = {"role": self.role}
        if identity and self.role in {"worker", "recovery_worker"}:
            # 身份由程序给出的单论文 schema 确定，不解析问题或模型提出的 scope。
            node = ("worker:" if role == "Worker" else "recovery_worker:") + identity
            context["paper_id"] = identity
        started = perf_counter()
        self.emit(
            node_type=role,
            node_id=node,
            status="running",
            event_type="progress",
            summary="真实模型调用已开始；此事件不代表该阶段完成。",
            details=context | {"activity": "model_request_started"},
            source="live",
        )
        try:
            response = self.client.complete(messages=messages, tools=tools)
            known_actions = {tool["name"] for tool in tools}
            self.emit(
                node_type=role,
                node_id=node,
                status="running",
                event_type="progress",
                summary="真实模型调用返回；工具动作尚需原 Controller 校验。",
                details=context
                | {
                    "activity": "model_request_returned",
                    "latency_ms": (perf_counter() - started) * 1000,
                    "requested_action_count": len(response.tool_calls),
                    "requested_actions": [
                        {
                            "tool_name": item.name
                            if item.name in known_actions
                            else "unknown_tool"
                        }
                        for item in response.tool_calls
                    ],
                },
                source="live",
            )
            return response
        except Exception:
            self.emit(
                node_type=role,
                node_id=node,
                status="running",
                event_type="progress",
                summary="真实模型调用失败；后续重试由原 Adapter 有界处理。",
                details=context
                | {
                    "activity": "model_request_failed",
                    "issue_code": "model_dependency_failure",
                    "latency_ms": (perf_counter() - started) * 1000,
                },
                source="live",
            )
            raise

    def close(self):
        self.client.close()


class _ObservedVision:
    def __init__(self, client, budget, emit):
        self.client, self.budget, self.emit = client, budget, emit

    def analyze(self, image_path, prompt):
        self.budget.reserve("vlm")
        self.emit(
            node_type="Worker",
            node_id="aggregate:worker",
            status="running",
            event_type="progress",
            summary="真实图像分析调用开始；不公开图像本地路径。",
            details={"activity": "vision_request_started"},
            source="live",
        )
        return self.client.analyze(image_path, prompt)

    def close(self):
        self.client.close()


class _ObservedRetriever:
    def __init__(self, retriever, emit):
        self.retriever, self.emit, self.records = retriever, emit, []
        self._lock = threading.Lock()

    def search_text(self, query, paper_ids=None, top_k=3):
        started = perf_counter()
        identity = _single_paper_id(paper_ids)
        node = "worker:" + identity if identity else "aggregate:worker"
        self.emit(
            node_type="Worker",
            node_id=node,
            status="running",
            event_type="progress",
            summary="真实 scoped 文本检索开始。",
            details={"query": query, "paper_ids": paper_ids, "top_k": top_k}
            | ({"paper_id": identity} if identity else {}),
            source="live",
        )
        record = {"query": query, "paper_ids": paper_ids, "top_k": top_k}
        if identity:
            record["paper_id"] = identity
        try:
            results = self.retriever.search_text(query, paper_ids, top_k)
            record.update(
                status="success",
                results=[item.model_dump(mode="json") for item in results],
            )
            return results
        except Exception:
            record.update(status="error", issue_code="retrieval_dependency_failure")
            raise
        finally:
            record["latency_ms"] = (perf_counter() - started) * 1000
            record = public_value(record)
            with self._lock:
                self.records.append(record)
            self.emit(
                node_type="Worker",
                node_id=node,
                status="running",
                event_type="progress",
                summary="真实检索结束；命中记录来自当前索引。",
                details=record,
                source="live",
            )


def _single_paper_id(identities):
    """仅接受已确定的单元素范围；不对论文名称进行推断、映射或纠正。"""
    if (
        isinstance(identities, list)
        and len(identities) == 1
        and isinstance(identities[0], str)
        and identities[0].strip()
        and identities[0] == identities[0].strip()
    ):
        return identities[0]
    return None


def _worker_schema_identity(tools):
    """读取原协议适配器的程序 schema，不读取模型提出的调用参数。"""
    for tool in tools:
        if tool.get("name") != "search_text":
            continue
        properties = tool.get("input_schema", {}).get("properties", {})
        scope = properties.get("paper_ids", {})
        if scope.get("type") != "array" or scope.get("maxItems") != 1:
            return None
        return _single_paper_id(scope.get("items", {}).get("enum"))
    return None


class _PlannerBridge:
    """同步 Planner 在独立线程运行；模型依赖仍走已有异步 Gate/Retry。"""

    def __init__(self, adapter, loop):
        self.adapter, self.loop = adapter, loop
        self._pending = set()
        self._lock = threading.Lock()

    def complete(self, *, messages, tools):
        future = asyncio.run_coroutine_threadsafe(
            self.adapter.complete(messages=messages, tools=tools), self.loop
        )
        with self._lock:
            self._pending.add(future)
        try:
            return future.result()
        finally:
            with self._lock:
                self._pending.discard(future)

    def cancel(self):
        with self._lock:
            pending = tuple(self._pending)
        for future in pending:
            future.cancel()


async def _offload(operation, on_cancel=None):
    """取消时等待同步操作真实结束，避免后台线程继续占用模型或文件资源。"""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel:
            on_cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


class WorkbenchRuntime:
    def __init__(self, dependencies, *, llm_factory=None, vision_factory=None):
        self.dependencies = dependencies
        self.llm_factory = llm_factory or OpenAICompatibleLLMClient
        self.vision_factory = vision_factory or OpenAICompatibleVisionClient

    async def run(self, request, emit):
        started = perf_counter()
        budget = _RequestBudget(request.max_model_calls)
        execution = {
            "question": request.question,
            "paper_ids": list(request.paper_ids),
            "coverage_worker_enabled": False,
            "protocol_adapter_enabled": True,
            "planner_model_calls": 0,
        }
        planner_client, adapter, bridge = None, None, None
        retriever = None
        try:
            corpus, snapshot, config, raw_retriever = await _offload(
                self.dependencies.prepare_resources
            )
            available = {item.paper_id for item in corpus.text_evidence}
            if any(identity not in available for identity in request.paper_ids):
                raise WorkbenchUnavailable("prepared_paper_unavailable")
            settings = self.dependencies.provider_settings()
            retriever = _ObservedRetriever(raw_retriever, emit)
            runtime = ResearchRuntimeConfig(**config["runtime"])
            gate = ResourceGate(runtime)

            def client(role):
                values = settings["LLM"]
                return _ObservedClient(
                    self.llm_factory(
                        api_key=values["API_KEY"],
                        model=values["MODEL"],
                        base_url=values["BASE_URL"],
                        timeout=runtime.operation_timeout_seconds,
                    ),
                    role,
                    budget,
                    emit,
                )

            def vision():
                values = settings["VLM"]
                return _ObservedVision(
                    self.vision_factory(
                        api_key=values["API_KEY"],
                        model=values["MODEL"],
                        base_url=values["BASE_URL"],
                        timeout=runtime.operation_timeout_seconds,
                    ),
                    budget,
                    emit,
                )

            planner_client = client("planner")
            adapter = AsyncLLMAdapter(planner_client, gate)
            bridge = _PlannerBridge(adapter, asyncio.get_running_loop())
            planner = ResearchPlanner(bridge, load_paper_catalog())
            emit(
                node_type="Planner",
                node_id="planner",
                status="running",
                event_type="started",
                summary="真实 Planner 已启动；不注入预写计划或参考答案。",
                details={"paper_ids": list(request.paper_ids)},
                source="live",
            )
            preparation = await _offload(
                lambda: planner.prepare(request.question, list(request.paper_ids)),
                on_cancel=lambda: (budget.stop(), bridge.cancel()),
            )
            execution["preparation"] = preparation.model_dump(mode="json")
            emit(
                node_type="Planner",
                node_id="planner",
                status="completed",
                event_type="completed",
                summary="真实 Planner 完成，结构化计划已通过既有校验。",
                details={
                    "plan": preparation.plan.model_dump(mode="json"),
                    "requirement_count": len(preparation.requirements),
                    "worker_task_count": len(preparation.worker_tasks),
                },
                source="live",
            )
            execution["research"] = await run_protocol_case(
                preparation.plan,
                corpus,
                snapshot,
                config,
                retriever,
                client,
                vision,
            )
            if budget.rejected:
                execution["research"].update(
                    status="execution_failed", issue_type="model_budget_exhausted"
                )
            # 返回本次专属对象，finally 补齐真实计数后调用方才拿到它。
            return execution
        except asyncio.CancelledError as exc:
            budget.stop()
            if bridge:
                bridge.cancel()
            execution["research"] = getattr(
                exc,
                "evaluation_snapshot",
                {"status": "cancelled", "issue_type": "cancelled"},
            )
            exc.evaluation_snapshot = execution
            raise
        except (WorkbenchUnavailable, ResearchPlannerError) as exc:
            execution["research"] = {
                "status": "execution_failed",
                "issue_type": exc.code,
            }
            return execution
        except Exception:  # noqa: BLE001 - API 只公开稳定错误，不透传底层异常
            execution["research"] = {
                "status": "execution_failed",
                "issue_type": "research_dependency_failure",
            }
            return execution
        finally:
            budget.stop()
            if adapter:
                await adapter.drain()
            if planner_client:
                execution["planner_model_calls"] = len(planner_client.calls)
                planner_client.close()
            if retriever:
                execution["retrievals"] = retriever.records
            execution["model_calls_observed"] = budget.used
            execution["llm_calls_observed"] = budget.llm_used
            execution["vlm_calls_observed"] = budget.vlm_used
            execution["model_budget_exhausted"] = budget.rejected
            execution["max_model_calls"] = budget.maximum
            execution["total_latency_ms"] = (perf_counter() - started) * 1000
