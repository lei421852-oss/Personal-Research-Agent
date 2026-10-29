"""离线核对终态命令与规范哈希：同一 token 不可能对应两份内容。"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.persistence import serialization as ser

RUN_ID = "a" * 32
TOKEN = "b" * 32
REPORT_TOKEN = "c" * 32
FINISHED = datetime(2026, 10, 25, 0, 31, 12, 184203, tzinfo=UTC)


def report(**overrides):
    arguments = {
        "expected_current_revision": None,
        "revision_token": REPORT_TOKEN,
        "schema_version": 1,
        "snapshot_kind": "answer_first",
        "public_payload": {"answer_status": "answered", "status": "completed"},
        "authority_payload": None,
        "source_reference": None,
    }
    arguments.update(overrides)
    return ser.build_report_command(**arguments)


def finalize(**overrides):
    arguments = {
        "run_id": RUN_ID,
        "finalize_token": TOKEN,
        "expected_state_version": 3,
        "status": "completed",
        "finished_at": FINISHED,
        "error": None,
        "metrics": {"model_calls": 3},
        "expected_last_event_seq": 42,
        "final_last_event_seq": 42,
        "expected_current_revision": None,
        "report": report(),
    }
    arguments.update(overrides)
    return ser.build_finalize_command(**arguments)


def test_canonical_json_is_sorted_and_compact():
    assert ser.canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert (
        ser.canonical_json({"a": [1, {"z": 1, "y": 2}]}) == b'{"a":[1,{"y":2,"z":1}]}'
    )


def test_canonical_json_keeps_non_ascii():
    assert ser.canonical_json({"q": "中文"}) == '{"q":"中文"}'.encode()


def test_canonical_json_rejects_non_json_values():
    with pytest.raises(TypeError):
        ser.canonical_json({"when": datetime.now(UTC)})


def test_sha256_is_stable_and_order_insensitive():
    first = ser.sha256_hex({"a": 1, "b": 2})
    second = ser.sha256_hex({"b": 2, "a": 1})
    assert first == second
    assert len(first) == 64
    assert ser.sha256_hex({"a": 1, "b": 3}) != first


def test_utc_isoformat_is_microsecond_utc():
    shifted = FINISHED.astimezone(timezone(timedelta(hours=8)))
    assert ser.utc_isoformat(shifted) == "2026-10-25T00:31:12.184203+00:00"
    with pytest.raises(ValueError):
        # 刻意构造 naive datetime，验证不带时区即拒绝。
        ser.utc_isoformat(datetime.fromisoformat("2026-10-25T00:00:00"))


def test_report_revision_is_preallocated_from_predecessor():
    assert report(expected_current_revision=None).revision == 1
    assert report(expected_current_revision=4).revision == 5


def test_report_hash_covers_every_persisted_field():
    base = report()
    assert report(public_payload={"x": 1}).payload_sha256 != base.payload_sha256
    assert report(schema_version=2).payload_sha256 != base.payload_sha256
    assert (
        report(snapshot_kind="execution_report").payload_sha256 != base.payload_sha256
    )
    assert report(authority_payload={"report": 1}).payload_sha256 != base.payload_sha256
    assert (
        report(source_reference={"sha256": "d" * 64}).payload_sha256
        != base.payload_sha256
    )


def test_report_command_rejects_bad_identity():
    with pytest.raises(ValueError):
        report(revision_token="short")
    with pytest.raises(ValueError):
        report(revision_token="C" * 32)
    with pytest.raises(ValueError):
        report(schema_version=0)
    with pytest.raises(ValueError):
        report(snapshot_kind="unknown")


def test_finalize_hash_binds_report_identity():
    base = finalize()
    assert (
        finalize(report=report(expected_current_revision=None)).finalize_sha256
        == base.finalize_sha256
    )
    assert (
        finalize(
            expected_current_revision=1, report=report(expected_current_revision=1)
        ).finalize_sha256
        != base.finalize_sha256
    )
    assert (
        finalize(report=report(revision_token="e" * 32)).finalize_sha256
        != base.finalize_sha256
    )
    assert (
        finalize(report=report(public_payload={"x": 2})).finalize_sha256
        != base.finalize_sha256
    )


def test_finalize_hash_covers_terminal_content():
    base = finalize()
    assert finalize(status="failed").finalize_sha256 != base.finalize_sha256
    assert finalize(error={"code": "x"}).finalize_sha256 != base.finalize_sha256
    assert finalize(metrics={}).finalize_sha256 != base.finalize_sha256
    assert finalize(final_last_event_seq=43).finalize_sha256 != base.finalize_sha256
    assert (
        finalize(
            expected_current_revision=2, report=report(expected_current_revision=2)
        ).finalize_sha256
        != base.finalize_sha256
    )


def test_finalize_without_report_is_supported():
    command = finalize(status="cancelled", report=None, expected_current_revision=None)
    assert command.report is None
    assert command.content()["reports"]["report"] is None
    assert len(command.finalize_sha256) == 64


def test_finalize_rejects_inconsistent_inputs():
    with pytest.raises(ValueError):
        finalize(status="running")
    with pytest.raises(ValueError):
        finalize(run_id="short")
    with pytest.raises(ValueError):
        finalize(finalize_token="Z" * 32)
    with pytest.raises(ValueError):
        finalize(final_last_event_seq=41)
    with pytest.raises(ValueError):
        finalize(
            expected_current_revision=2, report=report(expected_current_revision=1)
        )


def test_finalize_reuses_same_hash_for_same_command():
    first = finalize()
    second = finalize()
    assert first.finalize_sha256 == second.finalize_sha256
    assert first == second


def test_commands_are_immutable():
    command = finalize()
    with pytest.raises(FrozenInstanceError):
        command.status = "failed"
    with pytest.raises(FrozenInstanceError):
        command.report.revision = 9
