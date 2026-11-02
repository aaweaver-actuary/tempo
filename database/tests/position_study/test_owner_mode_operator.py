"""Safety checks for the preliminary owner operator."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tempo_legacy_database.adapters.study import position_owner_operator as operator


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
    deployment = {'version': 1, 'revision': revision, 'schema': 57, 'evidence': {'commit': revision},
                  'verified_at': 'now', 'images': {'api': 'sha256:image'}, 'backup': backup}
    guard = {'version': 1, 'target': target, 'state': 'verified', 'verified_schema': 57,
             'verified_at': 'now', 'database': {'name': 'tempo', 'volume': 'volume'}, 'backup': backup}
    qualification = {'version': 1, 'revision': revision, 'schema': 57, 'target': target,
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
            return Rows([('tempo',)] if 'current_database' in sql else [(n,) for n in range(1, 58)])
    prepared = SimpleNamespace(authored_token=SimpleNamespace(repertoire_id='rep', authored_digest='source'))
    kwargs = dict(epoch='epoch', registration=registration, deployment=deployment, guard=guard,
                  qualification=qualification, revision=revision, target=target)
    assert operator.validate_release_evidence(Database(), prepared, **kwargs)['target'] == target
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
            prepared = SimpleNamespace(authored_token=SimpleNamespace(authored_digest='source'))
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
