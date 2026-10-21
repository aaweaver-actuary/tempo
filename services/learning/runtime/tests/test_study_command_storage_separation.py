"""Study command decisions bind semantic storage operations without owning SQL."""
from datetime import datetime
import json
import sqlite3
from types import SimpleNamespace

import chess
from fastapi import HTTPException
import pytest

from tempo_learning_runtime import queue_attempt_origins as origins, study_bootstrap as bootstrap
from tempo_learning_runtime import teaching_commands as teaching, remediation_adapter
from tempo_learning_runtime.review_conflicts import ReviewConflict


def test_queue_recovery_rejects_unprovable_identity_before_reading_card(monkeypatch):
    reads = []
    monkeypatch.setattr(origins.storage, "recovery_origins", lambda *_: reads.append("origins") or [])
    monkeypatch.setattr(origins.storage, "current_card_definition", lambda *_: pytest.fail("Ambiguous origin read a card"))
    for attempt in [None, "attempt"]:
        with pytest.raises(ReviewConflict) as conflict:
            origins.recover_queue_entry(None, "card", SimpleNamespace(
                attempt_id=attempt, queue_entry_id=2, expected_revision=None))
        assert conflict.value.code == "queue_attempt_unprovable"
    assert reads == ["origins"]


def test_origin_recovery_and_retained_completion_share_caller_transaction():
    with sqlite3.connect(":memory:") as database:
        database.row_factory = sqlite3.Row
        database.executescript("""
          CREATE TABLE cards(id TEXT,revision INTEGER,start_fen TEXT,moves_json TEXT,trained_color TEXT,content_type TEXT);
          CREATE TABLE queue_attempt_origins(queue_entry_id INTEGER,card_id TEXT,revision INTEGER,start_fen TEXT,
            moves_json TEXT,trained_color TEXT,content_type TEXT,legacy INTEGER,last_status TEXT,review_result_json TEXT);
          INSERT INTO cards VALUES('card',2,'fen','[]','white','opening');
          INSERT INTO queue_attempt_origins VALUES(4,'card',2,'fen','[]','white','opening',0,'queued',NULL);
        """)
        request = SimpleNamespace(attempt_id="attempt", queue_entry_id=4, expected_revision=2)
        entry = origins.recover_queue_entry(database, "card", request)
        assert (entry["id"], entry["status"], entry["_recovered"]) == (4, "queued", True)
        origins.retain_recovered_completion(database, entry, '{"rating":"correct"}')
        assert database.in_transaction
        assert origins.recover_queue_entry(database, "card", request)["status"] == "complete"
        database.rollback()
        assert origins.recover_queue_entry(database, "card", request)["status"] == "queued"


@pytest.mark.parametrize("changed", [None, "revision", "moves_json"])
def test_failure_marker_keeps_exact_definition_fence(monkeypatch, changed):
    origin = dict(card_id="card", revision=2, start_fen="fen", moves_json="[]", trained_color="white", content_type="opening")
    current = {**origin, "id": "card"}
    if changed:
        current[changed] = "changed"
    monkeypatch.setattr(origins.storage, "failure_marker_facts", lambda *_: ([origin], current))
    if changed:
        with pytest.raises(ReviewConflict) as conflict:
            origins.validate_failure_marker(None, 4, "card", 2)
        assert conflict.value.code == "queue_attempt_unprovable"
    else:
        assert origins.validate_failure_marker(None, 4, "card", 2) is None


@pytest.mark.parametrize("payload,enqueue", [({"study_only_mode": True}, False), ({"tactics_new_per_day": 7}, True)])
def test_settings_keep_alias_encoding_and_essential_enqueue_order(monkeypatch, payload, enqueue):
    events = []
    settings = {column: False if name in bootstrap.BOOLEAN_FIELDS else 4 for name, column in bootstrap.FIELDS.items()}
    def write(_database, columns):
        changes = list(columns)
        events.append(("write", changes))
        settings.update(changes)
    monkeypatch.setattr(bootstrap.storage, "write_settings_columns", write)
    monkeypatch.setattr(bootstrap.storage, "settings_row", lambda *_: events.append(("read",)) or settings)
    monkeypatch.setattr(bootstrap, "enqueue_task_in_transaction", lambda *args: events.append(("enqueue", args[1])))
    result = bootstrap.update_settings(None, payload)
    assert events[0] == ("write", [(bootstrap.FIELDS[name], int(value)) for name, value in payload.items()])
    assert [event[0] for event in events] == (["write", "enqueue", "read"] if enqueue else ["write", "read"])
    assert all(result[name] == value for name, value in payload.items())


def test_move_membership_uses_supplied_active_definition_rows(monkeypatch):
    card = dict(serving_active=1, archived=0, superseded_by=None, revision=2)
    monkeypatch.setattr(bootstrap.storage, "membership_card", lambda *_: card)
    monkeypatch.setattr(bootstrap, "require_card_not_deleted", lambda *_: None)
    definition = dict(card_id="authored", exercise_revision=2, starting_fen=chess.STARTING_FEN,
                      specification={"accepted_lines": [["e2e4", "e7e5"]]})
    monkeypatch.setattr(bootstrap.storage, "membership_candidates", lambda *_: [dict(
        id="card", revision=2, trained_color="white", authored_id="authored",
        payload_json=json.dumps({"publication": {"definitions": [definition]}}))])
    assert bootstrap.move_membership(None, "card", 2, chess.STARTING_FEN, "e2e4")["verified"] is True
    assert bootstrap.move_membership(None, "card", 2, chess.STARTING_FEN, "d2d4")["verified"] is False


@pytest.mark.parametrize("ply,accepted", [(0, True), (1, False)])
def test_teaching_keeps_learner_turn_validation_before_writing(monkeypatch, ply, accepted):
    events = []
    card = dict(archived=0, serving_active=1, superseded_by=None, revision=2,
                moves_json='["e2e4","e7e5"]', content_type="opening",
                start_fen=chess.STARTING_FEN, trained_color="white")
    monkeypatch.setattr(teaching.storage, "lock_teaching_card", lambda *_: events.append("lock") or card)
    monkeypatch.setattr(teaching, "require_card_not_deleted", lambda *_: events.append("deletion"))
    monkeypatch.setattr(remediation_adapter, "AcceptedDefinitions", lambda _db: SimpleNamespace(
        for_card=lambda _card: SimpleNamespace(active=True, card_revision=2)))
    def record(_database, card_id, revision, position, taught_at):
        events.append("write")
        assert datetime.fromisoformat(taught_at).tzinfo is not None
        return dict(card_id=card_id, revision=revision, ply=position, taught_at=taught_at)
    monkeypatch.setattr(teaching.storage, "record_teaching_state", record)
    payload = {"card_id": "card", "teaching_state": {"revision": 2, "ply": ply}}
    if accepted:
        assert teaching.record_teaching(None, payload)["ply"] == ply
        assert events == ["lock", "deletion", "write"]
    else:
        with pytest.raises(HTTPException) as error:
            teaching.record_teaching(None, payload)
        assert error.value.status_code == 422
        assert events == ["lock", "deletion"]
