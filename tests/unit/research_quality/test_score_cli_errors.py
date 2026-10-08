"""评分CLI必须明确区分缺失文件与还没有实际预测。"""

import json
import sys

import pytest

from scripts import score_quality_benchmark as cli
from scripts.finalize_quality_references import OUTPUT_DATASET


def test_missing_files_report_both_names_before_loading_corpus(tmp_path, monkeypatch):
    prediction = tmp_path / "predictions.jsonl"
    assessment = tmp_path / "human-assessments.jsonl"
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score",
            "--predictions",
            str(prediction),
            "--assessments",
            str(assessment),
            "--output",
            str(output),
        ],
    )

    def unexpected_load(*args):
        raise AssertionError(
            "Missing input files must be checked before corpus loading"
        )

    monkeypatch.setattr(cli, "load_dataset", unexpected_load)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    report = json.loads(output.read_text())
    assert report["issues"] == ["missing_predictions_file", "missing_assessments_file"]
    assert report["input_stage"] == "input_files"
    assert [r["argument"] for r in report["input_errors"]] == [
        "--predictions",
        "--assessments",
    ]
    assert report["real_provider_calls"] == 0
    assert "Traceback" not in output.read_text()


def test_existing_prediction_does_not_hide_missing_assessment(tmp_path):
    prediction = tmp_path / "predictions.jsonl"
    prediction.write_text("")
    with pytest.raises(cli.MissingInputFiles) as error:
        cli.check_input_files(prediction, tmp_path / "missing.jsonl")
    assert error.value.issues == ["missing_assessments_file"]


def test_missing_dataset_reports_stage_and_manifest_name(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["score", "--dataset", str(tmp_path / "missing"), "--output", str(output)],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    report = json.loads(output.read_text())
    assert report["issues"] == ["file_not_found"]
    assert report["input_stage"] == "dataset_load"
    assert report["file_name"] == "manifest.json"


def test_entry_check_without_result_files_succeeds(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "score",
            "--dataset",
            str(OUTPUT_DATASET),
            "--reference-kind",
            "ai_reviewed",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 0
    report = json.loads(output.read_text())
    assert report["status"] == "awaiting_predictions"
    assert report["metrics"]["overall"]["eligible_reference_cases"] == 72
    assert report["real_provider_calls"] == 0
