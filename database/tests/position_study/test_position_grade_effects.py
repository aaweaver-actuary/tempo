"""Focused policy/transition checks; PostgreSQL atomicity is a separate proof."""
from datetime import date, datetime, timezone

from fsrs import Card

from app.study.position_learning.daily_credit import CurrentPositionState
from tempo_evidence_math.position_learning import (
    AcceptedEvidence, DueAtIssuance, FirstResponse, Hold, PositionDefinition, PositionKey,
)
from tempo_legacy_database.adapters.study.position_grade_effects import _decide


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
DAY = date(2026, 11, 2)
KEY = PositionKey('r', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -')
DEF = PositionDefinition(KEY, 'e2e4', 'v1')


def facts(move, *, due=True, complete=False, revision=1):
    first = FirstResponse('attempt', 0, DEF, move, False, NOW, 'event')
    evidence = AcceptedEvidence(first, True, 'expected' if move == 'e2e4' else 'wrong',
                                complete, complete, 'epoch', 'digest', NOW, DAY)
    witness = DueAtIssuance(DEF, due, DAY, 'epoch', 1, NOW)
    current = CurrentPositionState(DEF, 'epoch', revision, DAY)
    return evidence, witness, current, (), Card(card_id=100, due=NOW).to_json()


def test_correct_pending_does_not_finalize_receipt():
    assert _decide(*facts('e2e4')) == Hold('awaiting_saved_completion')


def test_new_wrong_applies_again_even_when_nondue():
    result = _decide(*facts('d2d4', due=False))
    assert result.acceptance.receipt.final.grade == 'again'
    assert result.schedule is not None


def test_stale_due_correct_observes_without_schedule():
    result = _decide(*facts('e2e4', complete=True, revision=2))
    assert result.acceptance.receipt.final.grade == 'observe'
    assert result.acceptance.receipt.final.reason == 'stale_due_issuance'
    assert result.schedule is None


def test_state_locks_use_stable_order_and_authored_source():
    from tempo_legacy_database.adapters.study.position_grade_effects import _lock_states

    class Cursor:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    class Database:
        def __init__(self):
            self.keys = []
        def execute_native(self, sql, parameters):
            if 'study_position_owners' in sql:
                return Cursor({'epoch': 'epoch', 'mode': 'active', 'source_digest': 'authored'})
            self.keys.append(parameters)
            return Cursor({'owner_epoch': 'epoch', 'source_digest': 'authored'})

    other = PositionKey('r', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -')
    db = Database()
    result = _lock_states(db, repertoire_id='r', owner_epoch='epoch',
                          authored_digest='authored', positions=(other, KEY, other))
    assert set(result) == {KEY, other}
    assert db.keys == [(k.repertoire_id, k.variant, k.position) for k in sorted((KEY, other))]


def test_grade_writer_aborts_before_writes_when_correct_is_pending(monkeypatch):
    from types import SimpleNamespace
    from tempo_legacy_database.adapters.study import position_grade_effects as writer

    evidence, due, _, _, state_json = facts('e2e4')
    response = evidence.first_response
    plan = SimpleNamespace(plan_id='p', attempt_id='attempt', owner_epoch='epoch',
        target=DEF, due_positions=(due,), source_digest='original')
    monkeypatch.setattr(writer, '_load_plan', lambda *_: plan)
    monkeypatch.setattr(writer, '_load_events', lambda *_: ((response, 'expected', 'digest', NOW),))
    monkeypatch.setattr(writer, '_lock_states', lambda *_, **__: {KEY: {
        'expected_uci': 'e2e4', 'policy_version': 'v1', 'fsrs_json': state_json,
        'fsrs_internal_card_id': 100, 'version': 1}})

    class Cursor:
        def fetchall(self): return []
    class Database:
        read_only = False
        raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name='INTRANS')))
        def __init__(self): self.writes = []
        def execute_native(self, sql, params):
            if sql.lstrip().startswith(('INSERT', 'UPDATE')): self.writes.append(sql)
            return Cursor()
    db = Database()
    result = writer.apply_in_borrowed_transaction(db, 'attempt', ('event',), owner_epoch='epoch',
        authored_digest='authored', accepted_study_day=DAY, accepted_at=NOW)
    assert result == Hold('awaiting_saved_completion')
    assert db.writes == []


def test_two_due_decisions_write_two_effects_and_propagate_late_failure(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from tempo_legacy_database.adapters.study import position_grade_effects as writer

    key2 = PositionKey('r', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -')
    def2 = PositionDefinition(key2, 'e7e5', 'v1')
    first = FirstResponse('attempt', 0, DEF, 'e2e4', False, NOW, 'event-1')
    second = FirstResponse('attempt', 1, def2, 'e7e5', False, NOW, 'event-2')
    dues = tuple(DueAtIssuance(d, True, DAY, 'epoch', 1, NOW) for d in (DEF, def2))
    plan = SimpleNamespace(plan_id='p', attempt_id='attempt', owner_epoch='epoch',
                           target=DEF, due_positions=dues, source_digest='original')
    monkeypatch.setattr(writer, '_load_plan', lambda *_: plan)
    monkeypatch.setattr(writer, '_load_events', lambda *_: (
        (first, 'expected', 'digest1', NOW), (second, 'expected', 'digest2', NOW)))
    states = {d.key: {'expected_uci': d.expected_uci, 'policy_version': 'v1',
        'fsrs_json': Card(card_id=n, due=NOW).to_json(), 'fsrs_internal_card_id': n, 'version': 1}
        for d, n in ((DEF, 100), (def2, 101))}
    monkeypatch.setattr(writer, '_lock_states', lambda *_, **__: states)
    monkeypatch.setattr(writer, '_completion_saved', lambda *_: True)

    class Cursor:
        def fetchall(self): return []
    class Database:
        read_only = False
        raw = SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name='INTRANS')))
        def __init__(self, fail=False): self.writes, self.fail = [], fail
        def execute_native(self, sql, params):
            if sql.lstrip().startswith(('INSERT', 'UPDATE')):
                self.writes.append(sql)
                if self.fail and len(self.writes) == 4:
                    raise RuntimeError('late write failed')
            return Cursor()
    kwargs = dict(owner_epoch='epoch', authored_digest='authored', accepted_study_day=DAY,
                  accepted_at=NOW, completion=object())
    db = Database()
    receipts = writer.apply_in_borrowed_transaction(db, 'attempt', ('event-1','event-2'), **kwargs)
    assert [r.grade for r in receipts] == ['good', 'good']
    assert len(db.writes) == 4
    failing = Database(fail=True)
    with pytest.raises(RuntimeError, match='late write failed'):
        writer.apply_in_borrowed_transaction(failing, 'attempt', ('event-1','event-2'), **kwargs)
    assert len(failing.writes) == 4  # caller must roll back the borrowed transaction
