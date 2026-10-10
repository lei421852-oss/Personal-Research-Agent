"""异步工作台与冻结同步ReAct循环的薄桥接；多论文研究是可选工具。"""

import asyncio
import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from app.evidence.corpus import PaperCorpus
from app.evidence.resolver import EvidenceResolver
from app.llm.provider import OpenAICompatibleLLMClient
from app.papers.catalog import PaperCatalog, load_paper_catalog
from app.papers.expansion import QueryExpander
from app.research.answer_first import assemble_answer, failure_answer
from app.research.async_tools import AsyncLLMAdapter
from app.research.query_language import QueryLanguageAdapter
from app.research.react_budget import ResearchBudgetView, UnifiedRunBudget
from app.research.react_policy import MainPolicyClient, MainResearchAgent
from app.research.report_language import ReportLanguageAdapter
from app.research.research_handoff import ResearchContext
from app.research.research_tool import MultiAgentResearchTool
from app.research.run_presenter import execution_events, present_execution
from app.research.runtime import ResearchRuntimeConfig, ResourceGate
from app.research.workbench_runtime import WorkbenchRuntime, _ObservedClient, _offload
from app.tools.base import Tool
from app.tools.executor import ToolExecutor
from app.tools.observations import ObservationIssue, ToolObservation
from app.tools.paper_registry import create_paper_registry
from app.vision.analysis import FigureAnalyzer
from app.vision.provider import OpenAICompatibleVisionClient


class AsyncDecisionBridge:
    """同步循环等待异步依赖；取消后仍等待真实在途操作收尾。"""

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.futures = set()
        self.tasks = set()
        self.lock = threading.Lock()

    def invoke(self, operation):
        async def run():
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                return await operation()
            finally:
                self.tasks.discard(task)

        future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        with self.lock:
            self.futures.add(future)
        try:
            return future.result()
        finally:
            with self.lock:
                self.futures.discard(future)

    def cancel(self):
        with self.lock:
            for future in tuple(self.futures):
                future.cancel()
        for task in tuple(self.tasks):
            task.cancel()

    async def drain(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)


class SyncModelProxy:
    def __init__(self, bridge, adapter):
        self.bridge, self.adapter = bridge, adapter

    def complete(self, *, messages, tools):
        return self.bridge.invoke(
            lambda: self.adapter.complete(messages=messages, tools=tools)
        )


class GuardedTool(Tool):
    """调用前共享预算，调用后只发布已校验的参数和实际观察。"""

    def __init__(self, delegate, budget, emit, source, context=None):
        self.delegate, self.budget, self.emit = delegate, budget, emit
        self.source, self.context = source, context
        self.name, self.description, self.input_model = (
            delegate.name,
            delegate.description,
            delegate.input_model,
        )

    def schema(self):
        return self.delegate.schema()

    def execute(self, arguments):
        if (
            self.context
            and self.context.explicit_scope
            and self.name
            in {"search_text", "read_page", "find_figure", "analyze_figure"}
        ):
            scopes = getattr(arguments, "paper_ids", None) or (
                [arguments.paper_id] if hasattr(arguments, "paper_id") else []
            )
            if self.name == "analyze_figure":
                scopes = [
                    self.delegate.resolver.figure(arguments.figure_evidence_id).paper_id
                ]
            if not scopes or not set(scopes).issubset(self.context.explicit_scope):
                return ToolObservation(
                    status="error",
                    tool_name=self.name,
                    summary="工具参数超出显式范围；未静默修改范围。",
                    payload={},
                    issue=ObservationIssue(code="invalid_arguments", retryable=False),
                )
        if not self.budget.reserve_tool(self.source):
            return ToolObservation(
                status="error",
                tool_name=self.name,
                summary="共享工具预算已耗尽，领域能力未执行。",
                payload={"issue_code": "tool_budget_exhausted"},
                issue=ObservationIssue(code="invalid_arguments", retryable=False),
            )
        if self.source == "research":
            return self.delegate.execute(arguments)
        role = (
            "PaperSearch"
            if self.name == "search_papers"
            else "QueryExpansion"
            if self.name == "expand_search_query"
            else "ResearchTool"
            if self.name == "run_multi_agent_research"
            else "Evidence"
        )
        node = f"action:{self.budget.tools_used}:{self.name}"
        started = perf_counter()
        self.emit(
            node_type=role,
            node_id=node,
            status="running",
            event_type="started",
            summary="已校验主Agent工具动作，正在实际执行。",
            details={
                "tool_name": self.name,
                "arguments": arguments.model_dump(mode="json"),
            },
            source="live",
        )
        try:
            observation = self.delegate.execute(arguments)
            self.emit(
                node_type=role,
                node_id=node,
                status="failed" if observation.status == "error" else "completed",
                event_type="failed" if observation.status == "error" else "completed",
                summary=observation.summary,
                details={
                    "tool_name": self.name,
                    "status": observation.status,
                    "issue_code": observation.issue.code if observation.issue else None,
                    "result_count": observation.result_count,
                    "paper_ids": [
                        p["paper_id"] for p in observation.payload.get("papers", [])
                    ],
                    "evidence_ids": [
                        e["evidence_id"]
                        for e in observation.payload.get("evidence", [])
                    ],
                    "latency_ms": (perf_counter() - started) * 1000,
                },
                source="live",
            )
            return observation
        except Exception:
            self.emit(
                node_type=role,
                node_id=node,
                status="failed",
                event_type="failed",
                summary="工具依赖或领域执行失败；内部异常不公开。",
                details={
                    "tool_name": self.name,
                    "issue_code": "tool_execution_failure",
                },
                source="live",
            )
            raise


