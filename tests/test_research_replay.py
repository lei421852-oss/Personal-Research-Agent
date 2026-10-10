"""历史演示回放使用固定来源，不触发模型、检索或原文件写入。"""

import hashlib
import json
from pathlib import Path

import pytest

from app.research.run_replay import ReplayError, ReplayRepository

ROOT = Path(__file__).resolve().parents[1]


def test_replay_catalog_contains_only_existing_two_cases():
    cases = ReplayRepository(ROOT).list_cases()
    assert [item["case_id"] for item in cases] == ["g2", "g2-1"]
    assert all(item["title"] and item["description"] for item in cases)
    assert all("directory" not in item for item in cases)


@pytest.mark.parametrize(
    "case_id,directory,expected_claims",
    [
        ("g2", "docs/demos/blip2-vs-flamingo", 5),
        ("g2-1", "docs/acceptance/stage_g2_1/results", 4),
    ],
)
def test_replay_preserves_original_report_and_files(
    case_id, directory, expected_claims
):
    report_path = ROOT / directory / "research-report.json"
    digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    expected = json.loads(report_path.read_text())
    execution = ReplayRepository(ROOT).load(case_id)
    assert execution["outcome"]["report"] == expected
    assert len(execution["outcome"]["report"]["verified_claims"]) == expected_claims
    assert execution["initial_worker_results"]
    assert execution["source"] == "replay"
    assert len(execution["replay_provenance"]["source_files"]) == 3
    assert all(
        not Path(item["path"]).is_absolute() and len(item["sha256"]) == 64
        for item in execution["replay_provenance"]["source_files"]
    )
    assert hashlib.sha256(report_path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "case_id", ["unknown", "../../.env", "/etc/passwd", "g2/../g2-1"]
)
def test_unknown_or_path_shaped_replay_case_is_rejected(case_id):
    with pytest.raises(ReplayError, match="^unknown_replay_case$"):
        ReplayRepository(ROOT).load(case_id)


def test_missing_replay_artifact_has_fixed_error_without_private_path(tmp_path):
    with pytest.raises(ReplayError) as caught:
        ReplayRepository(tmp_path).load("g2")
    assert str(caught.value) == "replay_artifact_unavailable"
    assert str(tmp_path) not in str(caught.value)


def test_replay_source_symlink_cannot_escape_repository(tmp_path):
    directory = tmp_path / "docs/demos/blip2-vs-flamingo"
    directory.mkdir(parents=True)
    (directory / "execution-record.json").symlink_to(
        ROOT / "docs/demos/blip2-vs-flamingo/execution-record.json"
    )
    with pytest.raises(ReplayError, match="^invalid_replay_source$"):
        ReplayRepository(tmp_path).load("g2")
