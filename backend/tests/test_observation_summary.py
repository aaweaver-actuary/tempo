"""Preserve the real incremental/rebuild writers' shared observation policy."""
import json
from collections import defaultdict

import pytest

from app import card_deletion
from app.study import position_checkpoint
from app.study.policy.observation_summary import observation_counts
from app.study.policy.opening_decision_evidence import reduce_observations


@pytest.mark.parametrize("observation,expected", [
    ({}, (0, 0, 0, 0, 0, 0)),
    ({"first_response_uci": None, "assistance_before_response": ["hint"]}, (0, 0, 0, 0, 0, 0)),
    ({"first_response_uci": "", "first_response_correct": False}, (1, 0, 1, 0, 0, 0)),
    ({"first_response_uci": "e2e4", "first_response_correct": 0}, (1, 0, 0, 0, 0, 0)),
    ({"first_response_uci": "e2e4", "clean": True}, (1, 1, 0, 0, 0, 0)),
    ({"first_response_uci": "d2d4", "first_response_correct": False,
      "assistance_before_response": ["hint"], "corrected": True}, (1, 0, 1, 1, 0, 1)),
    ({"manual_failure": True, "assistance_before_response": ["hint"]}, (0, 0, 0, 0, 1, 0)),
    ({"clean": 2, "manual_failure": "3", "corrected": -1}, (0, 2, 0, 0, 3, -1)),
])
def test_exact_six_counter_semantics(observation, expected):
    assert observation_counts(observation) == expected


@pytest.mark.parametrize("field", ["clean", "manual_failure", "corrected"])
@pytest.mark.parametrize("value,error", [(None, TypeError), ("not-an-int", ValueError)])
def test_numeric_conversion_errors_are_not_hidden(field, value, error):
    with pytest.raises(error):
        observation_counts({field: value})


def event(sequence, kind, *, decision="d", index=0, day="2020-11-12", **extra):
    return dict(sequence=sequence, kind=kind, decision_id=decision,
                decision_index=index, expected_uci="e2e4",
                observed_at=f"{day}T12:00:00+00:00", **extra)


def test_actual_incremental_and_removal_rebuild_preserve_totals_and_days(monkeypatch):
    rows, totals, days, deltas, order = {}, defaultdict(lambda: [0]*6), set(), [], []
    storage = position_checkpoint.storage
    def prior(db, key):
        order.append(key)
        return (json.dumps(rows[key]),) if key in rows else None
    def increment(db, values):
        decision, *delta = values
        deltas.append(tuple(delta))
        totals[decision] = [a+b for a,b in zip(totals[decision], delta)]
    def insert_day(db, key):
        fresh = key not in days
        days.add(key)
        return fresh
    def save(db, values):
        rows[(values[0], values[1])] = json.loads(values[-1])
    monkeypatch.setattr(storage, "prior_observation", prior)
    monkeypatch.setattr(storage, "increment_summary", increment)
    monkeypatch.setattr(storage, "insert_clean_day", insert_day)
    day_increments = []
    monkeypatch.setattr(storage, "increment_clean_days", lambda db, key: day_increments.append(key))
    monkeypatch.setattr(storage, "upsert_observation", save)
    db = object()
    # Real reducer prefixes: assistance alone, incorrect response, correction.
    events = [event(1, "assistance", assistance="hint"),
              event(2, "first_response", response_uci="d2d4"),
              event(3, "correction", response_uci="e2e4")]
    for end in range(1, 4):
        position_checkpoint._persist_observations(db, "wrong", reduce_observations(events[:end], "UTC"))
    assert deltas[:3] == [(0,0,0,0,0,0), (1,0,1,1,0,0), (0,0,0,0,0,1)]
    # Repeated clean day counts once; another day separately. Exact replay is inert.
    for attempt, day in [("clean1","2020-11-12"), ("clean2","2020-11-12"), ("clean3","2020-11-13")]:
        observations = reduce_observations([event(1, "first_response", day=day, response_uci="e2e4")], "UTC")
        position_checkpoint._persist_observations(db, attempt, observations)
        before = len(deltas)
        position_checkpoint._persist_observations(db, attempt, observations)
        assert len(deltas) == before
    assert totals["d"] == [4,3,1,1,0,1]
    assert days == {("d","2020-11-12"), ("d","2020-11-13")}
    assert day_increments == [("d",), ("d",)]
    # The real purge orchestrator rebuilds only survivors, including duplicate day.
    deletion = card_deletion.storage
    monkeypatch.setattr(deletion, "affected_evidence_decisions", lambda *args: ["d"])
    def purge(*args):
        rows.pop(("wrong",0))
    monkeypatch.setattr(deletion, "purge_evidence_records", purge)
    monkeypatch.setattr(deletion, "remaining_evidence_observations", lambda *args: [(v["decision_id"],json.dumps(v)) for v in rows.values()])
    rebuilt, rebuilt_days = {}, {}
    monkeypatch.setattr(deletion, "record_evidence_summary", lambda db, decision, summary: rebuilt.update({decision: summary["counts"]}))
    monkeypatch.setattr(deletion, "record_evidence_clean_days", lambda db, decision, selected: rebuilt_days.update({decision: selected}))
    card_deletion._purge_opening_evidence(db, ["removed-card"], [4])
    assert rebuilt == {"d": [3,3,0,0,0,0]}
    assert rebuilt_days == {"d": {"2020-11-12", "2020-11-13"}}
    assert rebuilt["d"] == [a-b for a,b in zip(totals["d"], (1,0,1,1,0,1))]


def test_incremental_writer_keeps_signed_delta_and_decision_index_order(monkeypatch):
    # Existing numeric compatibility values exercise subtraction, not bool coercion.
    observations = [dict(decision_id=d, decision_index=i, clean=0, manual_failure=0,
        corrected=0, response_at=None, observed_at="2020-11-12T12:00:00Z", failure_at=None)
        for d,i in [("z",2),("a",1),("a",0)]]
    seen, deltas = [], []
    def prior(db, key):
        seen.append(key)
        return (json.dumps({"manual_failure":2,"corrected":1}),)
    monkeypatch.setattr(position_checkpoint.storage, "prior_observation", prior)
    monkeypatch.setattr(position_checkpoint.storage, "increment_summary", lambda db, values: deltas.append(values))
    monkeypatch.setattr(position_checkpoint.storage, "upsert_observation", lambda *args: None)
    position_checkpoint._persist_observations(object(), "attempt", observations)
    assert seen == [("attempt",0),("attempt",1),("attempt",2)]
    assert deltas == [(d,0,0,0,0,-2,-1) for d in ["a","a","z"]]
