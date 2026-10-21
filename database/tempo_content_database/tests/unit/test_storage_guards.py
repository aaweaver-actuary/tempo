"""Content-owned storage-only unit guards; synthetic drivers, no native infrastructure."""
from contextlib import contextmanager

import chess
import pytest

from tempo_content.domain import ContentError, canonical_position
from tempo_content_database.adapters import store
from tempo_content_database.adapters.store import ContentService
from tempo_content_database.ddl import migrate as migration


def storage_binding_case():
    task = {'cursor': 0, 'source_id': 'source-id', 'generation': 7}
    binding = {'route_key': 'route', 'policy_key': 'policy', 'ply': 0, 'exercise_key': 'exercise'}
    route = {'artifact_id': 'artifact-id', 'complete': True}
    occurrence = {'position_key': canonical_position(chess.STARTING_FEN),
                  'opponent': False, 'authored_move': 'e2e4'}
    return task, binding, route, occurrence


@pytest.mark.parametrize('exists', [False, True])
def test_binding_target_storage_preserves_read_parameters_and_missing_route_short_circuit(exists):
    task, binding, route, occurrence = storage_binding_case()
    calls = []

    def one(query, parameters):
        calls.append(parameters)
        return (route if exists else None) if len(calls) == 1 else occurrence

    service = store.ContentService.__new__(store.ContentService)
    service._one = one
    assert service._binding_target(task, binding) == ((route, occurrence) if exists else (None, None))
    assert calls == ([('source-id', 7, 'route', 'policy'), ('artifact-id', 0)] if exists else [
        ('source-id', 7, 'route', 'policy')])


@pytest.mark.parametrize('failure', [None, 'fence', 'after_writes'])
def test_binding_commit_keeps_fence_insert_advance_and_hook_in_one_transaction(failure):
    task, binding, route, occurrence = storage_binding_case()
    events = []
    error = ContentError('stale_generation' if failure == 'fence' else 'interrupted', 'Interrupted write')

    class Transaction:
        def execute(self, query, parameters):
            assert parameters[:7] == ('source-id', 7, 'route', 'policy', 0, 'exercise', occurrence['position_key'])
            assert parameters[7].obj == ['e2e4']
            events.append('insert')

    transaction = Transaction()

    @contextmanager
    def section():
        events.append('begin')
        try:
            yield transaction
        except Exception:
            events.append('rollback')
            raise
        else:
            events.append('commit')

    def hook(event, context):
        assert event == 'after_writes' and context['task'] is task
        events.append(event)
        if failure == event:
            raise error

    def fence(database, supplied_task, supplied_hook):
        assert database is transaction and supplied_task is task and supplied_hook is hook
        events.append('fence')
        if failure == 'fence':
            raise error

    def advance(database, supplied_task, *, cursor):
        assert database is transaction and supplied_task is task and cursor == 1
        events.append('advance')

    service = store.ContentService.__new__(store.ContentService)
    service._transaction, service._fence, service._advance = section, fence, advance
    if failure is None:
        service._commit_binding(task, binding, occurrence, ['e2e4'], hook)
        assert events == ['begin', 'fence', 'insert', 'advance', 'after_writes', 'commit']
    else:
        with pytest.raises(ContentError) as raised:
            service._commit_binding(task, binding, occurrence, ['e2e4'], hook)
        assert raised.value is error
        assert events == (['begin', 'fence', 'rollback'] if failure == 'fence' else [
            'begin', 'fence', 'insert', 'advance', 'after_writes', 'rollback'])


@pytest.mark.parametrize('stale', [False, True])
def test_worker_failure_storage_keeps_one_transaction_and_fence_before_update(stale):
    events = []
    task = {'source_id': 'source-id', 'generation': 4}

    class Failure(ValueError):
        def __str__(self):
            events.append('error text')
            return 'Malformed content'

    class Transaction:
        def execute(self, query, parameters):
            events.append('update')
            assert parameters[0].obj == {'code': 'invalid_content', 'message': 'Malformed content'}
            assert parameters[1:] == ('source-id', 4)

    @contextmanager
    def section():
        events.append('begin')
        yield Transaction()
        events.append('commit')

    def fence(database, supplied_task):
        assert isinstance(database, Transaction) and supplied_task is task
        events.append('fence')
        if stale:
            raise ContentError('stale_generation', 'Lease lost')

    service = ContentService.__new__(ContentService)
    service._transaction = section
    service._fence = fence
    assert service._fail_task(task, 'invalid_content', Failure()) is (not stale)
    assert events == (['begin', 'fence', 'commit'] if stale else [
        'begin', 'fence', 'error text', 'update', 'commit'])


def test_readiness_owner_borrows_the_original_connection_context(monkeypatch):
    transaction = object()
    events = []

    @contextmanager
    def connect(url):
        assert url == 'postgresql://unused/synthetic'
        events.append('connect')
        yield transaction
        events.append('close')

    monkeypatch.setattr(migration.psycopg, 'connect', connect)
    monkeypatch.setattr(migration, 'readiness', lambda database, workspace: (
        events.append((database is transaction, workspace)) or {'status': 'ready'}))
    assert migration.readiness_from_url('postgresql://unused/synthetic', 'workspace') == {'status': 'ready'}
    assert events == ['connect', (True, 'workspace'), 'close']
