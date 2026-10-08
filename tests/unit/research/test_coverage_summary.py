"""评分投影和来源绑定不允许把独立复核变成对历史标签的改写。"""

import hashlib
import json

import pytest

from scripts.summarize_worker_coverage_comparison import (
    reviewed_record_hash,
    semantic_projection,
    summarize,
)


def test_semantic_projection_does_not_infer_success_from_complete_properties():
    record = {
        "semantic_correct": False,
        "scores": {},
        "comparison_rubric": {},
        "per_paper_complete": {"a": True, "b": True},
        "unsupported_additional_assertion_count": 1,
        "support_issue_count": 1,
        "premature_supported_requirement_count": 0,
        "valid_answer_delivered": True,
        "abstention": {"reasonableness": None},
    }
    projected = semantic_projection(record)
    assert projected["semantic_complete_requirements"] == 2
    assert projected["semantic_correct"] is False
    assert projected["effective_answer"] is True
    assert projected["abstention_reasonable"] is None


def test_review_hash_uses_explicit_serialization_and_binds_changed_answer():
    record = {"case_id": "test", "answer": "中文", "cost": 1.5}
    expected = hashlib.sha256(
        json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert reviewed_record_hash(record) == expected
    assert reviewed_record_hash(record | {"answer": "changed"}) != expected


def test_wrong_source_fingerprint_stops_before_score_generation(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "treatment_executions.jsonl").write_text('{"case_id":"test"}\n')
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps({"source_hashes": {"baseline_executions": {"sha256": "wrong"}}})
    )
    with pytest.raises(ValueError, match="review_source_file_hash_mismatch"):
        summarize(output, review)
    assert not (output / "semantic_assessments.jsonl").exists()
    assert not (output / "comparison_summary.json").exists()
