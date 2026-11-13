"""Writer spans preserve the exact source fence, plan application and lock order."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
import json

import pytest

from app import queue_commands
from app.services import background_runtime, queue_next_card
from tempo_evidence_math.position_learning import Hold


@pytest.mark.parametrize("timing", ["1", "0", None])
@pytest.mark.parametrize("held", [False, True])
def test_position_writer_timing_preserves_success_and_source_busy_hold(
    monkeypatch, timing, held
):
    from tempo_storage.authored_repertoires import (
        position_study_source,
    )
    from app.study.position_learning import issuance as position_issued_plans

    payload = {
        "entry_id": 41,
        "card_id": "private-card",
        "expected_revision": 4,
        "queue_date": "2026-11-02",
    }
    original_source = object()
    prepared = queue_next_card.PreparedQueueHead(
        payload, "position", NS(source=original_source)
    )
    calls = []
    plan = NS(to_wire=lambda: {"attempt_id": "private-attempt"})
    monkeypatch.setattr(
        queue_commands, "current_study_day", lambda db: payload["queue_date"]
    )
    def fence_original(database, source):
        assert source is original_source
        calls.append("source")
        return Hold("position_source_graph_busy") if held else None

    monkeypatch.setattr(position_study_source, "fence_position_source", fence_original)
    def apply_original(database, proposal):
        assert proposal is prepared.proposal
        calls.append("plan")
        return plan

    monkeypatch.setattr(position_issued_plans, "issue_in_borrowed_transaction", apply_original)
    monkeypatch.setattr(
        queue_commands, "_lock_attempt_card", lambda *a: calls.append("card")
    )
    monkeypatch.setattr(queue_commands, "lock_queue_date_for_position", lambda *a: calls.append("queue"))
    monkeypatch.setattr(queue_commands.queue_storage, "retired_issuance_target", lambda *a: False)
    monkeypatch.setattr(queue_commands.queue_storage, "issued_origin", lambda *a: True)
    monkeypatch.setattr(
        queue_commands.queue_storage,
        "prepare_queue_head",
        lambda *a: lambda day: {"id": 41},
    )
    def validate_original(database, entry, card, revision, *, current_day):
        assert (entry, card, revision, current_day) == (41, "private-card", 4, payload["queue_date"])
        calls.append("validate")

    monkeypatch.setattr(queue_commands, "validate_available_attempt", validate_original)
    monkeypatch.setattr(
        queue_commands.queue_storage,
        "stamp_issued_head",
        lambda *a: calls.append("stamp") or True,
    )
    monkeypatch.setenv("TEMPO_TRAINING_TIMINGS", timing or "1")
    with (
        background_runtime.measure_training_timing(
            "queue.issue_head", "private-operation"
        )
        if timing is not None
        else nullcontext(None)
    ) as measurement:
        result = queue_commands.issue_queue_head(object(), prepared)
    assert result == {
        "issued": not held,
        "position_study_mode": "position",
        "position_presentation": None if held else {"attempt_id": "private-attempt"},
        "position_study_hold": "position_source_graph_busy" if held else None,
    }
    assert calls == (
        ["source"] if held else ["source", "plan", "card", "queue", "validate", "stamp"]
    )
    if measurement is not None:
        assert set(measurement["stages_ms"]) == (
            {"position_source_fence"}
            if held
            else {"position_source_fence", "position_issue_apply"}
        )
        assert all(value >= 0 for value in measurement["stages_ms"].values())
        assert "private-" not in json.dumps(measurement)
