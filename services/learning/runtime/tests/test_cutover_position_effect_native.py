"""Adversarial native checks for the non-serving position effect prototype."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import psycopg

from tempo_learning_database.adapters.position_effect_store import PositionHold
from tempo_learning_runtime.position_effect_owner import accept_position_effect
from test_position_effect_owner_native import store, connect, issue, counts


def snapshot(db):
    return (counts(db), db.execute('SELECT due_at FROM position_effect_states').fetchone(),
            db.execute('SELECT attempt_id,checkpoint FROM position_effect_attempts ORDER BY attempt_id').fetchall())


@pytest.mark.parametrize('mutation,error', [
    ("DELETE FROM position_effect_authorities", psycopg.errors.ForeignKeyViolation),
    ("UPDATE position_effect_authorities SET scope='unqualified'", psycopg.errors.CheckViolation),
])
def test_existing_attempt_cannot_lose_fixture_authority(store, mutation, error):
    with connect(store) as db:
        issue(db)
        before = snapshot(db)
        with pytest.raises(error):
            with db.transaction():
                db.execute(mutation)
        assert snapshot(db) == before
        assert db.execute('SELECT scope FROM position_effect_authorities').fetchone() == ('disposable-prototype',)


def test_concurrent_same_logical_event_replays_original_operation(store):
    with connect(store) as db:
        request = issue(db)
    barrier = Barrier(2)
    def accept(operation):
        with connect(store) as db:
            barrier.wait(timeout=5)
            return accept_position_effect(db, **(request | {'operation_id': operation}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(accept, ('transport-one', 'transport-two')))
    first, replay = sorted(receipts, key=lambda receipt: receipt.replayed)
    assert not first.replayed and replay.replayed
    assert replay == type(first)(**(first.__dict__ | {'replayed': True}))
    with connect(store) as db:
        assert counts(db)[0:2] == (1, 4)
        assert db.execute('SELECT count(*),sum(checkpoint) FROM position_effect_attempts').fetchone() == (1, 1)
        assert db.execute("SELECT count(*) FROM position_effect_receipts WHERE grade='good'").fetchone()[0] == 1


def test_unsupported_game_lapse_does_not_consume_human_event(store):
    with connect(store) as db:
        request = issue(db, actual='d2d4', complete=False)
        before = snapshot(db)
        assert accept_position_effect(db, **(request | {'source_kind': 'game_lapse'})) == PositionHold(
            'separate_game_lapse_authority_required', 'unavailable_dependency')
        assert snapshot(db) == before
        receipt = accept_position_effect(db, **request)
        assert receipt.grade == 'again'
        assert receipt.operation_id == request['operation_id']
        assert counts(db)[0:2] == (1, 4)
