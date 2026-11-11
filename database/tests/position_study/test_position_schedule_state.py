"""Bounded fake query checks; native locking proof is explicitly opt-in."""
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tempo_evidence_math.position_learning import Hold, PositionKey
from tempo_storage.study.position_schedule_state import PositionScheduleStateReader

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -'
KEY = PositionKey('r', FEN)


def state_row(**changes):
    return dict(repertoire_id='r', variant='standard', position=FEN, expected_uci='e2e4',
        policy_version='p1', owner_epoch='e1', source_digest='source', version=7,
        fsrs_internal_card_id=123, fsrs_json=' { "exact": "donor bytes" } ', due_at=NOW,
        introduced_at=NOW, donor_card_id='donor', donor_card_revision=9,
        donor_fingerprint='fingerprint', donor_selection_policy='deterministic-v1',
        current_owner_epoch='e1', current_owner_mode='active', current_source_digest='source') | changes


class Result:
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Database:
    in_transaction = True
    read_only = False

    def __init__(self, rows=None):
        self.owner = dict(epoch='e1', mode='active', source_digest='source')
        self.rows = [state_row()] if rows is None else rows
        self.calls = []
        self.credited = []

    def execute_native(self, sql, params=()):
        self.calls.append((sql, params))
        if 'FROM study_position_owners owner' in sql:
            return Result(row=self.owner)
        if 'SELECT good.variant,good.position' in sql:
            return Result(rows=self.credited)
        return Result(rows=self.rows)


def reader(db):
    return PositionScheduleStateReader(db, repertoire_id='r', owner_epoch='e1', source_digest='source')


def test_exact_state_and_donor_bytes_preserved_without_card_or_scheduler_read():
    db = Database()
    state = reader(db).read_current(KEY)
    assert state.definition.key == KEY
    assert state.version == state.revision == 7
    assert state.fsrs_json == state_row()['fsrs_json']
    assert state.fsrs_internal_card_id == 123
    assert (state.donor_card_id, state.donor_card_revision) == ('donor', 9)
    assert state.due_at == NOW
    assert len(db.calls) == 2
    assert all(' cards ' not in q and 'UPDATE ' not in q for q, _ in db.calls)


@pytest.mark.parametrize('owner,reason', [
    (None, 'position_owner_missing'),
    (dict(epoch='e1', mode='inactive', source_digest='source'), 'position_owner_inactive'),
    (dict(epoch='old', mode='active', source_digest='source'), 'position_owner_epoch_changed'),
    (dict(epoch='e1', mode='active', source_digest='stale'), 'position_owner_source_changed'),
])
def test_owner_missing_inactive_stale_epoch_or_source_holds(owner, reason):
    db = Database()
    db.owner = owner
    assert reader(db).read_current(KEY).reason == reason
    assert len(db.calls) == 1


@pytest.mark.parametrize('changes,reason', [
    ({'owner_epoch': 'old'}, 'position_state_epoch_changed'),
    ({'source_digest': 'old'}, 'position_state_source_changed'),
    ({'current_owner_epoch': 'new'}, 'position_owner_epoch_changed'),
    ({'current_owner_mode': 'paused'}, 'position_owner_inactive'),
    ({'current_source_digest': 'new'}, 'position_owner_source_changed'),
    ({'due_at': NOW.replace(tzinfo=None)}, 'position_state_invalid'),
])
def test_state_fence_rechecks_definition_epoch_and_owner_at_state_snapshot(changes, reason):
    assert reader(Database([state_row(**changes)])).read_current(KEY).reason == reason


def test_missing_state_no_fallback_and_sorted_batch_lock_without_n_plus_one():
    db = Database([])
    assert reader(db).read_current(KEY).reason == 'position_state_missing'
    db = Database()
    states = reader(db).read_many((KEY,) * 100, lock=True)
    assert len(states) == 100 and states[0] is states[-1]
    assert len(db.calls) == 2
    assert 'FOR SHARE OF owner,epoch SKIP LOCKED' in db.calls[0][0]
    assert 'ORDER BY s.variant,s.position LIMIT 100 FOR UPDATE OF s SKIP LOCKED' in db.calls[1][0]
    assert db.calls[1][1] == (['standard'], [FEN], 'r')
    assert reader(Database([])).lock_current(KEY).reason == 'position_state_busy_or_missing'


def test_transaction_and_repertoire_bounds_checked_before_sql():
    db = Database()
    db.in_transaction = False
    assert reader(db).lock_current(KEY).reason == 'borrowed_native_transaction_required'
    assert db.calls == []
    db.in_transaction, db.read_only = True, True
    assert reader(db).lock_current(KEY).reason == 'borrowed_owner_writer_transaction_required'
    assert reader(db).read_current(replace(KEY, repertoire_id='other')).reason == 'position_repertoire_mismatch'
    with pytest.raises(ValueError, match='exceeds_100'):
        reader(db).read_many((KEY,) * 101)
    assert reader(db).read_many(()) == ()
    assert db.calls == []


def test_native_position_lock_borrowing_and_repertoire_isolation():
    """Real PostgreSQL only: scoped schema, two writers, rollback and lock release."""
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    class Native:
        read_only = False
        def __init__(self, connection): self.connection = connection
        @property
        def in_transaction(self): return self.connection.info.transaction_status.name == 'INTRANS'
        def execute_native(self, query, params=()): return self.connection.execute(query, params)

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as setup:
        assert setup.execute('SELECT current_database() db').fetchone()['db'].startswith('tempo_position_test_')
        schema = 'position_d2_' + uuid4().hex
        setup.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            setup.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            setup.execute('CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY)')
            setup.execute('CREATE TABLE cards(id text PRIMARY KEY,fsrs_card_json text)')
            ddl = Path(__file__).resolve().parents[3] / 'database/tempo_storage/ddl/migrations/057_position_study.sql'
            setup.execute(ddl.read_text())
            setup.execute("INSERT INTO study_position_epochs VALUES ('r','e1','source',%s),('other','e1','source',%s)", (NOW, NOW))
            setup.execute("INSERT INTO study_position_owners VALUES ('r','e1','active',%s),('other','e1','active',%s)", (NOW, NOW))
            for rep, card in [('r', 123), ('other', 124)]:
                setup.execute('''INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     version,fsrs_internal_card_id,fsrs_json,due_at)
                    VALUES (%s,'standard',%s,'e2e4','p1','e1','source',7,%s,%s,%s)''',
                    (rep, FEN, card, state_row()['fsrs_json'], NOW))
            with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as first, psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as second:
                for conn in (first, second):
                    conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                    conn.execute('BEGIN')
                a = reader(Native(first))
                b = reader(Native(second))
                assert a.lock_current(KEY).fsrs_json == state_row()['fsrs_json']
                assert b.lock_current(KEY).reason == 'position_state_busy_or_missing'
                other = PositionScheduleStateReader(Native(second), repertoire_id='other', owner_epoch='e1', source_digest='source')
                assert other.lock_current(replace(KEY, repertoire_id='other')).fsrs_internal_card_id == 124
                first.rollback()
                assert not Native(first).in_transaction
                assert b.lock_current(KEY).version == 7
                second.rollback()
            assert setup.execute('SELECT count(*) n FROM study_position_states').fetchone()['n'] == 2
        finally:
            setup.execute('SET search_path TO public')
            setup.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
