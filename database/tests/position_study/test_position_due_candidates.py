"""Fake shape checks plus opt-in PostgreSQL due-index/ledger acceptance."""
from dataclasses import replace
from datetime import date, timedelta
from itertools import combinations, islice
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tempo_evidence_math.position_learning import Hold, PositionKey
from tempo_legacy_database.adapters.study.position_due_candidates import PositionDueCandidateReader, PositionDueCursor
from test_position_schedule_state import Database, FEN, KEY, NOW, state_row


def reader(db, **changes):
    return PositionDueCandidateReader(db, **(dict(repertoire_id='r', owner_epoch='e1',
        source_digest='source', issued_at=NOW, study_day=date(2026, 11, 3)) | changes))


def test_due_query_uses_index_order_and_only_current_good_suppresses_target():
    db = Database()
    page = reader(db).read_due_page()
    candidate, = page.candidates
    assert candidate.witness.definition.key == KEY
    assert candidate.witness.due and candidate.witness.position_revision == 7
    assert candidate.witness.issued_at == NOW
    # The owner day is not derived from UTC/offset on issued_at.
    assert candidate.witness.study_day == date(2026, 11, 3)
    assert candidate.reason == 'due' and page.next_cursor is None
    assert len(db.calls) == 2
    sql, params = db.calls[1]
    assert 's.repertoire_id=%s AND s.owner_epoch=%s AND s.due_at<=%s' in sql
    assert 's.introduced_at IS NOT NULL' in sql
    assert 'ORDER BY s.due_at,s.variant,s.position LIMIT %s' in sql
    assert "good.grade='good'" in sql
    assert 'good.owner_epoch=s.owner_epoch AND good.version_after=s.version' in sql
    assert params == ('r', 'e1', NOW, date(2026, 11, 3), 101)
    assert all(word not in sql for word in ('cards', 'opening_graph', 'UPDATE', 'OFFSET'))


def test_keyset_cursor_is_bound_to_repertoire_epoch_source_time_day_and_intro_policy():
    db = Database([state_row(), state_row(position=FEN.replace(' w ', ' b '), expected_uci='e7e5')])
    first = reader(db).read_due_page(limit=1)
    assert len(first.candidates) == 1 and first.next_cursor is not None
    db.rows = db.rows[1:]
    second = reader(db).read_due_page(after=first.next_cursor, limit=1)
    assert second.next_cursor is None
    sql, params = db.calls[-1]
    assert '(s.due_at,s.variant,s.position) > (%s,%s,%s)' in sql
    assert params[-4:] == (NOW, 'standard', FEN, 2)
    for field, value in [('repertoire_id', 'other'), ('owner_epoch', 'e2'),
                         ('source_digest', 'changed'), ('issued_at', NOW + timedelta(seconds=1)),
                         ('study_day', date(2026, 11, 4)), ('include_unintroduced', True)]:
        before = len(db.calls)
        result = reader(db).read_due_page(after=replace(first.next_cursor, **{field: value}))
        assert result.reason == 'position_due_cursor_binding_changed'
        assert len(db.calls) == before


