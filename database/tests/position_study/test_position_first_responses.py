from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.services import postgres_opening_evidence as evidence
from tempo_legacy_database.adapters.study import position_first_responses as storage


def plan():
    key = NS(repertoire_id='r', variant='standard', position='position')
    definition = NS(key=key, expected_uci='e2e4', policy_version='v1')
    slot = NS(definition=definition, original_ply=6,
              fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1')
    return NS(attempt_id='attempt', queue_entry_id=21, original_card_id='card',
              original_card_revision=4, owner_epoch='epoch', decision_slots=(slot,))


def event(**changes):
    args = dict(event_id='event', decision_index=0, actual_uci='d2d4', manual_failure=False,
                assistance_before=False, observed_at='2026-11-02T12:00:00+00:00',
                classify_alternative=lambda *_: 'wrong')
    args.update(changes)
    return evidence.position_first_response_envelope(plan(), **args)


def test_server_adjudicates_issued_slot_and_never_guesses_alternative():
    row = event()
    assert (row['decision_index'], row['original_ply'], row['disposition']) == (0, 6, 'wrong')
    for disposition in ('alternate', 'unverified'):
        assert event(classify_alternative=lambda *_, d=disposition: d)['disposition'] == disposition
    assert event(actual_uci='e2e4')['disposition'] == 'expected'
    assert event(actual_uci='e2e5')['disposition'] == 'illegal'
    assert event(actual_uci=None, manual_failure=True)['disposition'] == 'manual_failure'
    with pytest.raises(ValueError):
        event(classify_alternative=lambda *_: 'maybe')


def test_digest_and_frozen_definition_are_checked():
    row = event()
    digest = evidence._position_digest(row)
    assert evidence.decode_position_first_response(row, digest, plan()) == row
    changed = dict(row, original_ply=0)
    with pytest.raises(ValueError):
        evidence.decode_position_first_response(changed, digest, plan())
    with pytest.raises(ValueError):
        evidence.decode_position_first_response(changed, evidence._position_digest(changed), plan())


def test_abandoned_wrong_retries_once_and_changed_bytes_conflict(monkeypatch):
    saved = []
    effects = []
    monkeypatch.setattr(storage, 'receipt', lambda *args: saved[:])
    def insert(_db, row, digest, _json, accepted):
        saved.append(dict(payload_json=row, payload_digest=digest, accepted_at=accepted))
        return ('event',)
    monkeypatch.setattr(storage, 'insert', insert)
    def grade(_db, attempt, ids):
        effects.append((attempt, ids))
        return object()
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    row = event()
    first = evidence.persist_position_first_response(None, plan(), row, now, apply_grade=grade)
    again = evidence.persist_position_first_response(None, plan(), row, now, apply_grade=grade)
    assert first == again and effects == [('attempt', ('event',))]
    with pytest.raises(evidence.EvidenceConflict):
        evidence.persist_position_first_response(None, plan(), event(assistance_before=True), now, apply_grade=grade)
    assert len(effects) == 1


def test_correct_stays_pending_and_grade_failure_aborts(monkeypatch):
    saved = []
    monkeypatch.setattr(storage, 'receipt', lambda *args: saved[:])
    monkeypatch.setattr(storage, 'insert', lambda _db, row, digest, _json, accepted:
                        saved.append(dict(payload_json=row, payload_digest=digest, accepted_at=accepted)) or ('event',))
    def unexpected(*_):
        raise AssertionError('positive response must await completion')
    evidence.persist_position_first_response(None, plan(), event(actual_uci='e2e4'),
                                             datetime.now(timezone.utc), apply_grade=unexpected)
    saved.clear()
    def held(*_):
        raise evidence.EvidenceConflict('grade held')
    with pytest.raises(evidence.EvidenceConflict):
        evidence.persist_position_first_response(None, plan(), event(), datetime.now(timezone.utc),
                                                 apply_grade=held)
