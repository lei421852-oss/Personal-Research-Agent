"""用 Fake Provider 验证薄适配仍走真实领域链；不作为论文语义评分。"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.evidence.models import RetrievedEvidence
from app.llm.fake import ScriptedLLMClient
from app.research.retry import DependencyCallError
from app.research.workbench_dependencies import WorkbenchDependencies
from app.research.workbench_runtime import (
    WorkbenchRuntime,
    _ObservedClient,
    _ObservedVision,
    _RequestBudget,
)
from scripts.evaluate_cross_paper import action, scripted_report


class FakeDependencies:
    def __init__(self):
        dependency = WorkbenchDependencies()
        self.config = dependency.config()
        self.corpus, self.snapshot = dependency.corpus(self.config)

    def prepare_resources(self):
        corpus = self.corpus

        class Retriever:
            def search_text(self, query, paper_ids, top_k):
                item = next(
                    e for e in corpus.text_evidence if e.paper_id == paper_ids[0]
                )
                return [RetrievedEvidence(evidence=item, retrieval_score=1.0)]

        return self.corpus, self.snapshot, self.config, Retriever()

    def provider_settings(self):
        return {
            kind: {
                "API_KEY": "fake",
                "MODEL": "fake",
                "BASE_URL": "https://fake.invalid",
            }
            for kind in ("LLM", "VLM")
        }


def clients(corpus, *, reject_initial=False):
    created = []
    seen_reviews = set()

    def policy(messages, tools, local):
        names = {tool["name"] for tool in tools}
        content = json.loads(messages[-1]["content"])
        if "submit_research_dimensions" in names:
            return action(
                "submit_research_dimensions",
                {
                    "dimensions": [
                        {
                            "dimension_id": "design",
                            "name": "Design",
                            "description": "Investigate design.",
                            "research_question": "What design does this paper document?",
                        }
                    ]
                },
            )
        if "submit_report" in names:
            return scripted_report(messages, tools)
        if "submit_review" in names:
            identity = content["requirement"]["requirement_id"]
            rejected = reject_initial and identity not in seen_reviews
            seen_reviews.add(identity)
            return action(
                "submit_review",
                {
                    "status": "rejected" if rejected else "passed",
                    "reason_code": "evidence_partially_supports_claim"
                    if rejected
                    else "evidence_supports_claim",
                },
            )
        paper = content["paper_id"]
        if local.steps == 1:
            return action(
                "search_text",
                {"query": "Prepared design evidence", "paper_ids": [paper], "top_k": 1},
            )
        evidence = next(e for e in corpus.text_evidence if e.paper_id == paper)
        prefix = "Narrowed statement: " if "recovery" in content else ""
        return action(
            "resolve_requirement",
            {
                "requirement_id": content["unresolved_requirements"][0][
                    "requirement_id"
                ],
                "status": "supported",
                "claim": prefix + evidence.text,
                "evidence_ids": [evidence.evidence_id],
            },
        )

    class Client(ScriptedLLMClient):
        def __init__(self, **kwargs):
            self.steps = 0
            self.closed = False
            super().__init__([])
            created.append(self)

        def complete(self, *, messages, tools):
            self.steps += 1
            self.calls.append({"status": "success"})
            return policy(messages, tools, self)

        def close(self):
            self.closed = True

    class Vision:
        def __init__(self, **kwargs):
            pass

        def analyze(self, image_path, prompt):
            raise AssertionError("Scripted text-only case must not call VLM")

        def close(self):
            pass

    return Client, Vision, created


def request(maximum=200):
    return SimpleNamespace(
        question="Compare prepared design evidence.",
        paper_ids=["blip-2-2301.12597v1", "flamingo-2204.14198v1"],
        max_model_calls=maximum,
    )


def test_real_pipeline_assembly_with_fake_provider_retains_verified_claims():
    dependencies = FakeDependencies()
    llm, vision, created = clients(dependencies.corpus)
    events = []
    runtime = WorkbenchRuntime(dependencies, llm_factory=llm, vision_factory=vision)
    result = asyncio.run(runtime.run(request(), lambda **event: events.append(event)))
    research = result["research"]
    assert research["status"] == "completed"
    assert research["worker_classes_observed"] == ["PaperResearchWorker"] * 2
    assert research["coverage_worker_enabled"] is False
    assert research["outcome"]["report"]["metrics"]["verified_requirements"] == 2
    assert result["planner_model_calls"] == 1
    assert result["model_calls_observed"] == 8
    assert result["llm_calls_observed"] == 8
    assert result["vlm_calls_observed"] == 0
    assert len(result["retrievals"]) == 2
    assert result["total_latency_ms"] > 0
    assert all(client.closed for client in created)
    assert all(event["source"] == "live" for event in events)
    assert {event["node_id"] for event in events if event["node_type"] == "Worker"} == {
        "worker:" + identity for identity in request().paper_ids
    }
    assert any(
        event["node_type"] == "Planner" and event["event_type"] == "completed"
        for event in events
    )
    assert not any(event["node_type"] == "Validator" for event in events)


def test_reviewer_rejection_still_uses_original_bounded_recovery():
    dependency = FakeDependencies()
    llm, vision, _ = clients(dependency.corpus, reject_initial=True)
    result = asyncio.run(
        WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision).run(
            request(), lambda **_: None
        )
    )
    assert all(
        item["status"] == "rejected"
        for item in result["research"]["initial_review_results"]
    )
    assert result["research"]["outcome"]["metrics"]["recovery_rounds_used"] == 1
    assert result["research"]["outcome"]["metrics"]["recovery_passed_count"] == 2


def test_overall_budget_includes_planner_and_never_calls_provider_after_limit():
    dependency = FakeDependencies()
    llm, vision, created = clients(dependency.corpus)
    result = asyncio.run(
        WorkbenchRuntime(dependency, llm_factory=llm, vision_factory=vision).run(
            request(1), lambda **_: None
        )
    )
    assert result["model_calls_observed"] == 1
    assert sum(len(client.calls) for client in created) == 1
    assert result["model_budget_exhausted"]
    assert result["research"]["issue_type"] == "model_budget_exhausted"
    assert result["research"]["outcome"]["report"]["missing_evidence"]


def test_shared_physical_budget_is_atomic_across_threads():
    budget = _RequestBudget(7)

    def reserve(_):
        try:
            budget.reserve()
            return True
        except DependencyCallError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(reserve, range(100)))
    assert sum(accepted) == 7
    assert budget.used == 7
    assert budget.rejected


@pytest.mark.parametrize("maximum", [0, 201, True, 1.0])
def test_invalid_overall_budget_rejected(maximum):
    with pytest.raises(ValueError):
        _RequestBudget(maximum)


def test_cancelled_planner_drains_inflight_thread_before_client_close():
    async def scenario():
        dependency = FakeDependencies()
        started, release = threading.Event(), threading.Event()
        created = []

        class WaitingClient:
            def __init__(self, **kwargs):
                self.calls = []
                self.closed = False
                created.append(self)

            def complete(self, **kwargs):
                self.calls.append({"status": "waiting"})
                started.set()
                release.wait(timeout=5)
                raise ConnectionError("Fake private path /home/secret and key")

            def close(self):
                self.closed = True

        runtime = WorkbenchRuntime(dependency, llm_factory=WaitingClient)
        task = asyncio.create_task(runtime.run(request(), lambda **_: None))
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert not created[0].closed
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert created[0].closed
        assert raised.value.evaluation_snapshot["research"]["status"] == "cancelled"
        assert raised.value.evaluation_snapshot["planner_model_calls"] == 1

    asyncio.run(scenario())


def test_initialization_error_is_sanitized_and_does_not_call_provider():
    class Broken:
        def prepare_resources(self):
            raise OSError("private /home/secret key=secret")

    result = asyncio.run(WorkbenchRuntime(Broken()).run(request(), lambda **_: None))
    assert result["research"]["issue_type"] == "research_dependency_failure"
    assert result["model_calls_observed"] == 0
    assert "secret" not in json.dumps(result)


def test_worker_cancellation_preserves_completed_peer_and_drains_clients():
    async def scenario():
        dependency = FakeDependencies()
        base, vision, created = clients(dependency.corpus)
        started, release = threading.Event(), threading.Event()

        class WaitingWorker(base):
            def complete(self, *, messages, tools):
                content = json.loads(messages[-1]["content"])
                if content.get("paper_id") == "flamingo-2204.14198v1":
                    self.calls.append({"status": "waiting"})
                    started.set()
                    release.wait(timeout=5)
                    raise ConnectionError("Fake cancelled in-flight operation")
                return super().complete(messages=messages, tools=tools)

        runtime = WorkbenchRuntime(
            dependency, llm_factory=WaitingWorker, vision_factory=vision
        )
        task = asyncio.create_task(runtime.run(request(), lambda **_: None))
        await asyncio.to_thread(started.wait, 5)
        assert started.is_set()
        # 等待同伴实际提交完成动作；这只是 Fake 工程断言，不模拟阶段时间。
        for _ in range(100):
            if any(client.steps >= 2 for client in created):
                break
            await asyncio.sleep(0.001)
        assert any(client.steps >= 2 for client in created)
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        result = raised.value.evaluation_snapshot
        workers = result["research"]["initial_worker_results"]
        assert any(worker["status"] == "completed" for worker in workers)
        assert any(worker["status"] == "cancelled" for worker in workers)
        assert all(client.closed for client in created)
        assert result["model_calls_observed"] == sum(
            len(client.calls) for client in created
        )

    asyncio.run(scenario())


def test_adapter_retry_is_included_in_overall_physical_budget():
    dependency = FakeDependencies()
    base, vision, created = clients(dependency.corpus)
    transient_used = False

    class Transient(base):
        def complete(self, *, messages, tools):
            nonlocal transient_used
            content = json.loads(messages[-1]["content"])
            if content.get("paper_id") and not transient_used:
                transient_used = True
                self.calls.append({"status": "transient_failure"})
                raise ConnectionError("Fake temporary connection failure")
            return super().complete(messages=messages, tools=tools)

    runtime = WorkbenchRuntime(dependency, llm_factory=Transient, vision_factory=vision)
    result = asyncio.run(runtime.run(request(8), lambda **_: None))
    assert transient_used
    assert result["model_calls_observed"] == 8
    assert sum(len(client.calls) for client in created) == 8
    assert result["model_budget_exhausted"]
    assert result["research"]["status"] == "execution_failed"
    assert all(client.closed for client in created)


def _schema(paper_ids):
    return [
        {
            "name": "search_text",
            "input_schema": {
                "properties": {
                    "paper_ids": {
                        "type": "array",
                        "maxItems": 1,
                        "items": {"enum": paper_ids},
                    }
                }
            },
        }
    ]


def test_unvalidated_model_arguments_never_enter_live_events():
    events = []
    response = action(
        "search_text",
        {
            "work_notes": "unvalidated-private-data",
            "unexpected_field": "hidden-value",
            "paper_ids": ["flamingo-2204.14198v1"],
        },
    )
    provider = ScriptedLLMClient([response])
    client = _ObservedClient(
        provider, "worker", _RequestBudget(3), lambda **event: events.append(event)
    )
    returned = client.complete(messages=[], tools=_schema(["blip-2-2301.12597v1"]))
    serialized = json.dumps(events)
    assert "work_notes" not in serialized
    assert "unvalidated-private-data" not in serialized
    assert "unexpected_field" not in serialized
    assert "arguments" not in serialized
    assert "flamingo-2204.14198v1" not in serialized
    assert returned.tool_calls[0].arguments == response.tool_calls[0].arguments
    assert all(event["node_id"] == "worker:blip-2-2301.12597v1" for event in events)
    assert events[-1]["details"]["requested_action_count"] == 1
    assert events[-1]["details"]["requested_actions"] == [{"tool_name": "search_text"}]
    unknown = _ObservedClient(
        ScriptedLLMClient([action("unvalidated-private-tool-name", {})]),
        "worker",
        _RequestBudget(1),
        lambda **event: events.append(event),
    )
    unknown.complete(messages=[], tools=_schema(["blip-2-2301.12597v1"]))
    assert "unvalidated-private-tool-name" not in json.dumps(events)
    assert events[-1]["details"]["requested_actions"] == [{"tool_name": "unknown_tool"}]


def test_concurrent_worker_identity_comes_from_program_schema_not_question():
    events = []
    budget = _RequestBudget(3)
    paper_ids = request().paper_ids

    def call(identity):
        client = _ObservedClient(
            ScriptedLLMClient([action("search_text", {})]),
            "worker",
            budget,
            lambda **event: events.append(event),
        )
        return client.complete(
            messages=[{"role": "user", "content": "Ask about a different paper"}],
            tools=_schema([identity]),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(call, paper_ids))
    assert {event["node_id"] for event in events} == {
        "worker:" + identity for identity in paper_ids
    }
    assert all(
        event["details"]["paper_id"] == event["node_id"].split(":", 1)[1]
        for event in events
    )
    ambiguous = _ObservedClient(
        ScriptedLLMClient([action("search_text", {})]),
        "worker",
        budget,
        lambda **event: events.append(event),
    )
    ambiguous.complete(
        messages=[{"role": "user", "content": "CLIP"}], tools=_schema(paper_ids)
    )
    assert events[-1]["node_id"] == "aggregate:worker"
    assert "paper_id" not in events[-1]["details"]


def test_overall_budget_distinguishes_llm_and_vlm_attempts():
    budget = _RequestBudget(2)
    budget.reserve()

    class Vision:
        def analyze(self, image_path, prompt):
            return "fake-vision-response"

    observed = _ObservedVision(Vision(), budget, lambda **_: None)
    assert (
        observed.analyze("/private/fake.png", "fake context") == "fake-vision-response"
    )
    assert budget.used == 2
    assert budget.llm_used == budget.vlm_used == 1
    with pytest.raises(DependencyCallError):
        observed.analyze("/private/fake.png", "fake context")
    assert budget.used == 2
