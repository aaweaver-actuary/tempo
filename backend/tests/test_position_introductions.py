from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from tempo_evidence_math.position_learning import Hold, PositionDefinition, PositionKey
from tempo_legacy_database.adapters.study import position_introductions as intro

FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -'
DEF = PositionDefinition(PositionKey('r', FEN), 'e2e4', 'p1')


class Cursor:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Writer:
    def __init__(self):
        self.calls = []
        self.history = None
        self.saved_attempt = False
        self.outcome = dict(disposition='hold', hold_reason='no_valid_current_introduced_donor', expected_uci='e2e4', policy_version='p1')
    def execute_native(self, sql, params=()):
        self.calls.append((sql, params))
        if 'study_position_seed_runs' in sql:
            return Cursor(dict(status='complete', source_digest='digest',
                source_token_json={'repertoire_id':'r','authored_digest':'digest'},
                selection_policy='earliest_due_then_card_id:v1', manifest_digest='manifest',
                allocator_preflight_json={'complete': True, 'after': None, 'scanned': 3},
                manifest_count=1, seeded_count=0, held_count=1))
        if 'FROM study_position_states' in sql: return Cursor(None)
        if 'study_position_seed_outcomes' in sql: return Cursor(self.outcome)
        if 'pg_try_advisory' in sql: return Cursor((True,))
        if 'FROM cards WHERE' in sql:
            return Cursor(dict(revision=1, introduced_at=self.history, state='new', archived=0,
                               pending_validation=0, superseded_by=None))
        if 'SELECT EXISTS(SELECT 1 FROM reviews' in sql: return Cursor((self.saved_attempt,))
        if 'nextval' in sql: return Cursor((4503599627370496,))
        if 'INSERT INTO study_position_states' in sql: return Cursor(None)
        raise AssertionError(sql)


def prepared(introduction='unintroduced'):
    source = NS(authored_token=NS(repertoire_id='r', authored_digest='digest'))
    binding = NS(position_key=FEN, selected_response_uci='e2e4', introduction=introduction,
                 presentation_id='card', presentation_revision=1)
    return intro.PreparedIntroduction(intro._INTRO_SEAL, source, 'card', (DEF,), (binding,), 'manifest', 1)


@pytest.fixture
def fences(monkeypatch):
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters.study import position_owner_epoch, position_fsrs_namespace
    monkeypatch.setattr(position_study_source, 'fence_position_source', lambda *a: None)
    monkeypatch.setattr(position_owner_epoch, 'owner_mode', lambda *a: NS(disposition='position', epoch='e1'))
    monkeypatch.setattr(position_fsrs_namespace, 'fence_namespace', lambda *a: None)


def test_only_authorized_unseen_position_gets_fresh_state_without_history(fences):
    db, proposal = Writer(), prepared()
    fenced = intro.fence_introduction(db, proposal)
    assert isinstance(fenced, intro.IntroductionFence)
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    intro.initialize_introduction(db, proposal, fenced, now=now)
    statement, params = db.calls[-1]
    assert 'INSERT INTO study_position_states' in statement
    assert params[7] == 4503599627370496 and params[-2:] == (now, now)
    from fsrs import Card
    card = Card.from_json(params[8])
    assert card.card_id == params[7] and card.last_review is None and card.due == now
    assert not any('INSERT INTO reviews' in sql or 'INSERT INTO study_position_grades' in sql for sql, _ in db.calls)


def test_unseen_proof_fails_closed_on_ambiguous_or_changed_history(fences):
    assert intro.fence_introduction(Writer(), prepared('unknown')).reason == 'position_introduction_history_ambiguous'
    db = Writer(); db.history = '2026-10-31'
    assert intro.fence_introduction(db, prepared()).reason == 'position_introduction_history_changed'
    db = Writer(); db.saved_attempt = True
    assert intro.fence_introduction(db, prepared()).reason == 'position_introduction_history_changed'
    db = Writer(); db.outcome['hold_reason'] = 'donor_currentness_unavailable'
    assert intro.fence_introduction(db, prepared()).reason == 'position_introduction_seed_outcome_unavailable'
    with pytest.raises(ValueError, match='fence_required'):
        intro.initialize_introduction(Writer(), prepared(), ('e1', (DEF,)), now=datetime.now(timezone.utc))


