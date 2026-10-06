"""Transport/projection checks against the unchanged bounded Agent and fake providers."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.runtime import AgentAPIRuntime
from app.evidence.corpus import load_paper_corpus
from app.llm.fake import ScriptedLLMClient
from app.main import create_app
from scripts.demo_day5 import (
    ControlledRetrieval,
    ControlledVLM,
    action,
    last_observation,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def corpus():
    return load_paper_corpus(ROOT / "data/papers")


def fake_runtime(corpus, mode="text"):
    def factory(stack):
        def finish_text(messages, tools):
            observed = last_observation(messages)["payload"]["evidence"][0]
            return action(
                "finish",
                "finish",
                answer="Contrastive image-text alignment.",
                evidence_ids=[observed["evidence_id"]],
                limitations=[],
            )

        def analyze(messages, tools):
            observed = last_observation(messages)["payload"]["figure"]
            return action(
                "analyze",
                "analyze_figure",
                question="Verify premise",
                figure_evidence_id=observed["evidence_id"],
                context_evidence_ids=[],
            )

        def finish_figure(messages, tools):
            observed = last_observation(messages)["payload"]["analysis"]
            return action(
                "finish",
                "finish",
                answer=observed["interpretation"],
                evidence_ids=observed["evidence_used"],
                limitations=observed["limitations"],
            )

        if mode == "figure":
            responses = [
                action("find", "find_figure", paper_id="blip", figure_id="Figure 3"),
                analyze,
                finish_figure,
            ]
        elif mode == "figure_without_analysis":

            def finish_caption(messages, tools):
                figure = last_observation(messages)["payload"]["figure"]
                return action(
                    "finish",
                    "finish",
                    answer="Only figure caption/context was used.",
                    evidence_ids=[figure["evidence_id"]],
                    limitations=["Visual analysis was not executed."],
                )

            responses = [
                action("find", "find_figure", paper_id="blip", figure_id="Figure 3"),
                finish_caption,
            ]
        elif mode == "insufficient":
            responses = [
                action(
                    "finish",
                    "finish",
                    answer="Prepared corpus does not contain enough evidence.",
                    evidence_ids=[],
                    limitations=[
                        "No prepared evidence for the requested private manifest."
                    ],
                )
            ]
        elif mode == "model_failure":
            responses = []
        elif mode == "exhaustion":
            responses = [
                action(str(i), "read_page", paper_id="flamingo", page=p)
                for i, p in enumerate([4, 9, 10, 11, 4, 9, 10])
            ]
        else:
            responses = [
                action(
                    "search",
                    "search_text",
                    query="CLIP objective",
                    paper_ids=["clip"],
                    top_k=1,
                ),
                finish_text,
            ]
        return ScriptedLLMClient(responses), ControlledVLM()

    return AgentAPIRuntime(
        corpus, ControlledRetrieval(corpus), factory, retrieval_mode="test"
    )


def test_text_http_projection_and_finish_trace(corpus):
    with TestClient(create_app(runtime_factory=lambda: fake_runtime(corpus))) as client:
        response = client.post("/api/agent/run", json={"question": "CLIP objective"})
    assert response.status_code == 200
    body = response.json()
    assert (
        body["status"] == "completed"
        and body["answer"] == "Contrastive image-text alignment."
    )
    assert body["citations"][0]["paper_id"] == "clip-2103.00020v1"
    assert body["citations"][0]["page"] > 0
    assert [s["tool_name"] for s in body["trace"]] == ["search_text", "finish"]
    assert body["trace"][0]["evidence_ids"]
    assert body["trace"][0]["latency_ms"] >= 0
    assert body["trace"][1]["latency_ms"] is None  # No invented finish timing.
    assert body["metrics"]["tool_calls"] == 1 and body["metrics"]["model_calls"] == 2
    assert body["metrics"]["vlm_calls"] == 0
    assert set(body) == {
        "status",
        "answer",
        "citations",
        "limitations",
        "trace",
        "metrics",
        "figures",
        "stop_details",
        "retrieval_mode",
    }
    forbidden = [
        "messages",
        "observed_evidence_ids",
        "asset_path",
        "/home/",
        "Traceback",
        "POLICY",
        "reasoning_content",
    ]
    assert not any(s in response.text for s in forbidden)


def test_figure_citation_image_and_analysis(corpus):
    with TestClient(
        create_app(runtime_factory=lambda: fake_runtime(corpus, "figure"))
    ) as client:
        response = client.post("/api/agent/run", json={"question": "Figure QA"})
        body = response.json()
        assert response.status_code == 200
        assert body["citations"][0]["figure_id"] == "Figure 3"
        figure = body["figures"][0]
        assert figure["page"] == 4 and figure["paper_id"] == "blip-2201.12086v2"
        assert figure["caption"] and figure["analysis"]["observations"]
        assert "interpretation" in figure["analysis"]
        assert body["metrics"]["vlm_calls"] == 1
        image = client.get(figure["image_url"])
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        assert image.content.startswith(b"\x89PNG")
        assert "asset_path" not in response.text and "/home/" not in response.text


@pytest.mark.parametrize(
    "mode,status",
    [
        ("insufficient", "insufficient_evidence"),
        ("model_failure", "model_failure"),
        ("exhaustion", "step_exhaustion"),
    ],
)
def test_domain_stops_are_http_200(corpus, mode, status):
    with TestClient(
        create_app(runtime_factory=lambda: fake_runtime(corpus, mode))
    ) as client:
        response = client.post("/api/agent/run", json={"question": "Question"})
    assert response.status_code == 200 and response.json()["status"] == status
    if mode == "insufficient":
        assert response.json()["citations"] == [] and response.json()["limitations"]
    if mode == "exhaustion":
        assert response.json()["metrics"]["tool_calls"] == 6
        assert (
            len(response.json()["trace"]) == 6
        )  # Rejected seventh call never becomes an executed step.


def test_unexpected_exception_stable_error_and_lifespan_close():
    class Broken:
        closed = False

        def run(self, question):
            raise RuntimeError("sk-secret-key /home/private Traceback")

        def close(self):
            self.closed = True

    runtime = Broken()
    with TestClient(create_app(runtime_factory=lambda: runtime)) as client:
        response = client.post("/api/agent/run", json={"question": "Question"})
    assert runtime.closed
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sk-secret" not in response.text and "Traceback" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": True},
        {"question": "x", "path": "/home/private"},
        {"question": "x" * 4001},
    ],
)
def test_invalid_request_stable_422(corpus, body):
    with TestClient(create_app(runtime_factory=lambda: fake_runtime(corpus))) as client:
        response = client.post("/api/agent/run", json=body)
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "invalid_request"
    )
    assert "/home/" not in response.text


def test_unavailable_configuration_returns_503_and_page_still_loads():
    def unavailable():
        raise RuntimeError("secret settings /home/private")

    with TestClient(create_app(runtime_factory=unavailable)) as client:
        response = client.post("/api/agent/run", json={"question": "Question"})
        assert (
            response.status_code == 503
            and response.json()["error"]["code"] == "service_unavailable"
        )
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert "secret" not in response.text


def test_unknown_text_or_arbitrary_path_is_not_image(corpus):
    with TestClient(create_app(runtime_factory=lambda: fake_runtime(corpus))) as client:
        for identity in ["ev_unknown", corpus.text_evidence[0].evidence_id]:
            response = client.get(f"/api/evidence/{identity}/image")
            assert (
                response.status_code == 404
                and response.json()["error"]["code"] == "figure_not_found"
            )
        assert client.get("/api/evidence/../../etc/passwd/image").status_code != 200


def test_repeated_http_requests_have_separate_trace_and_clients(corpus):
    with TestClient(create_app(runtime_factory=lambda: fake_runtime(corpus))) as client:
        for _ in range(2):
            body = client.post(
                "/api/agent/run", json={"question": "CLIP objective"}
            ).json()
            assert body["metrics"]["tool_calls"] == 1 and len(body["trace"]) == 2


def test_figure_without_vlm_is_not_labeled_analyzed(corpus):
    with TestClient(
        create_app(
            runtime_factory=lambda: fake_runtime(corpus, "figure_without_analysis")
        )
    ) as client:
        body = client.post("/api/agent/run", json={"question": "Figure QA"}).json()
    assert body["figures"][0]["analysis"] is None
    assert body["metrics"]["vlm_calls"] == 0
    assert [s["tool_name"] for s in body["trace"]] == ["find_figure", "finish"]


def test_provider_resources_close_even_after_model_failure(corpus):
    closed = []

    def factory(stack):
        llm, vision = ScriptedLLMClient([]), ControlledVLM()
        stack.callback(lambda: closed.append("llm"))
        stack.callback(lambda: closed.append("vision"))
        return llm, vision

    runtime = AgentAPIRuntime(corpus, ControlledRetrieval(corpus), factory)
    with TestClient(create_app(runtime_factory=lambda: runtime)) as client:
        response = client.post("/api/agent/run", json={"question": "Question"})
    assert response.status_code == 200 and response.json()["status"] == "model_failure"
    assert sorted(closed) == ["llm", "vision"]


def test_invalid_runtime_response_is_stable_api_error():
    class InvalidRuntime:
        def run(self, question):
            return {"unexpected": "sk-secret /home/private"}

        def close(self):
            pass

    with TestClient(
        create_app(runtime_factory=InvalidRuntime), raise_server_exceptions=False
    ) as client:
        response = client.post("/api/agent/run", json={"question": "Question"})
    assert (
        response.status_code == 500
        and response.json()["error"]["code"] == "internal_error"
    )
    assert "sk-secret" not in response.text and "/home/" not in response.text
