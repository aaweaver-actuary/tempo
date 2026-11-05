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
    from tempo_legacy_database.adapters.authored_repertoires import (
        position_study_source,
    )
    from tempo_legacy_database.adapters.study import (
        position_issued_plans,
        game_feedback,
    )
    from app.services import legacy_study_remediation

    payload = {
        "entry_id": 41,
        "card_id": "private-card",
        "expected_revision": 4,
        "queue_date": "2026-11-02",
    }
    prepared = queue_next_card.PreparedQueueHead(
        payload, "position", NS(source=object())
    )
    calls = []
    plan = NS(to_wire=lambda: {"attempt_id": "private-attempt"})
    monkeypatch.setattr(
        queue_commands, "current_study_day", lambda db: payload["queue_date"]
    )
    monkeypatch.setattr(
        position_study_source,
        "fence_position_source",
        lambda *a: (
            calls.append("source")
            or (Hold("position_source_graph_busy") if held else None)
        ),
    )
    monkeypatch.setattr(
        position_issued_plans,
        "issue_in_borrowed_transaction",
        lambda *a: calls.append("plan") or plan,
    )
    monkeypatch.setattr(
        queue_commands, "_lock_attempt_card", lambda *a: calls.append("card")
    )
    monkeypatch.setattr(
        queue_commands.queue_storage, "is_postgres_connection", lambda *a: False
    )
    monkeypatch.setattr(queue_commands.queue_storage, "issued_origin", lambda *a: True)
    monkeypatch.setattr(
        queue_commands.queue_storage,
        "prepare_queue_head",
        lambda *a: lambda day: {"id": 41},
    )
    monkeypatch.setattr(
        queue_commands,
        "validate_available_attempt",
        lambda *a: calls.append("validate"),
    )
    monkeypatch.setattr(
        legacy_study_remediation, "bind_first_issuance", lambda *a: calls.append("bind")
    )
    monkeypatch.setattr(
        queue_commands.queue_storage,
        "stamp_issued_head",
        lambda *a: calls.append("stamp") or True,
    )
    monkeypatch.setattr(game_feedback, "installed", lambda *a: False)
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
        ["source"] if held else ["source", "plan", "card", "validate", "bind", "stamp"]
    )
    if measurement is not None:
        assert set(measurement["stages_ms"]) == (
            {"position_source_fence"}
            if held
            else {"position_source_fence", "position_issue_apply"}
        )
        assert all(value >= 0 for value in measurement["stages_ms"].values())
        assert "private-" not in json.dumps(measurement)