def test_native_authorized_introduction_writes_fresh_state_only_after_admission(monkeypatch):
    """Real source/owner/seed/FSRS storage; legacy admission and F1 guard isolated."""
    import json
    import os
    from contextlib import contextmanager
    from pathlib import Path
    from uuid import uuid4
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned TEMPO_POSITION_TEST_DSN')
    import chess
    import psycopg
    from psycopg import sql
    from test_product_snapshot_postgres import DDL
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters.study import position_fsrs_namespace
    from app.services import postgres_queue_refresh as refresh, study_mode, queue_next_card
    schema = 'position_h2_intro_' + uuid4().hex
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    with psycopg.connect(dsn, autocommit=True, row_factory=tempo_row_factory) as raw:
        assert raw.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        raw.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            raw.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            raw.execute(DDL)
            raw.execute('ALTER TABLE cards ADD COLUMN fsrs_card_json text, ADD COLUMN state text')
            raw.execute('''CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY);
                CREATE TABLE repertoire_integrity_state(repertoire_id text PRIMARY KEY,
                    status text,scan_status text,scan_generation text,checked_at text,
                    scan_completed_sources bigint,scan_total_sources bigint,scan_error text);
                CREATE TABLE background_tasks(id text,kind text,deduplication_key text,
                    generation bigint,state text,payload_json text);
                CREATE TABLE daily_queue(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    queue_date text,card_id text,status text DEFAULT 'queued');
                CREATE TABLE reviews(card_id text);
                CREATE TABLE opening_evidence_attempts(card_id text);
                CREATE TABLE queue_attempt_origins(card_id text,issued_as_head int,attempt_failed int,
                    review_result_json text,last_status text);''')
            ddl = Path(__file__).resolve().parents[2] / 'database/tempo_legacy_database/ddl/migrations/057_position_study.sql'
            raw.execute(ddl.read_text())
            moves = json.dumps(['e2e4'])
            raw.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
            raw.execute("INSERT INTO repertoires VALUES('r','Synthetic',7,0,'[]')")
            raw.execute("INSERT INTO opening_graph_publications VALUES('r',3,'ready')")
            raw.execute("INSERT INTO repertoire_lines VALUES('route','r','Route','white',%s,%s)", (chess.STARTING_FEN,moves))
            raw.execute("INSERT INTO cards VALUES('card','r',1,%s,%s,'white','opening',0,0,NULL,1,NULL,NULL,'new')", (chess.STARTING_FEN,moves))
            raw.execute("INSERT INTO repertoire_cards VALUES('r','card',1)")
            raw.execute("INSERT INTO opening_graph_steps VALUES('r',3,'route',0,'card',%s,%s,'white')", (chess.STARTING_FEN,moves))
            raw.execute("INSERT INTO repertoire_integrity_state VALUES('r','clean','idle','scan:2','2026-10-26T12:00:00Z',1,1,NULL)")
            raw.execute("INSERT INTO background_tasks VALUES('graph','opening_graph_rebuild','r',3,'complete','{}'),('scan','integrity_scan','r',2,'complete',%s)", (json.dumps(dict(repertoire_id='r',graph_generation=3)),))
            class Port:
                read_only = False
                in_transaction = True
                def __init__(self, raw): self.raw = raw
                def execute_native(self, query, params=()): return self.raw.execute(query, params)
            port = Port(raw)
            source = position_study_source.prepare_position_source(port, 'r')
            assert not isinstance(source, Hold)
            definition = source.routes[0].decisions[0].definition
            digest = source.authored_token.authored_digest
            source_intro = intro.prepare_introduction(source, 'card')
            assert not isinstance(source_intro, Hold)
            raw.execute("INSERT INTO study_position_epochs VALUES('r','e1',%s,%s)", (digest,now))
            raw.execute("INSERT INTO study_position_owners VALUES('r','e1','active',%s)", (now,))
            raw.execute('''INSERT INTO study_position_seed_runs
                (repertoire_id,owner_epoch,source_digest,source_token_json,selection_policy,manifest_digest,
                 manifest_count,allocator_preflight_json,seeded_count,held_count,status,created_at,completed_at)
                VALUES('r','e1',%s,%s::jsonb,'earliest_due_then_card_id:v1',%s,1,%s::jsonb,0,1,'complete',%s,%s)''',
                (digest,json.dumps(source.authored_token.as_dict()),source_intro.manifest_digest,json.dumps({'complete':True,'after':None,'scanned':1}),now,now))
            raw.execute('''INSERT INTO study_position_seed_outcomes
                (repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,hold_reason)
                VALUES('r','e1','standard',%s,%s,%s,'hold','no_valid_current_introduced_donor')''',
                (definition.key.position,definition.expected_uci,definition.policy_version))
            @contextmanager
            def borrowed(*args, **kwargs): yield port
            monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)
            monkeypatch.setattr(refresh, 'background_read_connection', borrowed)
            monkeypatch.setattr(refresh, 'connection', borrowed)
            monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *a, **k: (None,0))
            monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
            monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
            monkeypatch.setattr(refresh, 'lock_current_slice', lambda *a: True)
            monkeypatch.setattr(refresh, 'advance_task_slice_in_transaction', lambda *a, **kw: True)
            monkeypatch.setattr(position_fsrs_namespace, 'fence_namespace', lambda db: None)
            queue_next_card._source_cache.clear()
            planned = {'repertoire_id':'r','card_id':'card','reason':None}
            task = {'payload': {'queue_date':day,'_queue_phase':'study_opening_item',
                'study_opening_items':[planned],'study_opening_index':0,'study_opening_more':False,
                'study_opening_counts':{}}}
            monkeypatch.setattr(refresh, '_admit_one_prioritized_opening', lambda *a: False)
            raw.execute('BEGIN')
            assert refresh.execute_postgres_queue_refresh_slice(task)
            raw.execute('COMMIT')
            assert raw.execute('SELECT count(*) FROM study_position_states').fetchone()[0] == 0
            def admit(*args):
                raw.execute("UPDATE cards SET introduced_at=%s,state='learning' WHERE id='card'", (day,))
                raw.execute("INSERT INTO daily_queue(queue_date,card_id) VALUES(%s,'card')", (day,))
                return True
            monkeypatch.setattr(refresh, '_admit_one_prioritized_opening', admit)
            raw.execute('BEGIN')
            assert refresh.execute_postgres_queue_refresh_slice(task)
            raw.execute('COMMIT')
            state = raw.execute('SELECT * FROM study_position_states').fetchone()
            from fsrs import Card
            native = Card.from_json(state['fsrs_json'])
            assert state['position'] == definition.key.position and state['introduced_at'] is not None
            assert native.card_id == state['fsrs_internal_card_id'] and native.last_review is None
            assert raw.execute('SELECT count(*) FROM study_position_grades').fetchone()[0] == 0
            assert raw.execute("SELECT disposition FROM study_position_seed_outcomes").fetchone()[0] == 'hold'
        finally:
            queue_next_card._source_cache.clear()
            raw.execute('ROLLBACK')
            raw.execute('SET search_path TO public')
            raw.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_all_covered_ordinary_introduction_is_skipped_without_completion(monkeypatch):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as refresh, position_queue_admission as admission, study_mode
    calls = []
    @contextmanager
    def borrowed(*args, **kwargs): yield object()
    monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)
    monkeypatch.setattr(refresh, 'background_read_connection', borrowed)
    monkeypatch.setattr(refresh, 'connection', borrowed)
    monkeypatch.setattr(refresh.frozen_opening_order, 'activation', lambda *a, **k: (None, 0))
    monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda: False)
    monkeypatch.setattr(refresh, 'lock_current_slice', lambda *a: True)
    monkeypatch.setattr(admission, 'prepare_opening_introduction', lambda *a: ('position', object()))
    monkeypatch.setattr(admission, 'fence_opening_introduction', lambda *a: NS(definitions=()))
    monkeypatch.setattr(refresh, '_admit_one_prioritized_opening', lambda *a: calls.append('admit'))
    monkeypatch.setattr(admission, 'apply_opening_introduction', lambda *a, **kw: calls.append('initialize'))
    monkeypatch.setattr(refresh, 'advance_task_slice_in_transaction', lambda *a, **kw: calls.append('advance') or True)
    task = {'payload': {'queue_date': '2026-11-02', '_queue_phase': 'study_opening_item',
        'study_opening_items': [{'repertoire_id': 'r', 'card_id': 'c', 'reason': None}],
        'study_opening_index': 0, 'study_opening_more': False, 'study_opening_counts': {}}}
    assert refresh.execute_postgres_queue_refresh_slice(task)
    assert calls == ['advance']