def test_context_witnesses_include_nondue_missing_and_already_good_positions():
    black_fen = FEN.replace(' w ', ' b ')
    black_key = PositionKey('r', black_fen)
    db = Database([state_row(), state_row(position=black_fen, expected_uci='e7e5', due_at=NOW + timedelta(seconds=1))])
    missing = PositionKey('r', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -')
    db.credited = [dict(variant='standard', position=FEN)]
    result = reader(db).read_context((KEY, black_key, missing, KEY))
    assert [getattr(w, 'due', None) for w in result] == [True, False, None, True]
    assert result[2].reason == 'position_state_missing'
    assert result[0] == result[3]
    assert len(db.calls) == 2
    assert all('study_position_grades' not in q for q, _ in db.calls)


def test_unintroduced_is_explicit_opt_in_hint_and_stale_source_holds_page():
    db = Database([state_row(introduced_at=None)])
    page = reader(db).read_due_page(include_unintroduced=True)
    assert page.candidates[0].reason == 'unintroduced'
    assert not page.candidates[0].witness.due
    assert 's.introduced_at IS NOT NULL' not in db.calls[-1][0]
    db.rows = [state_row(source_digest='stale')]
    assert reader(db).read_due_page().reason == 'position_state_source_changed'
    db.owner['mode'] = 'paused'
    assert reader(db).read_due_page().reason == 'position_owner_inactive'


def test_hundred_context_aliases_are_two_queries_and_all_bounds_precede_sql():
    db = Database()
    assert len(reader(db).read_context((KEY,) * 100)) == 100
    assert len(db.calls) == 2
    assert db.calls[1][1][0:2] == (['standard'], [FEN])
    assert reader(db).read_context(()) == ()
    before = len(db.calls)
    with pytest.raises(ValueError, match='exceeds_100'):
        reader(db).read_context((KEY,) * 101)
    for limit in [0, 101, True]:
        with pytest.raises(ValueError, match='limit'):
            reader(db).read_due_page(limit=limit)
    with pytest.raises(ValueError, match='aware'):
        reader(db, issued_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match='study_day'):
        reader(db, study_day=NOW)
    assert len(db.calls) == before


def test_native_due_keyset_pagination_exclusions_context_and_index():
    """Only a task-owned disposable DSN; never creates a server or database."""
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    import chess
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    class Native:
        read_only = False
        def __init__(self, connection): self.connection = connection; self.calls = []
        @property
        def in_transaction(self): return self.connection.info.transaction_status.name == 'INTRANS'
        def execute_native(self, query, params=()):
            self.calls.append((query, params))
            return self.connection.execute(query, params)

    # More than 10k legal, distinct, clock-free positions; no route aliases exist
    # in position state, so presentation multiplicity cannot multiply this read.
    positions = []
    squares = [sq for sq in chess.SQUARES if chess.square_file(sq) != 4 and chess.square_rank(sq) != 7]
    for rooks in islice(combinations(squares, 3), 10000):
        board = chess.Board(None)
        board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
        for square in rooks: board.set_piece_at(square, chess.Piece(chess.ROOK, chess.WHITE))
        assert board.is_valid()
        positions.append(' '.join(board.fen().split()[:4]))
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        assert connection.execute('SELECT current_database() db').fetchone()['db'].startswith('tempo_position_test_')
        schema = 'position_h1_' + uuid4().hex
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            connection.execute('CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY)')
            connection.execute('CREATE TABLE cards(id text PRIMARY KEY,fsrs_card_json text)')
            ddl = Path(__file__).resolve().parents[3] / 'database/tempo_legacy_database/ddl/migrations/057_position_study.sql'
            connection.execute(ddl.read_text())
            connection.execute("INSERT INTO study_position_epochs VALUES ('r','e1','source',%s),('other','e1','source',%s)", (NOW, NOW))
            connection.execute("INSERT INTO study_position_owners VALUES ('r','e1','active',%s),('other','e1','active',%s)", (NOW, NOW))
            with connection.cursor() as cursor:
                cursor.executemany('''INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                    VALUES ('r','standard',%s,'a1a2','p1','e1','source',%s,'{}',%s,%s)''',
                    [(position, i + 1, NOW, NOW) for i, position in enumerate(positions)])
            ordered = sorted(positions)
            connection.execute('UPDATE study_position_states SET introduced_at=NULL WHERE position=%s', (ordered[0],))
            connection.execute('UPDATE study_position_states SET due_at=%s WHERE position=%s', (NOW + timedelta(seconds=1), ordered[1]))
            connection.execute('''INSERT INTO study_position_states
                (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                 fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                VALUES ('other','standard',%s,'a1a2','p1','e1','source',20001,'{}',%s,%s)''', (ordered[2], NOW, NOW))
            # Immutable first-response ancestry for a prior Good on this owner day.
            connection.execute("INSERT INTO study_position_issued_plans VALUES ('a',1,'card',1,'r','standard','e1','source','2026-11-03','d','{}',%s)", (NOW,))
            connection.execute("INSERT INTO study_position_first_responses VALUES ('ev','a',0,'r','standard',%s,'e1','d','{}',%s,%s)", (ordered[2], NOW, NOW))
            connection.execute('''INSERT INTO study_position_grades
                (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,variant,
                 position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,
                 request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES ('g','study','ev','a',0,'response','r','standard',%s,'e1','a1a2','p1','source',
                        '2026-11-03','good','d',%s,'{}','{}',1,2)''', (ordered[2], NOW))
            connection.execute('UPDATE study_position_states SET version=2 WHERE position=%s AND repertoire_id=%s', (ordered[2], 'r'))
            connection.execute('ANALYZE study_position_states')
            connection.execute('BEGIN')
            db = Native(connection)
            due = reader(db)
            seen, cursor = [], None
            while True:
                page = due.read_due_page(after=cursor, limit=100)
                assert not isinstance(page, Hold)
                seen.extend(c.state.definition.key.position for c in page.candidates)
                cursor = page.next_cursor
                if cursor is None: break
            assert seen == ordered[3:]
            assert len(seen) == len(set(seen)) == 9997
            # Real planner uses the repertoire/epoch/due/variant/position index.
            query, params = next((q, p) for q, p in db.calls if 'NOT EXISTS' in q)
            plan = connection.execute('EXPLAIN (FORMAT JSON) ' + query, params).fetchone()['QUERY PLAN']
            assert 'study_position_due' in str(plan)
            context = due.read_context(tuple(PositionKey('r', p) for p in ordered[:3]))
            assert [w.due for w in context] == [False, False, True]
            # A later genuine wrong first response can create a new due
            # obligation despite today's Good. It cannot grant a second Good.
            again_at = NOW + timedelta(minutes=1)
            connection.execute("INSERT INTO study_position_issued_plans VALUES ('a2',2,'card',2,'r','standard','e1','source','2026-11-03','d2','{}',%s)", (again_at,))
            connection.execute("INSERT INTO study_position_first_responses VALUES ('ev2','a2',0,'r','standard',%s,'e1','d2','{}',%s,%s)", (ordered[2], again_at, again_at))
            connection.execute('''INSERT INTO study_position_grades
                (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,variant,
                 position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,grade,
                 request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES ('g2','study','ev2','a2',0,'response','r','standard',%s,'e1','a1a2','p1','source',
                        '2026-11-03','again','d2',%s,'{}','{}',2,3)''', (ordered[2], again_at))
            connection.execute('UPDATE study_position_states SET version=3,due_at=%s WHERE position=%s AND repertoire_id=%s', (again_at, ordered[2], 'r'))
            after_original_due = PositionDueCursor('r', 'e1', 'source', again_at,
                date(2026, 11, 3), False, NOW, 'standard', ordered[-1])
            again_due = reader(db, issued_at=again_at).read_due_page(after=after_original_due, limit=100)
            # The contextual position with due=NOW+1 second also becomes due
            # before this later response. Preserve natural due ordering.
            assert [item.state.definition.key.position for item in again_due.candidates] == ordered[1:3]
            assert all(item.witness.due for item in again_due.candidates)
            assert reader(db, issued_at=again_at).read_context((PositionKey('r', ordered[2]),))[0].due
            tomorrow = reader(db, issued_at=again_at, study_day=date(2026, 11, 4))
            assert tomorrow.read_context((PositionKey('r', ordered[2]),))[0].due
            other = reader(db, repertoire_id='other').read_due_page()
            assert len(other.candidates) == 1
            connection.rollback()
        finally:
            connection.execute('SET search_path TO public')
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
