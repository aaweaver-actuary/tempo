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


def test_native_two_decisions_and_late_failure_rollback():
    """Opt-in native proof; requires a task-owned disposable PostgreSQL database."""
    import json
    import os
    from dataclasses import replace
    from pathlib import Path
    from uuid import uuid4
    import pytest

    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from app.services.postgres_opening_evidence import position_first_response_envelope, _position_digest
    from app.study.position_learning.issued_plan_contracts import select_and_freeze
    from tests.test_position_issued_plan_contracts import option, request_for
    from tempo_legacy_database.adapters.study.position_grade_effects import (
        CompletionAuthority, apply_in_borrowed_transaction,
    )

    route = option('italian-knight-attack')
    request = request_for(route)
    token = {'original_snapshot_id': route.certificate.snapshot_id,
             'original_source_revision': route.certificate.source_revision,
             'original_source_digest': route.certificate.source_digest,
             'repertoire_id': request.target.key.repertoire_id,
             'authored_digest': 'authored', 'header': {'id': request.target.key.repertoire_id},
             'integrity': {'status': 'complete'}}
    plan = select_and_freeze(replace(request, authored_source_token=token))
    assert not isinstance(plan, Hold) and len(plan.decision_slots) >= 2
    now = plan.issued_at
    schema = 'position_d3_' + uuid4().hex
    migration = (Path(__file__).resolve().parents[2] /
                 'tempo_legacy_database/ddl/migrations/057_position_study.sql').read_text()

    class Borrowed:
        read_only = False
        def __init__(self, raw, fail=False):
            self.raw, self.fail, self.inserts = raw, fail, 0
        def execute_native(self, statement, parameters=()):
            if statement.lstrip().startswith('INSERT INTO study_position_grades'):
                self.inserts += 1
                if self.fail and self.inserts == 2:
                    raise RuntimeError('injected late failure')
            return self.raw.execute(statement, parameters)

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        name = conn.execute('SELECT current_database() AS name').fetchone()['name']
        assert name.startswith('tempo_position_test_'), 'refusing non-task-owned database'
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            conn.execute('CREATE TABLE tempo_schema_migrations (version integer PRIMARY KEY)')
            conn.execute(migration)
            conn.execute('CREATE TABLE legacy_sentinel (value text NOT NULL)')
            conn.execute("INSERT INTO legacy_sentinel VALUES ('unchanged')")
            conn.execute('CREATE TABLE opening_evidence_attempts (attempt_id text, queue_entry_id bigint, card_id text, card_revision bigint, state text, review_id bigint)')
            conn.execute('CREATE TABLE review_attempt_receipts (attempt_id text, queue_entry_id bigint, card_id text, review_id bigint, scheduling_status text)')
            conn.execute('CREATE TABLE reviews (id bigint, card_id text, source_kind text, source_ref text, invalidated_at text)')
            rep, epoch = plan.target.key.repertoire_id, plan.owner_epoch
            conn.execute('INSERT INTO study_position_epochs VALUES (%s,%s,%s,%s)', (rep, epoch, 'authored', now))
            conn.execute("INSERT INTO study_position_owners VALUES (%s,%s,'active',%s)", (rep, epoch, now))
            for index, slot in enumerate(plan.decision_slots):
                d = slot.definition
                card_id = 300 + index
                conn.execute('''INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     version,fsrs_internal_card_id,fsrs_json,due_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,7,%s,%s,%s)''',
                    (rep,d.key.variant,d.key.position,d.expected_uci,d.policy_version,epoch,'authored',
                     card_id,Card(card_id=card_id,due=now).to_json(),now))
            conn.execute('''INSERT INTO study_position_issued_plans VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (plan.attempt_id,plan.queue_entry_id,plan.original_card_id,plan.original_card_revision,
                 rep,'standard',epoch,'authored',plan.local_day,plan.plan_id,Jsonb(plan.to_record()),now))
            for index, slot in enumerate(plan.decision_slots[:2]):
                event = position_first_response_envelope(plan, event_id='event-'+str(index),
                    decision_index=index, actual_uci=slot.definition.expected_uci, manual_failure=False,
                    assistance_before=False, observed_at=now.isoformat(), classify_alternative=lambda *_: 'wrong')
                conn.execute('''INSERT INTO study_position_first_responses VALUES
                    (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (event['event_id'],plan.attempt_id,index,rep,'standard',slot.definition.key.position,
                     epoch,_position_digest(event),Jsonb(event),now,now))
            conn.execute('INSERT INTO opening_evidence_attempts VALUES (%s,%s,%s,%s,%s,%s)',
                         (plan.attempt_id,plan.queue_entry_id,plan.original_card_id,plan.original_card_revision,'complete',10))
            conn.execute('INSERT INTO review_attempt_receipts VALUES (%s,%s,%s,%s,%s)',
                         (plan.attempt_id,plan.queue_entry_id,plan.original_card_id,10,'canonical'))
            conn.execute('INSERT INTO reviews VALUES (%s,%s,%s,%s,NULL)',
                         (10,plan.original_card_id,'position_study','attempt:'+plan.attempt_id))
            authority = CompletionAuthority(plan.attempt_id,plan.queue_entry_id,plan.original_card_id,
                                             plan.original_card_revision,10)
            args = dict(owner_epoch=epoch,authored_digest='authored',accepted_study_day=plan.local_day,
                        accepted_at=now,completion=authority)
            with pytest.raises(RuntimeError, match='injected late failure'):
                with conn.transaction():
                    apply_in_borrowed_transaction(Borrowed(conn,True),plan.attempt_id,('event-0','event-1'),**args)
            assert conn.execute('SELECT count(*) AS n FROM study_position_grades').fetchone()['n'] == 0
            assert all(r['version'] == 7 for r in conn.execute('SELECT version FROM study_position_states').fetchall())
            with conn.transaction():
                result = apply_in_borrowed_transaction(Borrowed(conn),plan.attempt_id,('event-0','event-1'),**args)
            assert [r.grade for r in result] == ['good','good']
            assert conn.execute('SELECT value FROM legacy_sentinel').fetchone()['value'] == 'unchanged'
        finally:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
