"""七题实验入口的有界执行、失败分类和冻结预检；全部免 Provider。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts import run_worker_coverage_comparison as cli


def test_watchdog_returns_success_without_changing_the_result():
    expected = {"status": "completed", "case_id": "synthetic"}

    async def operation():
        return expected

    result = asyncio.run(cli.run_with_watchdog(operation(), 1))
    assert result is expected
    assert "case_watchdog_triggered" not in result


def test_watchdog_timeout_keeps_real_cancel_snapshot_and_never_calls_it_abstention():
    snapshot = {
        "status": "cancelled",
        "initial_worker_results": [{"status": "cancelled"}],
        "llm_provider_attempts_observed": 2,
        "tool_observations": [{"tool_name": "search_text"}],
    }

    async def operation():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            exc.evaluation_snapshot = snapshot
            raise

    result = asyncio.run(cli.run_with_watchdog(operation(), 0.001))
    assert result == snapshot | {"case_watchdog_triggered": True}
    assert result["status"] != "insufficient_evidence"
    assert "case_watchdog_triggered" not in snapshot


def test_watchdog_refuses_timeout_without_execution_snapshot():
    async def operation():
        await asyncio.sleep(10)

    with pytest.raises(RuntimeError, match="watchdog_failure_snapshot_unavailable"):
        asyncio.run(cli.run_with_watchdog(operation(), 0.001))


def test_watchdog_does_not_replace_an_explicit_external_cancellation():
    async def scenario():
        async def operation():
            await asyncio.sleep(10)

        task = asyncio.create_task(cli.run_with_watchdog(operation(), 2))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_jsonl_load_rejects_duplicates_but_accepts_blank_lines(tmp_path):
    source = tmp_path / "records.jsonl"
    source.write_text('{"case_id":"a"}\n\n{"case_id":"b"}\n')
    assert [row["case_id"] for row in cli.load_rows(source)] == ["a", "b"]
    source.write_text('{"case_id":"a"}\n{"case_id":"a"}\n')
    with pytest.raises(ValueError, match="duplicate_case_id"):
        cli.load_rows(source)


def provider_error(kind="APIConnectionError"):
    return {"status": "error", "error_type": kind, "role": "worker"}


def failed_worker(code="dependency_failure"):
    return {
        "paper_id": "paper-local",
        "status": "failed",
        "error": {"code": code},
        "requirements": [{"requirement_id": "req-local", "status": "execution_failed"}],
    }


def successful_worker():
    return {
        "paper_id": "paper-local",
        "status": "completed",
        "error": None,
        "requirements": [{"requirement_id": "req-local", "status": "supported"}],
    }


def unavailable_review():
    return {
        "paper_id": "paper-local",
        "requirement_id": "req-local",
        "status": "not_reviewable",
        "issue": {"code": "dependency_failure"},
    }


def passed_review():
    return {
        "paper_id": "paper-local",
        "requirement_id": "req-local",
        "status": "passed",
        "issue": None,
    }


def test_infrastructure_is_true_for_final_failed_worker_with_connection_error():
    detail = {
        "provider_attempts": [provider_error()],
        "initial_worker_results": [failed_worker()],
        "initial_review_results": [],
        "outcome": {
            "merged": {"worker_results": [failed_worker()], "review_results": []}
        },
    }
    assert cli.classify_infrastructure(detail) is True


def test_infrastructure_detects_final_reviewer_unavailable():
    detail = {
        "provider_attempts": [provider_error()],
        "initial_worker_results": [successful_worker()],
        "initial_review_results": [unavailable_review()],
        "outcome": {
            "merged": {
                "worker_results": [successful_worker()],
                "review_results": [unavailable_review()],
            }
        },
    }
    assert cli.classify_infrastructure(detail) is True


def test_recovered_provider_error_is_not_final_infrastructure_failure():
    detail = {
        "provider_attempts": [provider_error(), {"status": "success"}],
        "initial_worker_results": [successful_worker()],
        "initial_review_results": [unavailable_review()],
        "outcome": {
            "merged": {
                "worker_results": [successful_worker()],
                "review_results": [passed_review()],
            }
        },
    }
    assert cli.classify_infrastructure(detail) is False


@pytest.mark.parametrize(
    "code,kind",
    [
        ("protocol_correction_exhausted", "APIConnectionError"),
        ("dependency_failure", "ValueError"),
    ],
)
def test_protocol_or_permanent_action_error_is_not_connection_failure(code, kind):
    detail = {
        "provider_attempts": [provider_error(kind)],
        "initial_worker_results": [failed_worker(code)],
        "initial_review_results": [],
        "outcome": {
            "merged": {"worker_results": [failed_worker(code)], "review_results": []}
        },
    }
    assert cli.classify_infrastructure(detail) is False


def prepare_preflight_files(tmp_path, monkeypatch):
    """只构建能抵达身份检查的输入；一旦触及 Provider/Index 就让测试失败。"""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "verify", lambda path: {"status": "PASS"})
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: None)
    original = b"unchanged-f31-source\n"
    monkeypatch.setattr(
        cli.subprocess, "check_output", lambda *args, **kwargs: original
    )
    for name in cli.SOURCES[:3]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)
    source = tmp_path / "comparison" / "requests.jsonl"
    source.parent.mkdir()
    source.write_text(
        "".join(
            json.dumps(
                {
                    "case_id": f"synthetic-{index}",
                    "question": "Compare named paper architectures.",
                    "paper_ids": ["paper-a", "paper-b"],
                    "dimension": "language_model",
                }
            )
            + "\n"
            for index in range(7)
        )
    )
    source.with_name("comparison_manifest.json").write_text(
        json.dumps(
            {
                "requests_sha256": cli.digest(source),
            }
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("preflight must fail before touching credentials, corpus, or index")

    monkeypatch.setattr(cli, "load_dotenv", forbidden)
    monkeypatch.setattr(cli, "load_paper_corpus", forbidden)
    monkeypatch.setattr(cli, "ChromaTextVectorStore", forbidden)
    return SimpleNamespace(requests=source, case_timeout=10, env_file=tmp_path / ".env")


@pytest.mark.parametrize("source_index", [0, 1, 2])
def test_preflight_rejects_changed_f31_source_before_provider_or_index(
    tmp_path, monkeypatch, source_index
):
    args = prepare_preflight_files(tmp_path, monkeypatch)
    (tmp_path / cli.SOURCES[source_index]).write_text("changed production protocol")
    with pytest.raises(ValueError, match="f31_coverage_implementation_changed"):
        cli.preflight(args)


def test_preflight_rejects_altered_seven_request_hash(tmp_path, monkeypatch):
    args = prepare_preflight_files(tmp_path, monkeypatch)
    rows = cli.load_rows(args.requests)
    rows[0]["question"] = "A changed evaluation question."
    args.requests.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="seven_request_identity_mismatch"):
        cli.preflight(args)


def test_preflight_rejects_frozen_baseline_failure_before_git_or_requests(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "verify", lambda path: {"status": "FAIL"})
    with pytest.raises(ValueError, match="frozen_or_f2_artifact_changed"):
        cli.preflight(SimpleNamespace(case_timeout=10))


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_preflight_rejects_nonpositive_or_nonfinite_watchdog(
    tmp_path, monkeypatch, timeout
):
    args = prepare_preflight_files(tmp_path, monkeypatch)
    args.case_timeout = timeout
    with pytest.raises(ValueError, match="case_timeout_must_be_positive_finite"):
        cli.preflight(args)
