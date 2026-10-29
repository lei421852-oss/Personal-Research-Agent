"""原ReAct上的Scripted记忆测试；不调用真实Provider或数据库。"""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from app.memory.models import MemorySave, MemoryScope
from app.memory.service import MemoryService
from app.research.react_runtime import ReActWorkbenchRuntime
from app.research.run_manager import MainAgentRunRequest
from tests.test_react_workbench import (
    CATALOG,
    Dependencies,
    PaperSearch,
    factory,
    finish,
    research_args,
    research_finish,
)
from tests.unit.memory.fakes import FakeMemoryRepository, MemoryStore

NOW = datetime(2026, 10, 29, tzinfo=UTC)
CONTENT = "Prefer formulas."
SAVE = {
    "memory_type": "preference",
    "memory_key": "explanation.style",
    "content": CONTENT,
    "tags": ["explanation"],
}


def svc(store=None):
    return MemoryService(
        FakeMemoryRepository(store),
        MemoryScope(scope_id="demo_project"),
        clock=lambda: NOW,
    )


def execute(
    steps,
    *,
    service=None,
    question="Remember this preference: Prefer formulas.",
    max_tools=64,
):
    deps = Dependencies()
    deps.environment = dict
    llm, vision, _, calls, events = factory(deps, steps)
    schemas = []

    class Client(llm):
        def complete(self, *, messages, tools):
            if any(t["name"] == "run_multi_agent_research" for t in tools):
                schemas.append(tools)
            return super().complete(messages=messages, tools=tools)

    runtime = ReActWorkbenchRuntime(
        deps,
        llm_factory=Client,
        vision_factory=vision,
        paper_retriever=PaperSearch(),
        paper_catalog=CATALOG,
        memory_service=service,
        max_tools=max_tools,
    )
    result = asyncio.run(
        runtime.run(
            MainAgentRunRequest(question=question, max_model_calls=200),
            lambda **event: events.append(event),
        )
    )
    return result, calls, events, schemas


def memory_finish(messages):
    data = json.loads(messages[-1]["content"])["payload"]
    return finish(
        "memory",
        memory_ids=[m["memory_id"] for m in data.get("memories", [])],
        answer="UNTRUSTED invented memory answer must not become authoritative",
    )


def test_memory_default_disabled_and_available_tools_not_forced():
    ordinary, _, _, schemas = execute([finish()])
    assert ordinary["status"] == "completed"
    assert not any(t["name"].startswith("memory_") for t in schemas[0])
    service = svc()
    enabled, _, events, schemas = execute([finish()], service=service)
    assert enabled["status"] == "completed" and service.repository.store.calls == []
    assert {"memory_search", "memory_save", "memory_update", "memory_forget"}.issubset(
        {t["name"] for t in schemas[0]}
    )
    assert not any(e["node_type"] == "Planner" for e in events)


def test_save_feedback_and_memory_only_finish_preserve_source_not_model_guess():
    service = svc()
    result, calls, _, _ = execute(
        [("memory_save", SAVE), memory_finish], service=service
    )
    assert result["status"] == "completed", result
    assert (
        json.loads(calls[1][-1]["content"])["payload"]["memories"][0]["content"]
        == CONTENT
    )
    answer = result["answer_first"]
    assert (
        CONTENT in answer["direct_answer"]
        and "UNTRUSTED invented" not in answer["direct_answer"]
    )
    assert answer["citations"] == [] and answer["evidence_level"] == "memory"
    assert answer["source_references"][0]["source_kind"] == "project_memory"
    assert answer["source_references"][0]["source_ref"].startswith("question_sha256:")
    assert answer["metrics"]["tool_calls"] == 1


def test_next_request_with_new_repository_recalls_without_writing():
    store = MemoryStore()
    result, *_ = execute([("memory_save", SAVE), memory_finish], service=svc(store))
    assert result["status"] == "completed"
    before = len(store.records)
    later, _, _, _ = execute(
        [("memory_search", {"keywords": ["formulas"]}), memory_finish],
        service=svc(store),
        question="What was my saved explanation preference?",
    )
    assert later["status"] == "completed", later
    assert CONTENT in later["answer_first"]["direct_answer"]
    assert len(store.records) == before
    assert store.calls == ["create", "search"]


def test_no_match_is_normal_memory_business_result():
    result, _, _, _ = execute(
        [("memory_search", {"keywords": ["missing"]}), memory_finish], service=svc()
    )
    assert result["status"] == "completed", result
    assert result["answer_first"]["direct_answer"] == "当前项目中没有匹配的有效记忆。"
    assert result["main_agent"]["trace"][0]["status"] == "empty"
    assert result["answer_first"]["citations"] == []


def test_memory_identifier_cannot_be_used_as_paper_evidence():
    def fake_evidence(messages):
        identity = json.loads(messages[-1]["content"])["payload"]["memories"][0][
            "memory_id"
        ]
        return finish("evidence", evidence_ids=[identity])

    result, *_ = execute([("memory_save", SAVE), fake_evidence], service=svc())
    assert result["status"] != "completed"
    assert result["answer_first"]["citations"] == []


