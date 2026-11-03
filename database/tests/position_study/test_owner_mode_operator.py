"""Safety checks for the preliminary owner operator."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tempo_legacy_database.adapters.study import position_owner_operator as operator
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class DB:
    def __init__(self, existing=None):
        self.existing = existing
        self.statements = []

    def execute_native(self, sql, params=()):
        self.statements.append((sql, params))
        if 'FROM operation_receipts WHERE' in sql:
            return Cursor(self.existing)
        if 'FROM study_position_owners o' in sql:
            return Cursor({'epoch': 'epoch', 'mode': 'active', 'source_digest': 'source'})
        return Cursor()


@pytest.fixture
def prepared(monkeypatch):
    monkeypatch.setattr(operator, '_lock', lambda db, prepared, **kwargs: 'rep')
    return SimpleNamespace(authored_token=SimpleNamespace(authored_digest='source'))


def call(db, prepared, action='pause'):
    return operator.transition_in_writer(db, prepared, None, action=action, epoch='epoch',
        operation_id='op', now=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_pause_writes_receipt_and_owner_in_same_borrowed_transaction(prepared):
    db = DB()
    assert call(db, prepared)['mode'] == 'paused'
    statements = [s for s, _ in db.statements]
    assert any('INSERT INTO operation_receipts' in s for s in statements)
    assert any('UPDATE study_position_owners' in s for s in statements)
    assert not any(s == 'COMMIT' for s in statements)


@pytest.mark.parametrize('action', ['stage', 'activate', 'resume', 'rollforward'])
def test_unqualified_operations_hold_and_roll_back(prepared, action):
    db = DB()
    with pytest.raises(operator.OperatorHold, match='invalid_transition|evidence_unavailable|new_epoch_required'):
        call(db, prepared, action)
    if action != 'rollforward':
        assert any(s.startswith('ROLLBACK TO SAVEPOINT') for s, _ in db.statements)
    assert not any('UPDATE study_position_owners' in s for s, _ in db.statements)


def test_operation_id_cannot_be_reused_with_different_request(prepared):
    db = DB({'command_name': 'position_owner.transition', 'request_hash': 'different',
             'state': 'complete', 'response_json': '{}'})
    with pytest.raises(operator.OperatorHold, match='operation_conflict'):
        call(db, prepared)
    assert any(s.startswith('ROLLBACK TO SAVEPOINT') for s, _ in db.statements)


@pytest.mark.parametrize(('action', 'current', 'expected'), [
    ('stage', 'inactive', 'staged'), ('activate', 'staged', 'active'), ('resume', 'paused', 'active'),
])
def test_qualified_transition_requires_preflight_and_frontier(prepared, monkeypatch, action, current, expected):
    db = DB()
    original = db.execute_native
    def execute(sql, params=()):
        if 'FROM study_position_owners o' in sql:
            db.statements.append((sql, params))
            return Cursor({'epoch': 'epoch', 'mode': current, 'source_digest': 'source'})
        if 'FROM study_position_seed_runs' in sql:
            return Cursor((1,))
        return original(sql, params)
    db.execute_native = execute
    calls = []
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: calls.append('release') or {'revision': 'r'})
    monkeypatch.setattr(operator, 'preflight_in_writer', lambda *a, **kw: calls.append('seed'))
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', lambda *a: calls.append('frontier'))
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    result = operator.transition_in_writer(db, prepared, None, action=action, epoch='epoch',
        operation_id='op', now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    assert result['mode'] == expected
    assert calls == ['release', 'seed', 'frontier']


def test_frontier_hold_prevents_mode_change(prepared, monkeypatch):
    db = DB()
    original = db.execute_native
    def execute(sql, params=()):
        if 'FROM study_position_owners o' in sql:
            return Cursor({'epoch': 'epoch', 'mode': 'staged', 'source_digest': 'source'})
        return original(sql, params)
    db.execute_native = execute
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {})
    monkeypatch.setattr(operator, 'preflight_in_writer', lambda *a, **kw: None)
    def hold(*args):
        raise operator.OperatorHold('position_old_issued_attempt_unfinished')
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', hold)
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    with pytest.raises(operator.OperatorHold, match='old_issued'):
        operator.transition_in_writer(db, prepared, None, action='activate', epoch='epoch',
            operation_id='op', now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    assert not any('UPDATE study_position_owners' in sql for sql, _ in db.statements)


def test_release_evidence_is_candidate_bound_and_requires_runtime_inspection(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace

    revision = 'a' * 40
    registration = {'version': 1, 'daemonId': 'daemon', 'context': 'default',
                    'root': str(tmp_path), 'project': 'tempo', 'postgresVolumeKey': 'pg', 'volumes': {'pg': {'name': 'volume'}}}
    target = hashlib.sha256(json.dumps(['daemon', ['volume']], separators=(',', ':')).encode()).hexdigest()[:24]
    directory = tmp_path / target
    directory.mkdir()
    backup = {'filename': 'verified.dump', 'verified': True, 'restore_created': True, 'restore_removed': True}
    deployment = {'version': 1, 'revision': revision, 'schema': POSTGRES_SCHEMA_VERSION, 'evidence': {'commit': revision},
                  'verified_at': 'now', 'images': {'api': 'sha256:image'}, 'backup': backup}
    guard = {'version': 1, 'target': target, 'state': 'verified', 'verified_schema': POSTGRES_SCHEMA_VERSION,
             'verified_at': 'now', 'database': {'name': 'tempo', 'volume': 'volume'}, 'backup': backup}
    qualification = {'version': 1, 'revision': revision, 'schema': POSTGRES_SCHEMA_VERSION, 'target': target,
        'repertoire_id': 'rep', 'epoch': 'epoch', 'source_digest': 'source',
        'checks': {name: {'status': 'passed', 'evidence': 'test-only evidence'}
                   for name in operator._REQUIRED_QUALIFICATIONS}}
    for name, value in [('deployment.json', deployment), ('migration-guard.json', guard)]:
        (directory / name).write_text(json.dumps(value))
    regpath, qualpath = tmp_path / 'registration.json', tmp_path / 'qualification.json'
    regpath.write_text(json.dumps(registration))
    qualpath.write_text(json.dumps(qualification))
    class Rows:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0]
        def fetchall(self): return self.rows
    class Database:
        def execute_native(self, sql):
            return Rows([('tempo',)] if 'current_database' in sql else [(n,) for n in range(1, POSTGRES_SCHEMA_VERSION + 1)])
    prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='source'))
    kwargs = dict(epoch='epoch', registration=registration, deployment=deployment, guard=guard,
                  qualification=qualification, revision=revision, target=target)
    assert operator.validate_release_evidence(Database(), prepared, **kwargs)['target'] == target
    with pytest.raises(operator.OperatorHold, match='deployment_receipt_unqualified'):
        operator.validate_release_evidence(Database(), prepared, **{**kwargs, 'deployment': {**deployment, 'schema': 57}})
    with pytest.raises(operator.OperatorHold, match='qualification_identity_mismatch'):
        operator.validate_release_evidence(Database(), prepared, **{**kwargs, 'qualification': {**qualification, 'revision': 'b' * 40}})
    class MissingVersion(Database):
        def execute_native(self, sql):
            return Rows([('tempo',)] if 'current_database' in sql else [(n,) for n in range(1, POSTGRES_SCHEMA_VERSION)])
    with pytest.raises(operator.OperatorHold, match='schema_not_ready'):
        operator.validate_release_evidence(MissingVersion(), prepared, **kwargs)
    standalone = dict(kwargs, guard=None, deployment={k: v for k, v in deployment.items() if k != 'backup'},
                      operation={'phase': 'backup_verified', 'revision': revision, 'target': 'tempo', 'backup': backup})
    assert operator.validate_release_evidence(Database(), prepared, **standalone)['backup'] == 'verified.dump'
    standalone['operation'] = {'phase': 'applying_migrations'}
    with pytest.raises(operator.OperatorHold, match='migration_receipt_unqualified'):
        operator.validate_release_evidence(Database(), prepared, **standalone)
    qualification['checks']['fresh_introduction'] = True
    with pytest.raises(operator.OperatorHold, match='qualification_incomplete'):
        operator.validate_release_evidence(Database(), prepared, **kwargs)


def test_pause_still_available_after_source_changes(prepared):
    db = DB()
    original = db.execute_native
    def execute(sql, params=()):
        if 'FROM study_position_owners o' in sql:
            return Cursor({'epoch': 'epoch', 'mode': 'active', 'source_digest': 'old-source'})
        return original(sql, params)
    db.execute_native = execute
    assert call(db, prepared)['mode'] == 'paused'


def test_rollforward_preview_classifies_persisted_definition_without_writes(monkeypatch):
    from test_position_source_authority import setup
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    prepared = prepare_position_source(setup(monkeypatch), 'rep')
    definitions = {item.definition for route in prepared.routes for item in route.decisions}
    monkeypatch.setattr(operator, '_lock', lambda db, prepared, **kwargs: 'rep')
    class Rows:
        def __init__(self, values): self.values = values
        def fetchone(self): return self.values[0] if self.values else None
        def fetchall(self): return self.values
    class Database:
        def __init__(self): self.statements = []
        def execute_native(self, sql, params):
            self.statements.append(sql)
            if 'FROM study_position_owners' in sql:
                return Rows([{'epoch': 'epoch', 'mode': 'paused', 'source_digest': 'source'}])
            return Rows([{'variant': definition.key.variant, 'position': definition.key.position,
                          'expected_uci': definition.expected_uci, 'policy_version': definition.policy_version,
                          'owner_epoch': 'epoch', 'source_digest': 'source'} for definition in definitions])
    db = Database()
    result = operator.preview_rollforward_in_writer(db, prepared, epoch='epoch')
    assert result['decisions'][0]['disposition'] == 'preserve_schedule'
    assert all(not sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for sql in db.statements)


def test_native_owner_transition_frontier_and_retry(monkeypatch):
    """Exercise actual transaction/receipt SQL on a task-owned disposable DB."""
    import os
    from pathlib import Path
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory

    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    migration = Path(__file__).resolve().parents[2] / 'tempo_legacy_database/ddl/migrations/057_position_study.sql'
    schema = 'position_f4_' + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            admin.execute('CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY)')
            admin.execute('CREATE TABLE cards(id text PRIMARY KEY, repertoire_id text, fsrs_card_json text)')
            admin.execute('CREATE TABLE repertoire_cards(repertoire_id text,card_id text)')
            admin.execute('CREATE TABLE opening_evidence_attempts(repertoire_id text,state text)')
            admin.execute('CREATE TABLE study_game_feedback(card_id text,status text)')
            admin.execute('''CREATE TABLE queue_attempt_origins(queue_entry_id bigint,card_id text,revision bigint,
                issued_as_head bigint,content_type text,last_status text,review_result_json text,admission_repertoire_id text)''')
            admin.execute('''CREATE TABLE operation_receipts(operation_id text PRIMARY KEY,command_name text NOT NULL,
                request_hash text NOT NULL,state text NOT NULL,response_json text)''')
            admin.execute(migration.read_text())
            admin.execute("INSERT INTO study_position_epochs VALUES ('rep','epoch','source',now())")
            admin.execute("INSERT INTO study_position_owners VALUES ('rep','epoch','inactive',now())")
            admin.execute("INSERT INTO cards(id,repertoire_id) VALUES ('card','rep')")
            admin.execute("INSERT INTO queue_attempt_origins VALUES (1,'card',1,1,'opening','queued',NULL,'rep')")
            class Borrowed:
                read_only = False
                def __init__(self, raw): self.raw = raw
                def execute_native(self, statement, params=()): return self.raw.execute(statement, params)
            def lock(db, source, **kwargs):
                row = db.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                    (operator.owner_lock_key('rep'),)).fetchone()
                if not row[0]: raise operator.OperatorHold('position_owner_busy')
                return 'rep'
            monkeypatch.setattr(operator, '_lock', lock)
            monkeypatch.setattr(operator, 'preflight_in_writer', lambda *a, **kw: {})
            monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {'target': 'test'})
            prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='source',
                as_dict=lambda: {'authored_digest': 'new-source'}))
            release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
            def transition(db, action, operation):
                return operator.transition_in_writer(db, prepared, None, action=action, epoch='epoch',
                    operation_id=operation, now=datetime.now(timezone.utc), release=release if action != 'pause' else None)
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                db = Borrowed(conn)
                with pytest.raises(operator.OperatorHold, match='old_issued'):
                    transition(db, 'stage', 'stage-op')
                assert conn.execute('SELECT count(*) FROM operation_receipts').fetchone()[0] == 0
                conn.execute("UPDATE queue_attempt_origins SET last_status='complete',review_result_json='{}'")
                assert transition(db, 'stage', 'stage-op')['mode'] == 'staged'
                assert transition(db, 'stage', 'stage-op')['mode'] == 'staged'
                assert transition(db, 'activate', 'activate-op')['mode'] == 'active'
                assert transition(db, 'pause', 'pause-op')['mode'] == 'paused'
                conn.rollback()
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                assert conn.execute('SELECT mode FROM study_position_owners').fetchone()[0] == 'inactive'
                assert conn.execute('SELECT count(*) FROM operation_receipts').fetchone()[0] == 0
                # Exercise retained state/grade and resume through real SQL. Source,
                # policy, namespace and release capability are test stubs here.
                import json
                monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
                monkeypatch.setattr(operator, '_compatible_states', lambda *a, **kw: [{'disposition': 'preserve_schedule'}])
                conn.execute("UPDATE study_position_owners SET mode='paused' WHERE repertoire_id='rep'")
                conn.execute("UPDATE queue_attempt_origins SET last_status='complete',review_result_json='{}'")
                prior = {'action': 'activate', 'repertoire_id': 'rep', 'epoch': 'epoch',
                         'source_digest': 'source', 'mode': 'active'}
                conn.execute("INSERT INTO operation_receipts VALUES ('prior','position_owner.transition','hash','complete',%s)",
                             (json.dumps(prior),))
                conn.execute('''INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,
                    owner_epoch,source_digest,version,fsrs_internal_card_id,fsrs_json,due_at)
                    VALUES ('rep','standard','position','e2e4','policy','epoch','source',7,4503599627370496,'{"state":"retained"}',now())''')
                conn.execute('''INSERT INTO study_position_grades(effect_id,source_kind,game_effect_key,game_evidence_json,effect_kind,
                    repertoire_id,variant,position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,
                    grade,request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                    VALUES ('grade','game_lapse','game','{}'::jsonb,'again','rep','standard','position','epoch','e2e4',
                    'policy','source',CURRENT_DATE,'again','request',now(),'before','after',6,7)''')
                before_state = tuple(conn.execute('SELECT fsrs_internal_card_id,fsrs_json,version,due_at FROM study_position_states').fetchone())
                before_grade = tuple(conn.execute('SELECT * FROM study_position_grades').fetchone())
                prepared.authored_token.authored_digest = 'new-source'
                rolled = operator.transition_in_writer(db, prepared, None, action='rollforward', epoch='epoch', new_epoch='next',
                    operation_id='roll', now=datetime.now(timezone.utc), release=release)
                assert rolled['mode'] == 'paused'
                assert tuple(conn.execute('SELECT fsrs_internal_card_id,fsrs_json,version,due_at FROM study_position_states').fetchone()) == before_state
                assert tuple(conn.execute('SELECT * FROM study_position_grades').fetchone()) == before_grade
                assert operator.transition_in_writer(db, prepared, None, action='resume', epoch='next', operation_id='resume',
                    now=datetime.now(timezone.utc), release=release)['mode'] == 'active'
                conn.rollback()
        finally:
            admin.execute('SET search_path TO public')
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_rollforward_rebinds_only_epoch_and_source_and_retains_receipt(monkeypatch):
    db = DB()
    original = db.execute_native
    def execute(sql, params=()):
        if 'FROM study_position_owners o' in sql:
            db.statements.append((sql, params))
            return Cursor({'epoch': 'old', 'mode': 'paused', 'source_digest': 'old-digest'})
        return original(sql, params)
    db.execute_native = execute
    token = SimpleNamespace(authored_digest='new-digest', as_dict=lambda: {'authored_digest': 'new-digest'})
    prepared = SimpleNamespace(authored_token=token)
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {'revision': 'r'})
    monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_fence_retained_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_prior_owner_receipt', lambda *a, **kw: {
        'operation_id': 'prior', 'response_json': '{"mode":"active"}'})
    monkeypatch.setattr(operator, '_compatible_states', lambda *a, **kw: [{'disposition': 'preserve_schedule'}])
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    result = operator.transition_in_writer(db, prepared, None, action='rollforward', epoch='old', new_epoch='new',
        operation_id='roll', now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    assert (result['epoch'], result['prior_epoch'], result['mode'], result['prior_operation_id']) == ('new', 'old', 'paused', 'prior')
    updates = [sql for sql, _ in db.statements if sql.startswith('UPDATE study_position_states')]
    assert updates == ['UPDATE study_position_states SET owner_epoch=%s,source_digest=%s WHERE repertoire_id=%s AND owner_epoch=%s']
    assert not any('DELETE' in sql or 'fsrs_json' in sql for sql, _ in db.statements)


def test_rollforward_changed_definition_rolls_back_without_rebinding(monkeypatch):
    db = DB()
    original = db.execute_native
    def execute(sql, params=()):
        if 'FROM study_position_owners o' in sql:
            return Cursor({'epoch': 'old', 'mode': 'paused', 'source_digest': 'old-digest'})
        return original(sql, params)
    db.execute_native = execute
    prepared = SimpleNamespace(authored_token=SimpleNamespace(authored_digest='new-digest'))
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {})
    monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_fence_retained_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_prior_owner_receipt', lambda *a, **kw: {'response_json': '{"mode":"active"}'})
    monkeypatch.setattr(operator, '_compatible_states', lambda *a, **kw: [{'disposition': 'hold_definition'}])
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    with pytest.raises(operator.OperatorHold, match='definition_changed'):
        operator.transition_in_writer(db, prepared, None, action='rollforward', epoch='old', new_epoch='new',
            operation_id='roll', now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    assert any(sql.startswith('ROLLBACK TO SAVEPOINT') for sql, _ in db.statements)
    assert not any(sql.startswith('UPDATE study_position_states') or sql.startswith('INSERT INTO study_position_epochs')
                   for sql, _ in db.statements)


@pytest.mark.parametrize(('table', 'reason'), [
    ('opening_evidence_attempts', 'evidence_attempt_unfinished'),
    ('study_position_issued_plans', 'issued_attempt_unfinished'),
    ('study_game_feedback f', 'negative_feedback_unsettled'),
])
def test_retained_frontier_refuses_unfinished_obligations(table, reason):
    class Frontier:
        def execute_native(self, sql, params=()):
            return Cursor((1,) if sql.startswith('SELECT') and table in sql else None)
    with pytest.raises(operator.OperatorHold, match=reason):
        operator._fence_retained_frontier(Frontier(), 'rep')


def test_resume_retained_epoch_requires_original_rollforward_receipt(monkeypatch):
    prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='new'))
    monkeypatch.setattr(operator, '_prior_owner_receipt', lambda *a, **kw: None)
    with pytest.raises(operator.OperatorHold, match='receipt_missing'):
        operator._retained_preflight(DB(), prepared, epoch='new')
    monkeypatch.setattr(operator, '_prior_owner_receipt', lambda *a, **kw: {
        'operation_id': 'roll', 'response_json': '{"mode":"paused","state_count":1}'})
    monkeypatch.setattr(operator, '_compatible_states', lambda *a, **kw: [{'disposition': 'preserve_schedule'}])
    monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
    assert operator._retained_preflight(DB(), prepared, epoch='new')['rollforward_operation_id'] == 'roll'


def test_prepare_request_uses_list_inventory_port_and_deleted_donor_fact(monkeypatch):
    from contextlib import contextmanager
    from test_position_source_authority import setup
    from tempo_legacy_database.adapters.authored_repertoires import position_study_source
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.study import position_seed_inventory
    prepared = position_study_source.prepare_position_source(setup(monkeypatch), 'rep')
    reader = {'open': False}
    @contextmanager
    def connection(**options):
        assert options == {'read_only': True, 'authoritative': True, 'repeatable_read': True}
        reader['open'] = True
        try:
            yield object()
        finally:
            reader['open'] = False
    monkeypatch.setattr(postgres_store, 'connection', connection)
    monkeypatch.setattr(position_study_source, 'prepare_position_source', lambda db, rep: prepared)
    def donor_page(db, *, candidate_card_ids):
        assert reader['open'] and isinstance(candidate_card_ids, tuple) and len(candidate_card_ids) <= 500
        # The public owner returns materialized rows, not a cursor.
        return [dict(card_id=card, repertoire_id='rep', revision=1, state='learning', due_date='2026-11-03',
            fsrs_card_json='{}', introduced_at='2026-01-01T00:00:00Z', archived=0, pending_validation=0,
            superseded_by=None, integrity_blocked=False, owner_repertoire_ids=['rep'], card_exists=True,
            content_type='opening', deleted=False) for card in candidate_card_ids]
    monkeypatch.setattr(position_seed_inventory, 'read_seed_donor_page', donor_page)
    result, request = operator._prepare_request('rep')
    assert not reader['open']
    assert result is prepared
    assert request.sources[0].inventory is not None
    assert all(fact.owner_repertoire_ids == ('rep',) for fact in request.donors)
    assert request.donors and all(fact.deleted is False and fact.archived is False for fact in request.donors)


def test_rollforward_then_resume_plan_and_apply_use_retained_proof(monkeypatch):
    import json
    saved = {}
    class StateDB(DB):
        def __init__(self):
            super().__init__()
            self.owner = {'epoch': 'old', 'mode': 'paused', 'source_digest': 'old-digest'}
        def execute_native(self, sql, params=()):
            self.statements.append((sql, params))
            if 'FROM study_position_owners o' in sql:
                return Cursor(dict(self.owner))
            if sql.startswith('INSERT INTO operation_receipts'):
                saved[params[0]] = json.loads(params[3])
            if sql.startswith('UPDATE study_position_owners'):
                self.owner = {'epoch': params[0], 'mode': params[1], 'source_digest': 'new-digest'}
            return Cursor(None)
    db = StateDB()
    prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='new-digest',
        as_dict=lambda: {'authored_digest': 'new-digest'}))
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {})
    monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_fence_retained_frontier', lambda *a: None)
    monkeypatch.setattr(operator, '_compatible_states', lambda *a, **kw: [{'disposition': 'preserve_schedule'}])
    def prior(*args, **kwargs):
        if kwargs.get('action') == 'rollforward':
            return {'operation_id': 'roll', 'response_json': json.dumps(saved['roll'])}
        return {'operation_id': 'prior', 'response_json': '{"mode":"active"}'}
    monkeypatch.setattr(operator, '_prior_owner_receipt', prior)
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert operator.transition_in_writer(db, prepared, None, action='rollforward', epoch='old', new_epoch='new',
        operation_id='roll', now=now, release=release)['mode'] == 'paused'
    payload = {'action': 'resume', 'repertoire_id': 'rep', 'epoch': 'new', 'release': release}
    assert operator.plan_in_writer(db, prepared, None, payload)['preflight']['rollforward_operation_id'] == 'roll'
    assert operator.transition_in_writer(db, prepared, None, action='resume', epoch='new',
        operation_id='resume', now=now, release=release)['mode'] == 'active'


def test_pause_plan_and_apply_do_not_prepare_current_source(monkeypatch, capsys):
    import base64
    import json
    import sys
    from contextlib import contextmanager
    from tempo_legacy_database.adapters import postgres_store
    class PauseDB(DB):
        def execute_native(self, sql, params=()):
            if 'FROM study_position_owners o' in sql:
                return Cursor({'epoch': 'epoch', 'mode': 'active', 'source_digest': 'stored'})
            if sql.startswith('SELECT source_digest FROM study_position_epochs'):
                return Cursor({'source_digest': 'stored'})
            return super().execute_native(sql, params)
    @contextmanager
    def connection(): yield PauseDB()
    monkeypatch.setattr(postgres_store, 'connection', connection)
    monkeypatch.setattr(operator, '_lock_repertoire', lambda db, rep: rep)
    def no_source(*args): raise AssertionError('pause must not prepare invalid current source')
    monkeypatch.setattr(operator, '_prepare_request', no_source)
    for plan in (True, False):
        payload = {'version': 1, 'plan': plan, 'action': 'pause', 'repertoire_id': 'rep',
                   'epoch': 'epoch', 'operation_id': 'pause'}
        wire = base64.b64encode(json.dumps(payload).encode()).decode()
        monkeypatch.setattr(sys, 'argv', ['operator', '--request-base64', wire])
        operator.main()
        assert json.loads(capsys.readouterr().out)['source_digest'] == 'stored'


def test_native_full_source_operator_lifecycle():
    """Real schema/source/seed/operator; synthetic release evidence only.

    Requires a separately created EMPTY task-owned database named
    tempo_position_test_n2_f4_*. The N2 migration helper rejects nonempty public.
    This never checks or qualifies a running installation.
    """
    import hashlib
    import json
    import os
    from datetime import timedelta
    import chess
    import psycopg
    from fsrs import Card
    from psycopg.conninfo import conninfo_to_dict
    from tempo_evidence_math.position_learning import Hold, SourceCertificate
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    from tempo_legacy_database.adapters.study.position_seed_inventory import CARD_PAGE_SQL, DonorFact, InventoryRequest, SourceFacts
    from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot
    dsn = os.environ.get('TEMPO_POSITION_F4_FULL_DSN')
    if not dsn:
        pytest.skip('requires dedicated empty TEMPO_POSITION_F4_FULL_DSN')
    params = conninfo_to_dict(dsn)
    if params.get('host') not in ('127.0.0.1', 'localhost') or not params.get('dbname', '').startswith('tempo_position_test_n2_f4_'):
        raise RuntimeError('refusing nonlocal or non-F4 disposable database')
    from acceptance import _migrate
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    moves = json.dumps(['e2e4', 'e7e5', 'g1f3'])
    fsrs = Card(card_id=42, due=now - timedelta(days=1)).to_json()
    registration = {'version': 1, 'daemonId': 'synthetic', 'context': 'test', 'project': 'test',
                    'postgresVolumeKey': 'pg', 'volumes': {'pg': {'name': 'synthetic-volume'}}}
    target = hashlib.sha256(json.dumps(['synthetic', ['synthetic-volume']], separators=(',', ':')).encode()).hexdigest()[:24]
    revision = 'a' * 40
    backup = {'filename': 'synthetic.dump', 'verified': True, 'restore_created': True, 'restore_removed': True}
    deployment = {'version': 1, 'revision': revision, 'schema': POSTGRES_SCHEMA_VERSION, 'evidence': {'commit': revision},
                  'verified_at': 'synthetic', 'images': {'worker': 'synthetic'}, 'backup': backup}
    def release(source, epoch):
        qualification = {'version': 1, 'revision': revision, 'schema': POSTGRES_SCHEMA_VERSION, 'target': target,
            'repertoire_id': 'f4', 'epoch': epoch, 'source_digest': source.authored_token.authored_digest,
            'checks': {name: {'status': 'passed', 'evidence': 'synthetic-test-only'}
                       for name in operator._REQUIRED_QUALIFICATIONS}}
        return dict(registration=registration, deployment=deployment, guard=None, qualification=qualification,
                    revision=revision, target=target, operation=None)
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        _migrate(raw)
        raw.execute('INSERT INTO settings(id) VALUES(1)')
        raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('f4','F4 synthetic','fixture',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('f4',3,'ready',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('route','f4','Legal route','white',%s,%s,%s)", (chess.STARTING_FEN,moves,now.isoformat()))
        raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
            VALUES('card','f4','prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (chess.STARTING_FEN,moves,day,fsrs,(now-timedelta(days=2)).isoformat()))
        raw.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('f4','card')")
        raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES('f4',3,'route',0,'card',%s,%s,%s,'white')""", (chess.STARTING_FEN,chess.STARTING_FEN,moves))
        raw.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
            VALUES('f4','clean','idle','scan:2',%s,1,1)""", (now.isoformat(),))
        for ident, kind, generation, payload in [('graph','opening_graph_rebuild',3,{}), ('scan','integrity_scan',2,{'repertoire_id':'f4','graph_generation':3})]:
            raw.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
                VALUES(%s,%s,'f4',%s,'complete',%s,%s,%s,%s)""", (ident,kind,generation,json.dumps(payload),now.isoformat(),now.isoformat(),now.isoformat()))
        raw.commit()
        port = PostgresConnection(raw)
        def prepare():
            source = prepare_position_source(port, 'f4')
            assert not isinstance(source, Hold), source
            member = source.snapshot.membership
            cert = SourceCertificate(member.snapshot_id, member.source_revision, member.digest, True, True)
            inventory = inventory_from_snapshot(source.snapshot, source_certificate=cert)
            assert not isinstance(inventory, Hold), inventory
            donors = []
            for row in raw.execute(CARD_PAGE_SQL, (['card'],)).fetchall():
                fact = dict(row)
                fact['owner_repertoire_ids'] = tuple(fact['owner_repertoire_ids'])
                for field in ('archived', 'pending_validation', 'integrity_blocked', 'deleted'):
                    fact[field] = bool(fact[field])
                donors.append(DonorFact(**fact))
            request = InventoryRequest((SourceFacts(source.snapshot, inventory),), tuple(donors))
            raw.commit()
            return source, request
        source, request = prepare()
        for page_number in range(10):
            raw.execute('BEGIN')
            result = operator.seed_page_in_writer(port, source, request, epoch='old',
                operation_id=f'seed-{page_number}', page_size=100, now=now, release=release(source, 'old'))
            raw.commit()
            raw.execute('BEGIN')
            replay = operator.seed_page_in_writer(port, source, request, epoch='old',
                operation_id=f'seed-{page_number}', page_size=100, now=now, release=release(source, 'old'))
            raw.commit()
            assert replay == result
            if result['page']['stage'] == 'complete': break
        assert result['page']['stage'] == 'complete' and result['page']['held'] == 0
        original = [tuple(row) for row in raw.execute('''SELECT variant,position,fsrs_internal_card_id,fsrs_json,version,due_at
            FROM study_position_states ORDER BY variant,position''').fetchall()]
        raw.commit()
        for action in ('stage', 'activate'):
            raw.execute('BEGIN')
            receipt = operator.transition_in_writer(port, source, request, action=action, epoch='old',
                operation_id=action, now=now, release=release(source, 'old'))
            raw.commit()
        assert receipt['mode'] == 'active'
        # Regression: legacy queued scopes may be adopted without changing the
        # projection, origin, scheduling state or the prior review history.
        raw.execute("UPDATE settings SET timezone='UTC' WHERE id=1")
        queue_id = raw.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,admission_kind)
            VALUES(%s,'card',2,17,'reinforcement','due') RETURNING id""", (day,)).fetchone()[0]
        raw.commit()
        source, request = prepare()
        raw.execute("""INSERT INTO queue_attempt_origins(queue_entry_id,card_id,revision,queue_date,cycle,
            admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,
            start_fen,moves_json,trained_color,content_type,issued_as_head,issuance_sequence,issued_at)
            SELECT queue_entry_id+100000,card_id,revision,queue_date,cycle+1,admission_kind,
            admission_repertoire_id,attempt_failed,'complete',NULL,legacy,start_fen,moves_json,trained_color,
            content_type,1,nextval('study_attempt_issuance_sequence'),%s FROM queue_attempt_origins WHERE queue_entry_id=%s""", (now,queue_id))
        raw.commit()
        raw.execute('BEGIN')
        with pytest.raises(operator.OperatorHold, match='position_queue_attempt_exists'):
            operator.adopt_queue_in_writer(port, source, request, epoch='old', operation_id='adopt',
                now=now, release=release(source, 'old'))
        raw.rollback()
        raw.execute("UPDATE queue_attempt_origins SET review_result_json='{}' WHERE queue_entry_id=%s", (queue_id+100000,))
        raw.commit()
        raw.execute('BEGIN')
        plan = operator.adopt_queue_in_writer(port, source, request, epoch='old', operation_id='adopt',
            now=now, release=release(source, 'old'), plan=True)
        assert len(plan['rows']) == 1 and plan['rows'][0]['before']['id'] == queue_id
        assert raw.execute('SELECT admission_repertoire_id FROM daily_queue WHERE id=%s', (queue_id,)).fetchone()[0] is None
        raw.commit()
        raw.execute('BEGIN')
        adopted = operator.adopt_queue_in_writer(port, source, request, epoch='old', operation_id='adopt',
            now=now, release=release(source, 'old'))
        raw.commit()
        raw.execute('BEGIN')
        assert operator.adopt_queue_in_writer(port, source, request, epoch='old', operation_id='adopt',
            now=now, release=release(source, 'old')) == adopted
        assert tuple(raw.execute('SELECT cycle,position,attempt_state,admission_repertoire_id FROM daily_queue WHERE id=%s', (queue_id,)).fetchone()) == (2,17,'reinforcement','f4')
        assert tuple(raw.execute('SELECT admission_repertoire_id,issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=%s', (queue_id,)).fetchone()) == ('f4',0)
        raw.commit()
        raw.execute('BEGIN')
        paused = operator.transition_in_writer(port, None, None, action='pause', repertoire_id='f4', epoch='old',
            operation_id='pause', now=now)
        raw.commit()
        assert paused['mode'] == 'paused'
        raw.execute("UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE id='f4'")
        raw.commit()
        updated, updated_request = prepare()
        assert updated.authored_token.authored_digest != source.authored_token.authored_digest
        raw.execute('BEGIN')
        rolled = operator.transition_in_writer(port, updated, updated_request, action='rollforward', epoch='old',
            new_epoch='new', operation_id='roll', now=now, release=release(updated, 'new'))
        raw.commit()
        assert rolled['mode'] == 'paused'
        payload = {'action': 'resume', 'repertoire_id': 'f4', 'epoch': 'new', 'release': release(updated, 'new')}
        raw.execute('BEGIN')
        assert operator.plan_in_writer(port, updated, updated_request, payload)['preflight']['rollforward_operation_id'] == 'roll'
        raw.commit()
        raw.execute('BEGIN')
        resumed = operator.transition_in_writer(port, updated, updated_request, action='resume', epoch='new',
            operation_id='resume', now=now, release=release(updated, 'new'))
        raw.commit()
        assert resumed['mode'] == 'active'
        assert [tuple(row) for row in raw.execute('''SELECT variant,position,fsrs_internal_card_id,fsrs_json,version,due_at
            FROM study_position_states ORDER BY variant,position''').fetchall()] == original
        assert raw.execute("SELECT count(*) FROM study_position_epochs WHERE repertoire_id='f4'").fetchone()[0] == 2
        assert raw.execute("SELECT fsrs_card_json FROM cards WHERE id='card'").fetchone()[0] == fsrs


def test_seed_plan_uses_real_preview_without_writes(monkeypatch):
    from test_position_source_authority import setup
    from tempo_evidence_math.position_learning import SourceCertificate
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    from tempo_legacy_database.adapters.study.position_seed_inventory import InventoryRequest, SourceFacts
    from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot
    prepared = prepare_position_source(setup(monkeypatch), 'rep')
    membership = prepared.snapshot.membership
    cert = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
    request = InventoryRequest((SourceFacts(prepared.snapshot, inventory_from_snapshot(prepared.snapshot, source_certificate=cert)),), ())
    db = DB()
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {'revision': 'candidate'})
    monkeypatch.setattr(operator, 'fence_namespace', lambda *a: None)
    def no_seed(*a, **kw): raise AssertionError('plan must never write an F2 page')
    monkeypatch.setattr(operator, 'seed_shadow_page', no_seed)
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    result = operator.plan_in_writer(db, prepared, request, {'action': 'seed', 'repertoire_id': 'rep',
        'epoch': 'e', 'page_size': 1, 'release': release})
    assert result['plan'] and len(result['decisions']) == 1 and result['decisions'][0]['disposition'] == 'hold'
    assert not any(sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'SELECT NEXTVAL')) for sql, _ in db.statements)


def test_seed_page_replays_exact_receipt_and_rejects_changed_request(monkeypatch):
    import json
    class ReceiptDB(DB):
        def execute_native(self, sql, params=()):
            self.statements.append((sql, params))
            if 'FROM operation_receipts WHERE' in sql:
                return Cursor(self.existing)
            if sql.startswith('INSERT INTO operation_receipts'):
                self.existing = dict(command_name=params[1], request_hash=params[2], state='complete', response_json=params[3])
            return Cursor()
    db = ReceiptDB()
    token = SimpleNamespace(repertoire_id='rep', authored_digest='digest', as_dict=lambda: {'authored_digest': 'digest'})
    prepared = SimpleNamespace(authored_token=token)
    calls = []
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {'revision': 'candidate'})
    monkeypatch.setattr(operator, 'seed_shadow_page', lambda db, source, request, **kw: calls.append(kw) or {'stage': 'seeding', 'seeded': 1, 'held': 0})
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    args = dict(epoch='epoch', operation_id='op', page_size=1, now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    first = operator.seed_page_in_writer(db, prepared, object(), **args)
    assert operator.seed_page_in_writer(db, prepared, object(), **args) == first
    assert len(calls) == 1 and calls[0]['page_size'] == 1
    assert json.loads(db.existing['response_json']) == first
    with pytest.raises(operator.OperatorHold, match='operation_conflict'):
        operator.seed_page_in_writer(db, prepared, object(), **(args | {'page_size': 2}))
    assert len(calls) == 1
    assert any(sql.startswith('ROLLBACK TO SAVEPOINT') for sql, _ in db.statements)


def test_seed_page_failure_rolls_back_before_receipt(monkeypatch):
    db = DB()
    prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='digest',
        as_dict=lambda: {'authored_digest': 'digest'}))
    monkeypatch.setattr(operator, '_lock', lambda *a, **kw: 'rep')
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: {})
    def fail(*a, **kw): raise ValueError('source_changed')
    monkeypatch.setattr(operator, 'seed_shadow_page', fail)
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t', operation=None)
    with pytest.raises(ValueError, match='source_changed'):
        operator.seed_page_in_writer(db, prepared, object(), epoch='epoch', operation_id='op', page_size=1,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc), release=release)
    assert any(sql.startswith('ROLLBACK TO SAVEPOINT') for sql, _ in db.statements)
    assert not any(sql.startswith('INSERT INTO operation_receipts') for sql, _ in db.statements)


@pytest.mark.parametrize('plan',[True,False])
def test_cli_begins_caller_owned_transaction_before_plan_or_seed(monkeypatch,capsys,plan):
    import base64,json,sys
    from contextlib import contextmanager
    from tempo_legacy_database.adapters import postgres_store
    db=SimpleNamespace(read_only=False,raw=SimpleNamespace(info=SimpleNamespace(transaction_status=SimpleNamespace(name='IDLE'))))
    statements=[]
    def execute(sql,params=()):
        statements.append(sql)
        assert sql=='SELECT 1'
        db.raw.info.transaction_status=SimpleNamespace(name='INTRANS')
        return Cursor((1,))
    db.execute_native=execute
    exited=[]
    @contextmanager
    def connection():
        yield db
        exited.append(True)
    def borrowed(database,*args,**kwargs):
        if not operator._writer(database):
            raise operator.OperatorHold('borrowed_owner_writer_transaction_required')
        return {'plan':plan,'borrowed_transaction':True}
    monkeypatch.setattr(postgres_store,'connection',connection)
    monkeypatch.setattr(operator,'_prepare_request',lambda rep:(object(),object()))
    monkeypatch.setattr(operator,'plan_in_writer',borrowed)
    monkeypatch.setattr(operator,'seed_page_in_writer',borrowed)
    payload={'version':1,'plan':plan,'action':'seed','repertoire_id':'rep','epoch':'epoch','operation_id':'op','release':{}}
    wire=base64.b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setattr(sys,'argv',['operator','--request-base64',wire])
    operator.main()
    assert json.loads(capsys.readouterr().out)['borrowed_transaction']
    assert statements==['SELECT 1'] and exited==[True]
