"""公开事件安全边界与不可变契约。"""

import json

import pytest
from pydantic import ValidationError

from app.research.run_events import RunEvent, sanitize_public


def event(**changes):
    return RunEvent(
        **{
            "run_id": "run-test",
            "event_id": 1,
            "node_type": "Worker",
            "node_id": "worker:paper-1",
            "status": "completed",
            "event_type": "completed",
            "summary": "已读取实际研究结果。",
            "source": "derived",
            **changes,
        }
    )


def test_event_defaults_to_unknown_timestamp_and_is_frozen():
    item = event()
    assert item.timestamp is None
    with pytest.raises(ValidationError):
        item.summary = "changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"event_id": 0},
        {"event_id": True},
        {"source": "invented"},
        {"status": "success"},
        {"node_type": "LLM"},
        {"reasoning": "private"},
    ],
)
def test_event_rejects_invalid_contract(changes):
    with pytest.raises(ValidationError):
        event(**changes)


def test_nested_private_fields_are_removed_and_secrets_paths_redacted(monkeypatch):
    monkeypatch.setenv("WORKBENCH_API_KEY", "fixture-secret-value-8765")
    item = event(
        summary="failure at /home/lei/private/file.json sk-test123456789",
        details={
            "status": "error",
            "reasoning_content": "hidden-model-thinking",
            "raw_response": {"api_key": "fixture-secret-value-8765"},
            "actions": [
                {
                    "arguments": {"query": "fixture-secret-value-8765"},
                    "chain_of_thought": "hidden-model-thinking",
                }
            ],
            "filename": "D:\\private\\secret.json",
        },
    )
    public = item.model_dump_json()
    assert "hidden-model-thinking" not in public
    assert "reasoning_content" not in public
    assert "raw_response" not in public
    assert "fixture-secret-value-8765" not in public
    assert "/home/lei/private" not in public
    assert "D:\\\\private" not in public
    assert item.details["status"] == "error"
    assert "[redacted" in public


def test_untrusted_objects_never_render_repr():
    class PrivateObject:
        def __repr__(self):
            raise AssertionError("repr must not run")

    assert sanitize_public({"value": PrivateObject()}) == {
        "value": "[unsupported value]"
    }
    assert json.loads(event().model_dump_json())["source"] == "derived"
