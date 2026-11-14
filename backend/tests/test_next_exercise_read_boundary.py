"""Preparation reads keep bounded SQL inside the Study storage owner."""

import inspect
from contextlib import contextmanager

import pytest
from tempo_storage import connections
from tempo_storage.study import next_exercise_reads as reads

from app.services.queue_next_card import prepare_queue_head


class Connection:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_native(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result


def test_named_reads_keep_query_bounds_parameters_and_facts():
    db = Connection([{"attempt_id": "a"}, {"attempt_id": "b"}])
    assert reads.original_attempt_ids(db, entry_id=1, card_id="c", revision=2) == (
        "a",
        "b",
    )
    assert "LIMIT 2" in db.calls[-1][0] and db.calls[-1][1] == (1, "c", 2)
    db.result = {
        "admission_repertoire_id": "r",
        "issued_as_head": True,
        "content_type": "opening",
    }
    assert reads.admission_origin(
        db, entry_id=1, card_id="c", revision=2
    ) == reads.AdmissionOrigin("r", True, "opening")
    db.result = [{"repertoire_id": "r", "mode": "active"}]
    assert reads.applicable_owner_exists(db, card_id="c")
    assert "LIMIT 2" in db.calls[-1][0] and db.calls[-1][1] == ("c",)
    db.result = {"mode": "active", "epoch": 3, "source_digest": "d"}
    assert reads.position_owner(db, repertoire_id="r") == reads.PositionOwner(
        "active", 3, "d"
    )
    assert "JOIN study_position_epochs" in db.calls[-1][0] and db.calls[-1][1] == ("r",)
    db.result = [{"attempt_id": "p"}]
    assert reads.prior_attempt_ids(
        db, repertoire_id="r", owner_epoch=3, study_day="day"
    ) == ("p",)
    assert "ORDER BY issued_at DESC,attempt_id LIMIT 20" in db.calls[-1][0]
    assert db.calls[-1][1] == ("r", 3, "day")
    assert "execute_native" not in inspect.getsource(prepare_queue_head)


@pytest.mark.parametrize(
    ("attempts", "origin", "owner_exists", "expected"),
    [
        (("a", "b"), None, False, ("hold", "position_original_admission_ambiguous")),
        ((), None, False, ("hold", "position_original_admission_unavailable")),
        (
            (),
            reads.AdmissionOrigin(None, False, "opening"),
            True,
            ("hold", "position_admission_scope_ambiguous"),
        ),
        ((), reads.AdmissionOrigin(None, False, "opening"), False, ("legacy", None)),
        ((), reads.AdmissionOrigin(None, False, "tactics"), True, ("legacy", None)),
    ],
)
def test_ambiguity_and_missing_origin_do_not_fall_back(
    monkeypatch, attempts, origin, owner_exists, expected
):
    @contextmanager
    def borrowed():
        yield object()

    monkeypatch.setattr(connections, "read_connection", borrowed)
    monkeypatch.setattr(reads, "original_attempt_ids", lambda *a, **kw: attempts)
    monkeypatch.setattr(reads, "admission_origin", lambda *a, **kw: origin)
    monkeypatch.setattr(reads, "applicable_owner_exists", lambda *a, **kw: owner_exists)
    result = prepare_queue_head({"entry_id": 1, "card_id": "c", "expected_revision": 2})
    assert (result.mode, result.reason) == expected


@pytest.mark.parametrize(
    "owner",
    [
        None,
        reads.PositionOwner("inactive", 1, "d"),
        reads.PositionOwner("shadow", 1, "d"),
    ],
)
def test_absent_inactive_and_shadow_owner_preserve_legacy(monkeypatch, owner):
    @contextmanager
    def borrowed():
        yield object()

    monkeypatch.setattr(connections, "read_connection", borrowed)
    monkeypatch.setattr(reads, "original_attempt_ids", lambda *a, **kw: ())
    monkeypatch.setattr(
        reads,
        "admission_origin",
        lambda *a, **kw: reads.AdmissionOrigin("r", False, "opening"),
    )
    monkeypatch.setattr(reads, "position_owner", lambda *a, **kw: owner)
    result = prepare_queue_head({"entry_id": 1, "card_id": "c", "expected_revision": 2})
    assert result.mode == "legacy"
