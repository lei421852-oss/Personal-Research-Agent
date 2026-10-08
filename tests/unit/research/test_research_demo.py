"""演示封装的非生产契约：只读索引、默认链路、失败留存和展示一致性。"""

import argparse
import asyncio
import json
from types import SimpleNamespace

import pytest
from test_synthesis_runtime import run_report

from app.evidence.models import RetrievedEvidence, TextEvidence
from scripts import run_research_demo as demo
from scripts.smoke_research_synthesis import render_markdown


def test_cli_help_without_preflight_or_provider(monkeypatch, capsys):
    monkeypatch.setattr(demo, "preflight", lambda args: pytest.fail("No preflight"))
    with pytest.raises(SystemExit) as exc:
        demo.main(["--help"])
    assert exc.value.code == 0
    assert "--output-dir" in capsys.readouterr().out


def test_existing_execution_is_not_overwritten(tmp_path, monkeypatch):
    (tmp_path / "execution.json").write_text("original")
    monkeypatch.setattr(demo, "preflight", lambda args: pytest.fail("No preflight"))
    with pytest.raises(SystemExit) as exc:
        demo.main(["--output-dir", str(tmp_path), "--execute"])
    assert exc.value.code == 2
    assert (tmp_path / "execution.json").read_text() == "original"


def test_blocked_preflight_is_saved_without_execution(tmp_path, monkeypatch):
    def blocked(args):
        raise ValueError("existing_index_unavailable")

    monkeypatch.setattr(demo, "preflight", blocked)
    assert demo.main(["--output-dir", str(tmp_path), "--execute"]) == 2
    assert json.loads((tmp_path / "preflight.json").read_text())["provider_calls"] == 0
    assert not (tmp_path / "execution-started.json").exists()


def test_recording_retriever_preserves_query_scope_and_result_identity():
    hit = RetrievedEvidence(
        evidence=TextEvidence(evidence_id="ev-a", paper_id="a", page=1, text="Source"),
        retrieval_score=0.75,
    )

    class Retriever:
        def search_text(self, *args):
            assert args == ("original question", ["a"], 7)
            return [hit]

    recording = demo.RecordingRetriever(Retriever())
    assert recording.search_text("original question", ["a"], 7)[0] is hit
    assert recording.records[0]["results"][0]["retrieval_score"] == 0.75


def test_retrieval_error_is_retained_without_exception_text():
    class Retriever:
        def search_text(self, *args):
            raise RuntimeError("sensitive-provider-message")

    recording = demo.RecordingRetriever(Retriever())
    with pytest.raises(RuntimeError):
        recording.search_text("q")
    assert recording.records[0]["error_type"] == "RuntimeError"
    assert "sensitive-provider-message" not in json.dumps(recording.records)


def test_execute_delegates_to_original_default_chain(monkeypatch):
    seen = []

    async def original(case, corpus, snapshot, config, *, scripted, retriever):
        seen.append((case, scripted, retriever))
        return {"status": "completed"}

    monkeypatch.setattr(demo, "run_case", original)
    result = asyncio.run(demo.execute_case("case", None, {}, {}, "retriever", 1))
    assert result == {"status": "completed"}
    assert seen == [("case", False, "retriever")]


