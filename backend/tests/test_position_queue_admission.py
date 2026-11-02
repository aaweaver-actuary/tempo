from types import SimpleNamespace as NS

from tempo_evidence_math.position_learning import Hold
from app.services.position_queue_admission import select_carrier


def binding(card, *, repertoire='r', response='e2e4', available=True):
    return NS(repertoire_id=repertoire, position_key='position', selected_response_uci=response,
              presentation_id=card, presentation_revision=1, decision_id=card,
              available=available, introduction='introduced', introduction_matches_current_content=True)


def source(bindings):
    key = NS(repertoire_id='r', position='position')
    target = NS(key=key, expected_uci='e2e4')
    return NS(authored_token=NS(repertoire_id='r'),
              snapshot=NS(decision_presentations=bindings),
              routes=[NS(decisions=[NS(definition=target)])]), target


def test_same_repertoire_aliases_choose_one_deterministic_carrier():
    prepared, target = source([binding('b'), binding('a'), binding('x', repertoire='other')])
    assert select_carrier(prepared, target).presentation_id == 'a'


def test_conflicting_response_or_unavailable_carrier_holds():
    prepared, target = source([binding('a', response='d2d4')])
    assert isinstance(select_carrier(prepared, target), Hold)
    prepared, target = source([binding('a', available=False)])
    assert isinstance(select_carrier(prepared, target), Hold)


def test_refresh_consumes_prepared_due_before_advancing(monkeypatch):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as refresh
    from app.services import position_queue_admission as position
    from app.services import study_mode

    @contextmanager
    def borrowed(*args, **kwargs):
        yield object()

    calls = []
    prepared = {'cursor': {'position_repertoire': 'r', 'position_after': ['t', 'standard', 'p']}}
    monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)
    monkeypatch.setattr(refresh, 'background_read_connection', borrowed)
    monkeypatch.setattr(refresh, 'connection', borrowed)
    monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *a, **k: (None, 0))
    monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    monkeypatch.setattr(refresh, 'lock_current_slice', lambda *a: True)
    monkeypatch.setattr(position, 'prepare_due', lambda *a: calls.append('prepare') or prepared)
    monkeypatch.setattr(position, 'admit_due', lambda *a: calls.append('admit'))
    monkeypatch.setattr(refresh, 'advance_task_slice_in_transaction',
                        lambda db, task, **kw: calls.append(kw) or True)
    assert refresh.execute_postgres_queue_refresh_slice({'payload': {
        'queue_date': '2026-11-02', '_queue_phase': 'admit_position_due'}})
    assert calls[:2] == ['prepare', 'admit']
    assert calls[2]['next_phase'] == 'admit_position_due'
    assert calls[2]['next_payload']['position_repertoire'] == 'r'


def test_admission_fences_before_card_and_date_and_deduplicates(monkeypatch):
    from datetime import date, datetime, timezone
    from app.services.position_queue_admission import admit_due
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters.study import position_owner_epoch, position_due_candidates
    from tempo_legacy_database.adapters.queue_refresh import admissions
    from tempo_legacy_database.adapters import queue_position_lock

    order = []
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    prepared, target = source([binding('a')])
    prepared.authored_token.authored_digest = 'digest'
    state = NS(definition=target, owner_epoch='epoch', version=1)
    candidate = NS(state=state, witness=NS(issued_at=now))
    proposal = {'source': prepared, 'candidate': candidate, 'carrier': prepared.snapshot.decision_presentations[0]}
    monkeypatch.setattr(position_study_source, 'fence_position_source', lambda *a: order.append('source'))
    monkeypatch.setattr(position_owner_epoch, 'owner_mode', lambda *a: order.append('owner') or NS(disposition='position', epoch='epoch'))
    class Reader:
        def __init__(self, *a, **kw): self.states = self
        def lock_current(self, *a): order.append('state'); return state
        def read_context(self, *a): return (NS(due=True),)
    monkeypatch.setattr(position_due_candidates, 'PositionDueCandidateReader', Reader)
    monkeypatch.setattr(admissions, 'locked_card', lambda *a: order.append('card') or True)
    monkeypatch.setattr(queue_position_lock, 'lock_queue_date_for_position', lambda *a: order.append('day'))
    monkeypatch.setattr(admissions, 'position_carrier_eligible', lambda *a: {'occupied': True})
    monkeypatch.setattr(admissions, 'append_position_due', lambda *a: order.append('append'))
    assert admit_due(object(), date(2026, 11, 2).isoformat(), proposal) is None
    assert order == ['source', 'owner', 'state', 'card', 'day']