def test_targeted_game_intro_uses_exact_source_occurrence_and_real_admission(monkeypatch):
    from app.services import position_queue_admission as admission
    target = NS(decision_id='d', repertoire_id='r', position_key=FEN,
        selected_response_uci='e2e4', presentation_id='card', presentation_revision=1,
        route_id='route', route_ply=0, available=True)
    source = NS(snapshot=NS(decision_presentations=[target]),
                routes=[NS(decisions=[NS(definition=DEF)])])
    proposal = object()
    calls = []
    monkeypatch.setattr(intro, 'prepare_introduction', lambda *a, **kw:
                        calls.append(('prepare', kw['target'])) or proposal)
    prepared = admission.prepare_targeted_position_introduction(source, target)
    assert prepared == (proposal, target) and calls == [('prepare', DEF)]
    monkeypatch.setattr(intro, 'initialize_introduction', lambda *a, **kw: calls.append(('initialize', a[1])))
    class Canonical:
        def admit_encounter(self, day, **kw):
            calls.append(('admit', day, kw))
            return NS(status='introduced')
    fence = NS(prepared=proposal, definitions=(DEF,))
    class Database:
        def execute_native(self, *a): return Cursor((1,))
    result = admission.admit_targeted_position_introduction(Database(), prepared, fence,
        day='2026-11-02', now=datetime.now(timezone.utc), introductions=Canonical())
    assert result.status == 'introduced'
    assert [call[0] for call in calls] == ['prepare', 'admit', 'initialize']
    assert calls[1][2]['target'] is target


def test_targeted_game_intro_does_not_initialize_on_rejected_admission(monkeypatch):
    from app.services import position_queue_admission as admission
    proposal, target = object(), NS(repertoire_id='r', decision_id='d')
    monkeypatch.setattr(intro, 'initialize_introduction', lambda *a, **kw: pytest.fail('No authorized introduction'))
    class Rejected:
        def admit_encounter(self, *a, **kw): return NS(status='deferred', reason='paused')
    result = admission.admit_targeted_position_introduction(object(), (proposal, target),
        NS(prepared=proposal, definitions=(DEF,)), day='2026-11-02',
        now=datetime.now(timezone.utc), introductions=Rejected())
    assert result.status == 'deferred'
