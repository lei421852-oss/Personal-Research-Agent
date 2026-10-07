import json
import sys

import pytest

from app.research.models import ResearchDimension, ResearchPlan
from app.research.requirements import expand_requirements, group_worker_tasks
from scripts import smoke_async_research as smoke


def plan():
    return ResearchPlan(
        question="How does this model connect visual information to language?",
        paper_ids=["flamingo-2204.14198v1"],
        dimensions=[
            ResearchDimension(
                dimension_id="connection",
                name="Connection",
                description="Investigate the connection.",
                research_question="How does this paper connect visual representations to language?",
            )
        ],
    )


@pytest.mark.parametrize("format_name", ["plan", "preparation", "smoke"])
def test_load_existing_plan_formats_without_regenerating_dimensions(
    tmp_path, format_name
):
    expected = plan()
    data = expected.model_dump(mode="json")
    if format_name == "preparation":
        data = {"plan": data, "trace": {"status": "success"}}
    if format_name == "smoke":
        data = {
            "cases": [{"case_id": "selected", **data}, {"case_id": "other", **data}]
        }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data))
    loaded, tasks = smoke.load_worker_tasks(
        path, "selected" if format_name == "smoke" else None
    )
    assert loaded == expected
    assert tasks == group_worker_tasks(expand_requirements(expected))
    assert all(r.status == "pending" for t in tasks for r in t.requirements)


def test_multiple_plans_require_explicit_selection(tmp_path):
    path = tmp_path / "plans.json"
    data = plan().model_dump(mode="json")
    path.write_text(
        json.dumps({"cases": [{"case_id": "one", **data}, {"case_id": "two", **data}]})
    )
    with pytest.raises(ValueError, match="Select"):
        smoke.load_worker_tasks(path)
    with pytest.raises(ValueError, match="does not exist"):
        smoke.load_worker_tasks(path, "absent")


def test_validation_mode_cannot_invoke_models_or_workers(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    output = tmp_path / "validated.json"
    path.write_text(plan().model_dump_json())

    async def forbidden(*args):
        pytest.fail("Validation-only must not initialize or execute real research")

    monkeypatch.setattr(smoke, "run_real_smoke", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--plan",
            str(path),
            "--validate-only",
            "--env-file",
            str(tmp_path / "missing.env"),
            "--output",
            str(output),
            "--max-workers",
            "2",
        ],
    )
    smoke.main()
    saved = json.loads(output.read_text())
    assert saved["mode"] == "validated_not_executed"
    assert saved["real_smoke_executed"] is False
    assert saved["configuration"]["max_concurrent_workers"] == 2
    assert "orchestrator_trace" not in saved


def test_unknown_paper_plan_rejected_before_model_initialization(tmp_path, monkeypatch):
    path = tmp_path / "unknown.json"
    path.write_text(
        plan().model_copy(update={"paper_ids": ["unknown-paper"]}).model_dump_json()
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--plan",
            str(path),
            "--validate-only",
            "--output",
            str(tmp_path / "output.json"),
        ],
    )
    with pytest.raises(SystemExit) as failure:
        smoke.main()
    assert failure.value.code == 2