def test_native_refresh_admits_due_carrier_and_reuses_pending(monkeypatch):
    """Synthetic schema with real source, H1 and sliced queue admission."""
    import os
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from pathlib import Path
    from uuid import uuid4
    import pytest
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned TEMPO_POSITION_TEST_DSN')
    import psycopg
    from psycopg import sql
    from tempo_evidence_math.position_learning import PositionKey, PositionDefinition
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from app.services import postgres_queue_refresh as refresh, study_mode
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -'
    definition = PositionDefinition(PositionKey('r', fen), 'e2e4', 'p1')
    import chess
    board = chess.Board()
    board.push_uci('e2e4')
    board.push_uci('e7e5')
    second_fen = ' '.join(board.fen().split()[:4])
    second_definition = PositionDefinition(PositionKey('r', second_fen), 'g1f3', 'p1')
    schema = 'position_h2_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True, row_factory=tempo_row_factory) as raw:
        assert raw.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        raw.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            raw.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            from test_product_snapshot_postgres import DDL
            import json
            raw.execute(DDL)
            raw.execute("ALTER TABLE cards ADD COLUMN fsrs_card_json text")
            raw.execute("""CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY);
                CREATE TABLE repertoire_integrity_state(repertoire_id text PRIMARY KEY,
                    status text,scan_status text,scan_generation text,checked_at text,
                    scan_completed_sources bigint,scan_total_sources bigint,scan_error text);
                CREATE TABLE background_tasks(id text,kind text,deduplication_key text,
                    generation bigint,state text,payload_json text);
                CREATE TABLE daily_queue(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                  queue_date text,card_id text,cycle bigint,position bigint,card_bucket text,
                  admission_kind text,admission_repertoire_id text,status text DEFAULT 'queued',
                  UNIQUE(queue_date,card_id,cycle));
                CREATE TABLE queue_attempt_origins(queue_entry_id bigint,card_id text,issued_as_head int)""")
            ddl = Path(__file__).resolve().parents[2] / 'database/tempo_legacy_database/ddl/migrations/057_position_study.sql'
            raw.execute(ddl.read_text())
            moves = json.dumps(['e2e4','e7e5','g1f3'])
            raw.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
            raw.execute("INSERT INTO repertoires VALUES('r','Synthetic',7,0,'[]')")
            raw.execute("INSERT INTO opening_graph_publications VALUES('r',3,'ready')")
            raw.execute("INSERT INTO repertoire_lines VALUES('route','r','Route','white',%s,%s)", (chess.STARTING_FEN,moves))
            raw.execute("INSERT INTO cards VALUES('card','r',1,%s,%s,'white','opening',0,0,NULL,1,%s,NULL)",
                        (chess.STARTING_FEN,moves,now.isoformat()))
            raw.execute("INSERT INTO repertoire_cards VALUES('r','card',1)")
            raw.execute("INSERT INTO opening_graph_steps VALUES('r',3,'route',0,'card',%s,%s,'white')", (chess.STARTING_FEN,moves))
            raw.execute("INSERT INTO repertoire_integrity_state VALUES('r','clean','idle','scan:2','2026-10-26T12:00:00Z',1,1,NULL)")
            raw.execute("INSERT INTO background_tasks VALUES('graph','opening_graph_rebuild','r',3,'complete','{}'),('scan','integrity_scan','r',2,'complete',%s)", (json.dumps(dict(repertoire_id='r', graph_generation=3)),))
            class Port:
                read_only = False
                in_transaction = True
                def __init__(self, raw): self.raw = raw
                def execute_native(self, query, params=()): return self.raw.execute(query, params)
            port = Port(raw)
            prepared = position_study_source.prepare_position_source(port, 'r')
            assert not isinstance(prepared, Hold)
            digest = prepared.authored_token.authored_digest
            raw.execute("INSERT INTO study_position_epochs VALUES('r','e1',%s,%s)", (digest, now))
            raw.execute("INSERT INTO study_position_owners VALUES('r','e1','active',%s)", (now,))
            for internal_id, position, answer in [(1, fen, 'e2e4'), (2, second_fen, 'g1f3')]:
                raw.execute("""INSERT INTO study_position_states
                    (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
                     fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                    VALUES('r','standard',%s,%s,%s,'e1',%s,%s,'{}',%s,%s)""",
                    (position, answer, next(d.definition.policy_version for r in prepared.routes for d in r.decisions if d.definition.key.position == position), digest, internal_id, now-timedelta(days=1), now-timedelta(days=1)))
            @contextmanager
            def borrowed(*args, **kwargs): yield port
            monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)
            monkeypatch.setattr(refresh, 'background_read_connection', borrowed)
            monkeypatch.setattr(refresh, 'connection', borrowed)
            monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *a, **k: (None, 0))
            monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
            monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
            monkeypatch.setattr(refresh, 'lock_current_slice', lambda *a: True)
            saved = []
            monkeypatch.setattr(refresh, 'advance_task_slice_in_transaction', lambda db, task, **kw: saved.append(kw) or True)
            raw.execute('BEGIN')
            assert refresh.execute_postgres_queue_refresh_slice({'payload': {'queue_date': day, '_queue_phase': 'admit_position_due'}})
            raw.execute('COMMIT')
            assert [tuple(r) for r in raw.execute('SELECT card_id,cycle,admission_repertoire_id FROM daily_queue').fetchall()] == [('card', 0, 'r')]
            cursor_payload = saved[-1]['next_payload']
            raw.execute('BEGIN')
            assert refresh.execute_postgres_queue_refresh_slice({'payload': cursor_payload})
            raw.execute('COMMIT')
            assert raw.execute('SELECT count(*) FROM daily_queue').fetchone()[0] == 1
            raw.execute("UPDATE daily_queue SET status='complete'")
            raw.execute('BEGIN')
            assert refresh.execute_postgres_queue_refresh_slice({'payload': {'queue_date': day, '_queue_phase': 'admit_position_due'}})
            raw.execute('COMMIT')
            assert [r[0] for r in raw.execute('SELECT cycle FROM daily_queue ORDER BY cycle').fetchall()] == [0, 1]
        finally:
            raw.execute('ROLLBACK')
            raw.execute('SET search_path TO public')
            raw.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
