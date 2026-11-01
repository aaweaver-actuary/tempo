"""Application compositions select immutable Learning command definitions."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from threading import Event

import pytest
from fastapi.testclient import TestClient

from tempo_learning_runtime import api, gateway
from tempo_learning_runtime.config import Configuration


def configuration(workspace, maintenance=False):
    return Configuration(workspace, 'unused', 'unused', 'x' * 40,
                         maintenance=maintenance, host_activity_directory=None)


def test_learning_apps_do_not_share_mutable_registration(monkeypatch):
    class Database:
        def __init__(self, config): self.config = config
        @contextmanager
        def scope(self): yield
        def close(self): pass
    monkeypatch.setattr(api, 'Database', Database)
    original_factory = api.register_handlers
    definitions = original_factory()
    def registry_for(label):
        return gateway.CommandRegistry((name, (lambda *_: label) if name == 'study.settings.update'
                                        else definitions.handler(name)) for name in definitions.names)
    registries = iter((registry_for('first'), registry_for('second')))
    monkeypatch.setattr(api, 'register_handlers', lambda: next(registries))
    first = api.create_app(configuration('first'))
    second = api.create_app(configuration('second'))
    monkeypatch.setattr(api, 'register_handlers', original_factory)
    one, two = first.state.command_registry, second.state.command_registry
    assert one is not two
    assert one.names == two.names == api.register_handlers().names
    assert one.handler('queue.issue_head') is two.handler('queue.issue_head')
    with pytest.raises(FrozenInstanceError): one._definitions = {}
    with pytest.raises(TypeError): one._definitions['new'] = lambda *_: None
    with pytest.raises(ValueError, match='Duplicate Learning command'):
        gateway.CommandRegistry((('same', lambda *_: None), ('same', lambda *_: None)))
    @first.get('/first-registry')
    def first_registry(): return {'selected': gateway.current_registry() is one, 'handler': gateway.current_registry().handler('study.settings.update')(None, {})}
    @second.get('/second-registry')
    def second_registry(): return {'selected': gateway.current_registry() is two, 'handler': gateway.current_registry().handler('study.settings.update')(None, {})}
    with TestClient(first) as client:
        assert client.get('/first-registry', headers={'Authorization': 'Bearer ' + 'x' * 40}).json() == {'selected': True, 'handler': 'first'}
    with TestClient(second) as client:
        assert client.get('/second-registry', headers={'Authorization': 'Bearer ' + 'x' * 40}).json() == {'selected': True, 'handler': 'second'}
    assert gateway.current_registry() is gateway.default_registry()


def test_maintenance_to_thread_inherits_selected_registry_and_resets(monkeypatch):
    class Database:
        def __init__(self, config): pass
        @contextmanager
        def scope(self): yield
        def close(self): pass
    monkeypatch.setattr(api, 'Database', Database)
    observed, ran = [], Event()
    monkeypatch.setattr(api, 'safe_run_once', lambda: (observed.append(gateway.current_registry()), ran.set()))
    app = api.create_app(configuration('maintenance', maintenance=True))
    with TestClient(app):
        assert ran.wait(2)
    assert observed and all(item is app.state.command_registry for item in observed)
    assert gateway.current_registry() is gateway.default_registry()


def test_nested_registry_scope_restores_previous_on_exception():
    first = gateway.CommandRegistry((('one', lambda *_: None),))
    second = gateway.CommandRegistry((('two', lambda *_: None),))
    default = gateway.current_registry()
    with gateway.registry_scope(first):
        with pytest.raises(RuntimeError), gateway.registry_scope(second):
            assert gateway.current_registry() is second
            raise RuntimeError('stop')
        assert gateway.current_registry() is first
    assert gateway.current_registry() is default


@pytest.mark.parametrize('command', ['maintain', 'activate'])
def test_cli_entry_paths_select_a_registry(monkeypatch, tmp_path, command, capsys):
    import sys
    from tempo_learning_runtime import cli, maintenance
    class Database:
        def __init__(self, config): pass
        @contextmanager
        def scope(self): yield
        def close(self): pass
    monkeypatch.setattr(cli, 'Database', Database)
    monkeypatch.setattr(cli.Configuration, 'environment', lambda: Configuration(
        'cli', 'unused', 'unused', 'x' * 40, operator_token='o' * 40, maintenance=False))
    selected = []
    def capture(*args, **kwargs):
        selected.append(gateway.current_registry())
        return True
    monkeypatch.setattr(maintenance, 'run_once', capture)
    monkeypatch.setattr(gateway, 'dispatch', capture)
    payload = tmp_path / 'publication.json'
    payload.write_text('{"activation_id":"operation"}')
    monkeypatch.setattr(sys, 'argv', ['tempo-learning', command] + (
        ['--once'] if command == 'maintain' else ['--file', str(payload)]))
    cli.main()
    assert len(selected) == 1 and 'queue.issue_head' in selected[0]
    assert 'publication.activate' in selected[0]
    assert gateway.current_registry() is gateway.default_registry()


def test_registry_import_does_not_require_optional_services():
    import subprocess
    import sys
    code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'redis', 'celery', 'games'}:
        raise AssertionError('optional import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from tempo_learning_runtime.api import register_handlers
assert 'queue.issue_head' in register_handlers()
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_publication_fence_selects_request_app_registry(monkeypatch):
    class Database:
        def __init__(self, config): pass
        @contextmanager
        def scope(self): yield
        def close(self): pass
    monkeypatch.setattr(api, 'Database', Database)
    app = api.create_app(Configuration('fence', 'unused', 'unused', 'x' * 40,
        operator_token='o' * 40, maintenance=False, host_activity_directory=None))
    observed = []
    monkeypatch.setattr(gateway, 'fence_publication', lambda *args: observed.append(gateway.current_registry()) or {'state': 'failed'})
    document = {'workspace_id': 'fence', 'command_name': 'publication.retire', 'request': {
        'publication_id': 'pub', 'publication_generation': 1,
        'expected_activation_revision': 0, 'retirement_id': 'op'}}
    with TestClient(app) as client:
        response = client.post('/internal/publications/operations/op/abort', json=document,
                               headers={'Authorization': 'Bearer ' + 'o' * 40})
    assert response.status_code == 200
    assert observed == [app.state.command_registry]


def test_direct_gateway_default_does_not_import_http_composition():
    import subprocess
    import sys
    code = '''
import importlib.abc
import sys
from contextlib import nullcontext
class RejectHTTP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tempo_learning_runtime.api':
            raise AssertionError('gateway imported HTTP composition')
sys.meta_path.insert(0, RejectHTTP())
from tempo_learning_runtime import gateway
registry = gateway.default_registry()
assert callable(registry.handler('queue.issue_head'))
gateway.connection = lambda **kwargs: nullcontext(None)
gateway.storage.insert_operation_intent = lambda *args: None
gateway.storage.intent_identity = lambda *_: ('queue.issue_head', gateway.request_digest('queue.issue_head', {}))
gateway.record_intent('original-operation', 'queue.issue_head', {})
assert 'tempo_learning_runtime.api' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', code], check=True)