class ContextExecutor(ToolExecutor):
    def __init__(self, registry, context, emit):
        super().__init__(registry)
        self.context = context
        self.emit = emit

    def execute(self, *args, **kwargs):
        observation = super().execute(*args, **kwargs)
        self.context.observations.append(observation)
        if observation.status != "success":
            self.emit(
                node_type="MainAgent",
                node_id="aggregate:main_agent",
                status="running",
                event_type="progress",
                summary=observation.summary,
                details={
                    "tool_name": observation.tool_name,
                    "status": observation.status,
                    "issue_code": observation.issue.code,
                    "result_count": observation.result_count,
                },
                source="live",
            )
        return observation


class ReActWorkbenchRuntime:
    """默认入口只有问题；每一步由原有工具循环驱动，不运行固定多Agent流程。"""

    main_agent_entry = True

    def __init__(
        self,
        dependencies,
        *,
        llm_factory=None,
        vision_factory=None,
        paper_retriever=None,
        paper_catalog=None,
        max_research_calls=1,
        max_tools=64,
    ):
        self.dependencies = dependencies
        self.llm_factory = llm_factory or OpenAICompatibleLLMClient
        self.vision_factory = vision_factory or OpenAICompatibleVisionClient
        self.paper_retriever = paper_retriever
        self.paper_catalog = paper_catalog
        if type(max_research_calls) is not int or max_research_calls != 1:
            raise ValueError("research_calls_must_be_bounded_to_one")
        self.max_research_calls, self.max_tools = max_research_calls, max_tools

    async def run(self, request, emit):
        started = perf_counter()
        budget = UnifiedRunBudget(request.max_model_calls, self.max_tools)
        bridge = AsyncDecisionBridge()
        adapters, clients, visions = [], [], []
        execution = {
            "question": request.question,
            "paper_ids": [],
            "status": "failed",
            "coverage_worker_enabled": False,
            "entry_kind": "react",
        }
        context, resolver, answer, agent, cancellation = None, None, None, None, None
        full_resources = None
        discovery = self.paper_retriever

        def config():
            candidate = getattr(self.dependencies, "config", None)
            return candidate() if callable(candidate) else candidate or {"runtime": {}}

        runtime_config, gate = None, None

        def model(role):
            settings = (
                self.dependencies.language_settings()
                if hasattr(self.dependencies, "language_settings")
                else self.dependencies.provider_settings()["LLM"]
            )
            raw = self.llm_factory(
                api_key=settings["API_KEY"],
                model=settings["MODEL"],
                base_url=settings["BASE_URL"],
                timeout=runtime_config.operation_timeout_seconds,
            )

            def model_emit(**event):
                if role in {"query_language", "report_language"}:
                    event["node_id"] = role
                elif role == "query_expansion":
                    event["node_id"] = f"action:{budget.tools_used}:expand_search_query"
                emit(**event)

            observed = _ObservedClient(raw, role, budget, model_emit)
            adapter = AsyncLLMAdapter(observed, gate)
            clients.append(observed)
            adapters.append(adapter)
            return adapter

        async def resources():
            nonlocal full_resources
            if full_resources is None:
                full_resources = await _offload(self.dependencies.prepare_resources)
            return full_resources

        async def text_search(query, paper_ids, top_k):
            async with gate.slot("retrieval"):
                resource = await resources()
                return await _offload(
                    lambda: resource[3].search_text(query, paper_ids, top_k)
                )

        class TextAccess:
            def search_text(self, query, paper_ids=None, top_k=3):
                return bridge.invoke(lambda: text_search(query, paper_ids, top_k))

        async def paper_search(query, top_k):
            nonlocal discovery
            async with gate.slot("retrieval"):
                if discovery is None:
                    embedding = (
                        getattr(full_resources[3], "embedding_model", None)
                        if full_resources
                        else None
                    )
                    discovery = await _offload(
                        lambda: self.dependencies.prepare_discovery_resources(embedding)
                    )
                return await _offload(lambda: discovery.search(query, top_k))

        class PaperAccess:
            def search(self, query, top_k=20):
                return bridge.invoke(lambda: paper_search(query, top_k))

        class VisionAccess:
            def analyze(inner, image_path, prompt):
                async def analyze():
                    settings = self.dependencies.provider_settings()["VLM"]
                    raw = self.vision_factory(
                        api_key=settings["API_KEY"],
                        model=settings["MODEL"],
                        base_url=settings["BASE_URL"],
                        timeout=runtime_config.operation_timeout_seconds,
                    )
                    visions.append(raw)
                    async with gate.slot("vlm"):
                        budget.reserve("vlm", role="main_vision")
                        emit(
                            node_type="Evidence",
                            node_id="main_vision",
                            status="running",
                            event_type="progress",
                            summary="正在执行图像分析。",
                            details={},
                            source="live",
                        )
                        return await _offload(lambda: raw.analyze(image_path, prompt))

                return bridge.invoke(analyze)

        async def child(handoff):
            try:
                resource = await resources()

                class ChildDependencies:
                    def prepare_resources(inner):
                        return resource

                    def provider_settings(inner):
                        return self.dependencies.provider_settings()

                child_runtime = WorkbenchRuntime(
                    ChildDependencies(),
                    llm_factory=self.llm_factory,
                    vision_factory=self.vision_factory,
                    shared_budget=ResearchBudgetView(budget),
                    shared_gate=gate,
                    tool_wrapper=lambda tool: GuardedTool(
                        tool, budget, emit, "research"
                    ),
                    allow_catalog_targets=True,
                )
                child_request = SimpleNamespace(
                    question=handoff.research_goal,
                    paper_ids=handoff.selected_paper_ids,
                    max_model_calls=budget.maximum,
                )
                # 原入口事件已是公开执行事实，直接同run_id投递，不添加伪阶段。
                before_llm, before_vlm = budget.llm_used, budget.vlm_used
                seen = {}
                seen_lock = threading.Lock()

                def child_emit(**event):
                    if event["node_type"] == "Synthesis":
                        event["node_id"] = "synthesis"
                    with seen_lock:
                        seen[event["node_id"]] = event
                    emit(**event)

                output = await child_runtime.run(child_request, child_emit)
                derived = [
                    e
                    for e in execution_events(output)
                    if e["details"].get("record_available") is not False
                ]
                finished_ids = {e["node_id"] for e in derived}
                for event in derived:
                    emit(**event)
                # 只关闭实际出现过的依赖节点；此终态不是需求审核通过的判断。
                for identity, event in seen.items():
                    if identity not in finished_ids and event["status"] == "running":
                        emit(
                            node_type=event["node_type"],
                            node_id=identity,
                            status="completed",
                            event_type="completed",
                            summary="已发生的模型依赖调用已收尾；需求结论见对应审核/恢复记录。",
                            details={"activity": "dependency_calls_drained"},
                            source="derived",
                            timestamp=None,
                        )
                # 子报告只报告本次子链消耗；主账本保留整个请求的真实总数。
                output["llm_calls_observed"] = budget.llm_used - before_llm
                output["vlm_calls_observed"] = budget.vlm_used - before_vlm
                output["model_calls_observed"] = (
                    output["llm_calls_observed"] + output["vlm_calls_observed"]
                )
                return output
            except asyncio.CancelledError as exc:
                snapshot = getattr(exc, "evaluation_snapshot", None)
                if snapshot:
                    report = present_execution(snapshot)
                    digest = hashlib.sha256(
                        json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                    context.reports["report_" + digest] = {
                        "execution": snapshot,
                        "report": report,
                        "sha256": digest,
                        "research_status": "cancelled",
                    }
                raise

        try:
            runtime_config = ResearchRuntimeConfig(**config().get("runtime", {}))
            gate = ResourceGate(runtime_config)
            try:
                candidate = getattr(self.dependencies, "corpus", None)
                corpus, _ = candidate() if callable(candidate) else (candidate, None)
                if corpus is None:
                    raise ValueError("no_corpus")
            except Exception:  # noqa: BLE001 - 直接回答可在领域资源未就绪时运行
                corpus = PaperCorpus((), (), {}, {}, ())
            try:
                from app.research.discovery_resources import load_index_catalog

                catalog = self.paper_catalog
                if catalog is None:
                    catalog, _ = load_index_catalog(
                        getattr(
                            self.dependencies,
                            "root",
                            Path(__file__).resolve().parents[2],
                        )
                    )
            except (ValueError, OSError, KeyError):
                try:
                    catalog = load_paper_catalog()
                except (ValueError, OSError):
                    catalog = PaperCatalog([])
            if any(i not in catalog.documents for i in request.paper_ids):
                raise ValueError("unknown_explicit_scope")
            question = request.question
            query_adapter = QueryLanguageAdapter()
            query_input = query_adapter.prepare(question, request.language_mode)
            if query_input:
                emit(
                    node_type="QueryLanguage",
                    node_id="query_language",
                    status="running",
                    event_type="started",
                    summary="正在适配中文问题，原始意图保留。",
                    details={},
                    source="live",
                )
                response = await model("query_language").complete(
                    messages=query_input[0], tools=query_input[1], attempt_budget=1
                )
                converted = query_adapter.parse_response(
                    question, response, request.language_mode
                )
                execution["query_language"] = converted.model_dump(mode="json")
                if converted.status == "failed":
                    raise ValueError("invalid_query_translation")
                question = converted.research_question
                emit(
                    node_type="QueryLanguage",
                    node_id="query_language",
                    status="completed",
                    event_type="completed",
                    summary="问题语言适配完成。",
                    details=execution["query_language"],
                    source="live",
                )
            context = ResearchContext(
                request.question,
                question,
                catalog,
                {e.paper_id for e in corpus.text_evidence},
                list(request.paper_ids),
            )
            resolver = EvidenceResolver(corpus, paper_catalog=catalog)
            expander = QueryExpander(SyncModelProxy(bridge, model("query_expansion")))
            registry = create_paper_registry(
                corpus,
                TextAccess(),
                analyzer=FigureAnalyzer(VisionAccess()),
                paper_retriever=PaperAccess(),
                paper_catalog=catalog,
                query_expander=expander,
            )
            registry.register(
                MultiAgentResearchTool(
                    context,
                    budget,
                    bridge.invoke,
                    child,
                    max_calls=self.max_research_calls,
                )
            )
            from app.tools.registry import ToolRegistry

            guarded = ToolRegistry()
            for schema in registry.schemas():
                guarded.register(
                    GuardedTool(
                        registry.get(schema["name"]), budget, emit, "main", context
                    )
                )
            executor = ContextExecutor(guarded, context, emit)
            proxy = SyncModelProxy(bridge, model("main_agent"))
            agent = MainResearchAgent(
                MainPolicyClient(proxy, context, budget), executor, resolver, context
            )
            emit(
                node_type="MainAgent",
                node_id="aggregate:main_agent",
                status="running",
                event_type="started",
                summary="主Agent已启动；下一动作由模型决定，多Agent研究不是固定流程。",
                details={"question": request.question},
                source="live",
            )

            def run_sync():
                result = agent.run(question)
                context.agent_result = result
                return result

            result = await _offload(
                run_sync, on_cancel=lambda: (budget.stop(), bridge.cancel())
            )
            execution["main_agent"] = {
                "status": result.status,
                "tool_calls": result.tool_calls,
                "decision_steps": result.model_calls,
                "stop_details": result.stop_details,
            }
            execution["main_agent"]["trace"] = [
                t.model_dump(mode="json") for t in result.state.trace
            ]
            answer = assemble_answer(
                result, agent.finish.proposal, context, resolver, budget
            )
            execution["status"] = (
                "failed" if answer["answer_status"] == "failed" else "completed"
            )
            emit(
                node_type="MainAgent",
                node_id="aggregate:main_agent",
                status="failed" if execution["status"] == "failed" else "completed",
                event_type="failed" if execution["status"] == "failed" else "completed",
                summary="主Agent已结束，答案来源与不足状态保留。",
                details={
                    "answer_status": answer["answer_status"],
                    "execution_mode": answer["execution_mode"],
                    "tool_calls": result.tool_calls,
                },
                source="live",
            )
            translator = ReportLanguageAdapter()
            original_answer = deepcopy(answer)
            execution["answer_first"] = answer
            execution["original_answer"] = original_answer
            translation = translator.prepare(answer)
            if translation:
                answer["language"] = {"status": "pending"}
                answer["chinese_report"] = None
                emit(
                    node_type="ReportLanguage",
                    node_id="report_language",
                    status="running",
                    event_type="started",
                    summary="答案已生成，正在准备中文展示；原文立即可查看。",
                    details={},
                    source="live",
                    report_snapshot=answer
                    | {"language": {"status": "pending"}, "chinese_report": None},
                )
                try:
                    response = await model("report_language").complete(
                        messages=translation[0], tools=translation[1], attempt_budget=1
                    )
                    language = translator.parse_response(answer, response)
                except Exception:  # noqa: BLE001 - 语言失败保留权威原文
                    language = translator.failure(
                        answer, "answer_language_dependency_failure", model_calls=0
                    )
                answer["language"] = {
                    k: v
                    for k, v in language.model_dump(mode="json").items()
                    if k != "display_report"
                }
                answer["chinese_report"] = (
                    language.display_report if language.status == "translated" else None
                )
                emit(
                    node_type="ReportLanguage",
                    node_id="report_language",
                    status="completed" if language.status == "translated" else "failed",
                    event_type="completed"
                    if language.status == "translated"
                    else "failed",
                    summary="中文展示已生成；答案本身没有独立语义审核。"
                    if language.status == "translated"
                    else "中文展示失败，原始答案与研究结果保留。",
                    details={"issue_code": language.issue_code},
                    source="live",
                )
            execution["answer_first"] = answer
            execution["original_answer"] = original_answer
            return execution
        except asyncio.CancelledError as exc:
            budget.stop()
            bridge.cancel()
            cancellation = exc
            execution["status"] = "cancelled"
            if answer is not None:
                answer["language"] = {
                    "status": "failed",
                    "issue_code": "answer_language_cancelled",
                }
                answer["chinese_report"] = None
            exc.evaluation_snapshot = execution
            raise
        except Exception:  # noqa: BLE001 - 对外仅保留安全错误码及部分记录
            execution["status"] = "failed"
            execution["issue_type"] = "main_agent_dependency_failure"
            return execution
        finally:
            if cancellation:
                bridge.cancel()
            await bridge.drain()
            for adapter in adapters:
                await adapter.drain()
            for client in clients:
                client.close()
            for vision in visions:
                vision.close()
            if "answer_first" not in execution:
                if context is None:
                    context = ResearchContext(
                        request.question, request.question, PaperCatalog([]), set()
                    )
                    resolver = EvidenceResolver(PaperCorpus((), (), {}, {}, ()))
                execution["answer_first"] = failure_answer(
                    context, resolver, budget, cancelled=bool(cancellation)
                )
            execution.update(
                model_calls_observed=budget.used,
                llm_calls_observed=budget.llm_used,
                vlm_calls_observed=budget.vlm_used,
                total_latency_ms=(perf_counter() - started) * 1000,
            )
            execution["answer_first"]["metrics"] = (
                execution["answer_first"]["metrics"]
                | budget.snapshot()
                | {
                    "total_latency_ms": execution["total_latency_ms"],
                    "main_tool_calls": context.agent_result.tool_calls
                    if context and context.agent_result
                    else 0,
                    "main_decision_steps": context.agent_result.model_calls
                    if context and context.agent_result
                    else 0,
                    "research_calls": context.research_calls if context else 0,
                }
            )
            execution["answer_first"]["trace"] = execution.get("main_agent", {}).get(
                "trace", []
            )
            if execution["answer_first"].get("chinese_report"):
                execution["answer_first"]["chinese_report"]["metrics"] = execution[
                    "answer_first"
                ]["metrics"]
            execution["handoffs"] = context.handoffs if context else []
            execution["paper_ids"] = list(
                dict.fromkeys(
                    i for h in execution["handoffs"] for i in h["selected_paper_ids"]
                )
            )
            budget.stop()