def test_timeout_keeps_default_chain_cancellation_snapshot(monkeypatch):
    async def waiting(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            exc.evaluation_snapshot = {"known_initial_tool_calls": 2}
            raise

    monkeypatch.setattr(demo, "run_case", waiting)
    result = asyncio.run(demo.execute_case(None, None, {}, {}, None, 0.01))
    assert result["status"] == "timed_out"
    assert result["known_initial_tool_calls"] == 2


def test_report_rendering_keeps_original_claims_and_evidence(tmp_path):
    report, source, _ = run_report()
    evidence = [e for c in source.verified_claims for e in c.evidence]
    corpus = SimpleNamespace(text_evidence=evidence, figure_evidence=[])
    execution = {
        "question": source.plan.question,
        "research": {
            "outcome": {
                "report": report.model_dump(),
                "synthesis_input": source.model_dump(),
            }
        },
    }
    result = demo.write_display(tmp_path, execution, [], corpus)
    assert result["citation_check"] == "passed"
    assert (tmp_path / "research-report.md").read_text() == render_markdown(report)
    assert all(
        c.text in (tmp_path / "evidence-map.md").read_text()
        for c in report.verified_claims
    )


def test_tampered_report_does_not_enter_display(tmp_path):
    report, source, _ = run_report()
    data = report.model_dump()
    data["verified_claims"][0]["text"] = "Invented rewritten claim"
    execution = {
        "question": source.plan.question,
        "research": {
            "outcome": {"report": data, "synthesis_input": source.model_dump()}
        },
    }
    with pytest.raises(ValueError, match="rewrote"):
        demo.write_display(tmp_path, execution, [], None)
    assert not (tmp_path / "research-report.md").exists()


def test_failure_display_does_not_invent_report(tmp_path):
    result = demo.write_display(
        tmp_path, {"question": "q", "failure": {"issue_type": "TimeoutError"}}, [], None
    )
    assert result["report_status"] == "not_generated"
    assert not (tmp_path / "research-report.json").exists()
    assert "未返回" in (tmp_path / "research-report.md").read_text()


def test_preflight_opens_existing_index_without_create(tmp_path, monkeypatch):
    import app.retrieval.embedding
    import app.retrieval.vector_store

    for kind in ("LLM", "VLM"):
        for name in ("API_KEY", "MODEL", "BASE_URL"):
            monkeypatch.setenv(f"{kind}_{name}", "fake-local-test")
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    baseline = tmp_path / "docs/acceptance/stage_g1/baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text('{"f3_protected_artifacts": []}')
    (tmp_path / "index").mkdir()
    (tmp_path / "index/chroma.sqlite3").touch()
    config = {
        "runtime": {},
        "papers_dir": "papers",
        "additional_papers_dir": "extra",
        "corpus_snapshot_id": "snapshot",
        "index_dir": "index",
        "collection": "existing",
        "embedding_model": "fake",
        "device": "cpu",
        "distance": "cosine",
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    evidence = [
        TextEvidence(evidence_id="ev-" + p, paper_id=p, page=1, text="source")
        for p in ["a", "b"]
    ]
    corpus = SimpleNamespace(text_evidence=evidence, paper_aliases={})
    monkeypatch.setattr(demo, "verify", lambda path: {"status": "PASS"})
    monkeypatch.setattr(demo, "load_paper_corpus", lambda *args, **kwargs: corpus)
    monkeypatch.setattr(
        demo, "corpus_snapshot", lambda *args: {"snapshot_id": "snapshot"}
    )
    monkeypatch.setattr(
        demo,
        "load_paper_catalog",
        lambda: SimpleNamespace(documents={"a": None, "b": None}),
    )
    monkeypatch.setattr(demo, "index_records", lambda store: evidence)
    monkeypatch.setattr(demo, "index_fingerprint", lambda store: "fingerprint")

    class Store:
        def __init__(self, path, collection, *, create):
            assert create is False and collection == "existing"
            self.collection = SimpleNamespace(
                count=lambda: 2,
                metadata={
                    "corpus_snapshot_id": "snapshot",
                    "embedding_model": "fake",
                    "distance": "cosine",
                    "schema_version": 1,
                },
            )

    class Embedding:
        def __init__(self, *args, **kwargs):
            pass

        def embed_query(self, query):
            return [1.0, 0.0]

    monkeypatch.setattr(app.retrieval.vector_store, "ChromaTextVectorStore", Store)
    monkeypatch.setattr(app.retrieval.embedding, "QwenEmbeddingModel", Embedding)
    args = argparse.Namespace(
        question="q",
        timeout=1,
        config=config_path,
        env_file=tmp_path / "missing.env",
        paper_ids=["a", "b"],
    )
    result = demo.preflight(args)[0]
    assert result["provider_calls"] == 0 and result["index_rebuilt"] is False
