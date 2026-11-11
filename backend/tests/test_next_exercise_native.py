"""Deferred native proof against the current owned Study PostgreSQL fixture."""
from datetime import datetime, timezone

import pytest

from native_study_fixture import case, owned_postgres


def test_native_next_exercise_borrowed_reads_rollback_and_original_issuance(case, monkeypatch):
    from app import command_dispatch, queue_commands
    from app.product_composition.study import issue_returned_queue_head
    from app.services.queue_next_card import prepare_queue_head
    from tempo_storage import connections
    from tempo_storage.http_composition import queue_projection
    from tempo_storage.study import next_exercise_reads as reads

    day = datetime.now(timezone.utc).date().isoformat()
    with case.transaction() as db:
        entry = db.execute_native(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(%s,%s,0,0) RETURNING id",
            (day, case.card_id),
        ).fetchone()[0]
        identity = dict(entry_id=entry, card_id=case.card_id, revision=1)
        assert reads.original_attempt_ids(db, **identity) == ()
        origin = reads.admission_origin(db, **identity)
        assert origin and not origin.issued_as_head
        assert not reads.applicable_owner_exists(db, card_id=case.card_id)
        assert reads.position_owner(db, repertoire_id='missing-owner') is None
        assert reads.prior_attempt_ids(db, repertoire_id='missing-owner', owner_epoch='missing', study_day=day) == ()

    monkeypatch.setattr(connections, 'read_connection', case.transaction)
    monkeypatch.setattr(queue_commands, 'current_study_day', lambda db: day)
    card = dict(queue_entry_id=entry, id=case.card_id, revision=1)
    payload = dict(queue_date=day, entry_id=entry, card_id=case.card_id, expected_revision=1)
    prepared = prepare_queue_head(payload)
    assert prepared.mode == 'legacy'
    with pytest.raises(RuntimeError, match='rollback issuance'):
        with case.transaction() as db:
            assert queue_commands.issue_queue_head(db, prepared)['issued']
            raise RuntimeError('rollback issuance')
    with case.transaction() as db:
        assert not reads.admission_origin(db, **identity).issued_as_head

    calls = []
    def dispatch(name, request, *, idempotency_key):
        assert name == 'queue.issue_head' and idempotency_key is None
        calls.append(request)
        with case.transaction() as db:
            return queue_commands.issue_queue_head(db, prepare_queue_head(request))
    monkeypatch.setattr(command_dispatch, 'dispatch_command', dispatch)
    first = issue_returned_queue_head(day, card, read_connection=case.transaction, queue_projection_storage=queue_projection)
    second = issue_returned_queue_head(day, card, read_connection=case.transaction, queue_projection_storage=queue_projection)
    assert first == second == {'issued': True}
    assert calls == [payload]
    with case.transaction() as db:
        assert reads.admission_origin(db, **identity).issued_as_head
        assert reads.original_attempt_ids(db, **identity) == ()
