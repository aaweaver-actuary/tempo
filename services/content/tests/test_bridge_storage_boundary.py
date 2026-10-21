"""Bridge decisions use semantic facts while the original transaction owns locks."""
from contextlib import contextmanager
from types import SimpleNamespace

from tempo_content.bridge.application import BridgeApplication
from tempo_content import cli


def test_bridge_approval_keeps_scope_before_row_locks_on_one_borrowed_transaction():
    transaction = object()
    events = []

    @contextmanager
    def section():
        events.append('begin')
        yield transaction
        events.append('commit')

    class Storage:
        def draft(self, identifier):
            return {'digest': 'reviewed', 'source_key': 'source', 'source_id': 'source-id',
                    'expected_revision': 3, 'document': {'manifest': {'complete': True}}}

        def source_scope_lock(self, database, source_id):
            assert database is transaction and source_id == 'source-id'
            events.append('scope lock')

        def operation_for_draft(self, database, identifier):
            assert database is transaction and identifier == 'draft'
            events.append('existing operation')
            return None

        def lock_scope(self, database, source_id):
            assert database is transaction and source_id == 'source-id'
            events.append('reservation')

        def source_revision(self, database, source_id):
            assert database is transaction and source_id == 'source-id'
            events.append('source revision')
            return {'requested_revision': 3}

        def insert_operation(self, database, operation_id, identifier, source_id):
            assert database is transaction and identifier == 'draft' and source_id == 'source-id'
            events.append('approved operation')

    application = BridgeApplication(SimpleNamespace(workspace_id='workspace', _transaction=section), Storage())
    application.operation = lambda identifier: {'id': identifier}
    assert application.approve('draft', 'reviewed')['id']
    assert events == ['begin', 'scope lock', 'existing operation', 'reservation',
                      'source revision', 'approved operation', 'commit']


def test_cli_readiness_calls_database_owner_with_unchanged_configuration(monkeypatch, capsys):
    calls = []
    monkeypatch.setenv('TEMPO_CONTENT_WORKSPACE', 'synthetic-workspace')
    monkeypatch.setenv('TEMPO_CONTENT_DATABASE_URL', 'postgresql://unused/synthetic')
    monkeypatch.setattr(cli, 'readiness_from_url', lambda url, workspace: (
        calls.append((url, workspace)) or {'status': 'ready'}))
    assert cli.main(['readiness']) == 0
    assert calls == [('postgresql://unused/synthetic', 'synthetic-workspace')]
    assert '"status": "ready"' in capsys.readouterr().out
