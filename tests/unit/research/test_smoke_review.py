"""审核入口的程序校验模式不会读凭据、创建客户端或运行 Reviewer。"""

import json
import sys

import pytest
from test_paper_worker import Resolver
from test_review_validation import requirement, worker_result

from scripts import smoke_evidence_review as smoke


@pytest.mark.parametrize(
    "status,reviewable", [("supported", 1), ("insufficient_evidence", 0)]
)
def test_validation_only_checks_results_without_invoking_reviewers(
    tmp_path, monkeypatch, status, reviewable
):
    source = tmp_path / "worker.json"
    output = tmp_path / "validation.json"
    source.write_text(
        json.dumps(
            {
                "worker_results": [
                    worker_result(requirement(status=status)).model_dump(mode="json")
                ]
            }
        )
    )

    async def forbidden(*args):
        pytest.fail("Validation-only must not initialize or run Reviewer providers")

    monkeypatch.setattr(smoke, "run_review", forbidden)
    monkeypatch.setattr(smoke, "load_paper_corpus", lambda _: None)
    monkeypatch.setattr(smoke, "load_paper_catalog", lambda: None)
    monkeypatch.setattr(smoke, "EvidenceResolver", lambda *args, **kwargs: Resolver())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--worker-results",
            str(source),
            "--output",
            str(output),
            "--validate-only",
            "--env-file",
            str(tmp_path / "nonexistent.env"),
            "--max-reviewers",
            "1",
            "--max-model-calls",
            "1",
        ],
    )
    smoke.main()
    saved = json.loads(output.read_text())
    assert saved["mode"] == "validated_not_executed"
    assert saved["real_smoke_executed"] is False
    assert saved["reviewable_count"] == reviewable
    assert saved["configuration"] == {
        "max_concurrent_reviewers": 1,
        "max_model_calls_per_review": 1,
    }
    if not reviewable:
        skip = saved["bypassed_results"][0]
        assert skip["skip_reason"] == "insufficient_evidence"
        assert skip["trace"]["model_calls"] == 0
