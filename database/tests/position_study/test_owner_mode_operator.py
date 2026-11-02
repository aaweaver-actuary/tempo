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
    with pytest.raises(operator.OperatorHold, match='invalid_transition|evidence_unavailable|not_integrated'):
        call(db, prepared, action)
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
        return original(sql, params)
    db.execute_native = execute
    calls = []
    monkeypatch.setattr(operator, 'validate_release_evidence', lambda *a, **kw: calls.append('release') or {'revision': 'r'})
    monkeypatch.setattr(operator, 'preflight_in_writer', lambda *a, **kw: calls.append('seed'))
    monkeypatch.setattr(operator, 'fence_old_attempt_frontier', lambda *a: calls.append('frontier'))
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t')
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
    release = dict(registration={}, deployment={}, guard={}, qualification={}, revision='r', target='t')
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
                    'root': str(tmp_path), 'postgresVolumeKey': 'pg', 'volumes': {'pg': {'name': 'volume'}}}
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
    definition = prepared.routes[0].decisions[0].definition
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
                return Rows([{'epoch': 'epoch', 'mode': 'paused'}])
            return Rows([{'variant': definition.key.variant, 'position': definition.key.position,
                          'expected_uci': definition.expected_uci, 'policy_version': definition.policy_version,
                          'owner_epoch': 'epoch'}])
    db = Database()
    result = operator.preview_rollforward_in_writer(db, prepared, epoch='epoch')
    assert result['decisions'][0]['disposition'] == 'preserve_schedule'
    assert result['hold'] == 'position_rollforward_provenance_not_integrated'
    assert all(not sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for sql in db.statements)