def test_write_failure_does_not_turn_into_successful_persistence():
    service = svc()
    service.repository.store.failure = "memory_unavailable"
    result, *_ = execute(
        [
            ("memory_save", SAVE),
            finish("insufficient", limitations=["Memory database failed."]),
        ],
        service=service,
    )
    assert result["status"] == "failed"
    assert result["issue_type"] == "memory_write_failed"
    assert service.repository.store.records == {}
    assert result["memory"]["unresolved_write_errors"]


def test_unknown_commit_allows_read_but_never_second_write_or_fake_success():
    service = svc()
    service.repository.store.failure = "memory_commit_unknown"

    def read_after_error(messages):
        service.repository.store.failure = None
        return ("memory_search", {"keywords": ["formulas"]})

    result, *_ = execute(
        [("memory_save", SAVE), ("memory_save", SAVE), read_after_error, memory_finish],
        service=service,
    )
    assert result["status"] == "failed", result
    assert service.repository.store.calls.count("create") == 1
    assert len(service.repository.store.records) == 1
    assert result["memory"]["unknown_write_keys"] == ["explanation.style"]


def test_conflict_can_recover_with_observed_version_and_explicit_update():
    service = svc()
    asyncio.run(service.save(MemorySave(**SAVE), question=CONTENT))
    newer = "Prefer code examples."

    def update(messages):
        row = json.loads(messages[-1]["content"])["payload"]["memories"][0]
        return (
            "memory_update",
            SAVE
            | {
                "content": newer,
                "memory_id": row["memory_id"],
                "expected_version": row["version"],
            },
        )

    result, *_ = execute(
        [
            ("memory_save", SAVE | {"content": newer}),
            ("memory_search", {"keywords": ["formulas"]}),
            update,
            memory_finish,
        ],
        service=service,
        question="Replace the old preference: Prefer code examples.",
    )
    assert result["status"] == "completed", result
    assert newer in result["answer_first"]["direct_answer"]
    assert result["memory"]["unresolved_write_errors"] == {}
    assert [t["status"] for t in result["main_agent"]["trace"]] == [
        "error",
        "success",
        "success",
    ]
    assert result["main_agent"]["decision_steps"] == 4


def test_forget_is_soft_delete_and_finishes_as_lifecycle_ack():
    service = svc()
    asyncio.run(service.save(MemorySave(**SAVE), question=CONTENT))

    def forget(messages):
        row = json.loads(messages[-1]["content"])["payload"]["memories"][0]
        return (
            "memory_forget",
            {
                "memory_id": row["memory_id"],
                "expected_version": row["version"],
                "reason": "Forget the formula preference.",
            },
        )

    result, *_ = execute(
        [("memory_search", {"keywords": ["formulas"]}), forget, memory_finish],
        service=service,
        question="Forget the formula preference.",
    )
    assert result["status"] == "completed", result
    assert "失效" in result["answer_first"]["direct_answer"]
    assert len(service.repository.store.records) == 1
    assert next(iter(service.repository.store.records.values())).status == "deleted"


def test_original_optional_multi_agent_path_is_unchanged():
    service = svc()
    result, _, events, _ = execute(
        [("run_multi_agent_research", research_args()), research_finish],
        service=service,
        question="Compare BLIP-2 and Flamingo design evidence.",
    )
    assert result["status"] == "completed", result
    assert any(e["node_type"] == "Planner" for e in events)
    assert service.repository.store.calls == []


def test_shared_tool_budget_can_block_memory_write():
    service = svc()
    result, *_ = execute(
        [
            ("memory_search", {"keywords": ["formulas"]}),
            ("memory_save", SAVE),
            finish("insufficient", limitations=["Budget exhausted."]),
        ],
        service=service,
        max_tools=1,
    )
    assert service.repository.store.records == {}
    assert service.repository.store.calls == ["search"]
    assert result["answer_first"]["metrics"]["tool_calls"] == 1


def test_cancellation_propagates_through_existing_bridge():
    async def scenario():
        service = svc()
        entered, released = asyncio.Event(), asyncio.Event()

        async def waiting_search(_):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                released.set()

        service.search = waiting_search
        deps = Dependencies()
        deps.environment = dict
        llm, vision, _, _, _ = factory(
            deps, [("memory_search", {"keywords": ["formulas"]})]
        )
        runtime = ReActWorkbenchRuntime(
            deps,
            llm_factory=llm,
            vision_factory=vision,
            paper_retriever=PaperSearch(),
            paper_catalog=CATALOG,
            memory_service=service,
        )
        task = asyncio.create_task(
            runtime.run(
                MainAgentRunRequest(question="Recall saved preference."),
                lambda **_: None,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as error:
            await asyncio.wait_for(task, timeout=2)
        assert released.is_set()
        assert error.value.evaluation_snapshot["status"] == "cancelled"

    asyncio.run(scenario())


def test_rejected_sensitive_content_is_not_repeated_in_tool_trace_arguments():
    content = "My phone is 13800138000"
    service = svc()
    result, _, events, _ = execute(
        [
            ("memory_save", SAVE | {"content": content}),
            finish("insufficient", limitations=["Sensitive content rejected."]),
        ],
        service=service,
        question=content,
    )
    assert result["status"] == "failed"
    assert service.repository.store.records == {}
    assert "13800138000" not in json.dumps(result["main_agent"]["trace"])
    tool_events = [
        e
        for e in events
        if e.get("details", {}).get("tool_name", "").startswith("memory_")
    ]
    assert "13800138000" not in json.dumps(tool_events)
