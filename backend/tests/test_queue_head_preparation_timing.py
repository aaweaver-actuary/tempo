"""Position preparation spans preserve results and disclose only bounded labels."""

from contextlib import contextmanager, nullcontext
import json
from types import SimpleNamespace as NS

import pytest

from app.services import background_runtime, queue_next_card
from tempo_evidence_math.position_learning import Hold


LABELS = {
    "position_source_prepare",
    "position_due_carrier_scan",
    "position_prior_clean_proofs",
    "position_route_choice",
    "position_context_witnesses",
    "position_plan_proposal",
}


@pytest.mark.parametrize("timing", ["1", "0", None])
@pytest.mark.parametrize("held", [False, True])
def test_position_preparation_timing_preserves_proposal_and_source_hold(
    monkeypatch, caplog, timing, held
):
    from tempo_legacy_database import connections
    from tempo_legacy_database.adapters.study import (
        game_feedback,
        position_due_candidates,
        position_issued_plans,
    )
    from app.services import position_queue_admission
    from app.study.position_learning import issued_plan_service, route_planner

    payload = {
        "entry_id": 41,
        "card_id": "private-card",
        "expected_revision": 4,
        "queue_date": "2026-11-02",
    }
    key = "private-position"
    target = NS(key=key)
    witness = NS(definition=target)
    source = NS(
        authored_token=NS(authored_digest="private-digest"), routes=("private-route",)
    )
    route = NS(decision_occurrences=(NS(definition=target, ply=2),), target_ply=2)
    proposal = object()
    calls = []

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchall(self):
            return self.value

        def fetchone(self):
            return self.value

    class Reader:
        def execute_native(self, sql, params=()):
            calls.append("sql")
            if "SELECT p.attempt_id" in sql:
                return Result([])
            if "FROM queue_attempt_origins" in sql:
                return Result(
                    {
                        "admission_repertoire_id": "private-repertoire",
                        "issued_as_head": 0,
                        "content_type": "opening",
                    }
                )
            if "FROM study_position_owners" in sql:
                return Result(
                    {
                        "mode": "active",
                        "epoch": "epoch",
                        "source_digest": "private-digest",
                    }
                )
            if "SELECT attempt_id FROM study_position_issued_plans" in sql:
                return Result([{"attempt_id": "private-attempt"}])
            raise AssertionError(sql)

    @contextmanager
    def borrowed():
        yield Reader()

    class DueReader:
        def __init__(self, *a, **kw):
            pass

        def read_due_page(self, **kw):
            calls.append("due")
            return NS(candidates=(NS(state=NS(definition=target)),), next_cursor=None)

        def read_context(self, keys):
            calls.append("context")
            return (witness,)

    monkeypatch.setattr(connections, "read_connection", borrowed)
    monkeypatch.setattr(
        queue_next_card,
        "_cached_position_source",
        lambda *a: (
            calls.append("source")
            or (Hold("position_source_graph_busy") if held else source)
        ),
    )
    monkeypatch.setattr(
        position_due_candidates, "PositionDueCandidateReader", DueReader
    )
    monkeypatch.setattr(game_feedback, "read_position_urgency", lambda *a, **kw: None)
    monkeypatch.setattr(
        position_queue_admission,
        "select_carrier",
        lambda *a, **kw: calls.append("carrier") or NS(presentation_revision=4),
    )
    monkeypatch.setattr(
        position_issued_plans,
        "read_clean_presentation_proof",
        lambda *a: NS(attempt_id="private-attempt"),
    )
    monkeypatch.setattr(
        position_issued_plans, "read_applicable_clean_proofs", lambda *a, **kw: ()
    )
    monkeypatch.setattr(
        route_planner, "choose_plan", lambda *a, **kw: calls.append("route") or route
    )
    monkeypatch.setattr(
        issued_plan_service,
        "prepare_position_issuance",
        lambda *a: calls.append("proposal") or proposal,
    )
    monkeypatch.setenv("TEMPO_TRAINING_TIMINGS", timing or "1")
    caplog.set_level("INFO", logger="tempo.foreground_latency")
    with (
        background_runtime.measure_training_timing(
            "queue.issue_head", "private-operation"
        )
        if timing is not None
        else nullcontext(None)
    ) as measurement:
        result = queue_next_card.prepare_queue_head(payload)
    assert result.payload is payload
    assert result.mode == ("hold" if held else "position")
    assert result.reason == ("position_source_graph_busy" if held else None)
    assert result.proposal is (None if held else proposal)
    assert calls == (
        ["sql", "sql", "sql", "source"]
        if held
        else [
            "sql",
            "sql",
            "sql",
            "source",
            "due",
            "carrier",
            "sql",
            "route",
            "context",
            "proposal",
        ]
    )
    if measurement is not None:
        assert set(measurement["stages_ms"]) == (
            {"position_source_prepare"} if held else LABELS
        )
        assert all(value >= 0 for value in measurement["stages_ms"].values())
        logged = json.dumps(measurement)
        assert "private-" not in logged
    else:
        assert not any("training_timing" in record.message for record in caplog.records)
