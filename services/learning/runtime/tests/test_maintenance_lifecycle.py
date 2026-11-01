"""Fake bounded slices exercise ownership of the Learning maintenance loop."""
from contextlib import contextmanager
from threading import Event

import pytest
from fastapi.testclient import TestClient

from tempo_learning_runtime import api, maintenance
from tempo_learning_runtime.config import Configuration


@pytest.fixture
def lifecycle(monkeypatch):
    events = []
    class Database:
        def __init__(self, config): pass
        @contextmanager
        def scope(self): yield
        def close(self): events.append('database.close')
    class Activity:
        def __init__(self, *args, **kwargs): pass
        def start(self): events.append('activity.start')
        def close(self): events.append('activity.close')
    monkeypatch.setattr(api, 'Database', Database)
    monkeypatch.setattr(api, 'HostActivity', Activity)
    return events


def app():
    return api.create_app(Configuration('lifecycle', 'unused', 'unused', 'x' * 40,
        maintenance=True, host_activity_directory=None))


def test_failed_maintenance_still_closes_owned_resources(monkeypatch, lifecycle):
    ran = Event()
    def fail():
        ran.set()
        raise RuntimeError('terminal maintenance failure')
    monkeypatch.setattr(api, 'safe_run_once', fail)
    with pytest.raises(RuntimeError, match='terminal maintenance failure'):
        with TestClient(app()):
            assert ran.wait(2)
    assert lifecycle == ['activity.start', 'activity.close', 'database.close']


def test_idle_stop_after_temporary_contention_closes_resources(monkeypatch, lifecycle):
    ran = Event()
    def contend():
        ran.set()
        raise maintenance.psycopg.OperationalError('temporarily busy')
    monkeypatch.setattr(maintenance, 'run_once', contend)
    with TestClient(app()):
        assert ran.wait(2)
    assert lifecycle == ['activity.start', 'activity.close', 'database.close']


def test_shutdown_waits_for_inflight_slice_before_closing(monkeypatch, lifecycle):
    import asyncio
    entered, release = Event(), Event()
    def slice_work():
        lifecycle.append('slice.start')
        entered.set()
        assert release.wait(2)
        lifecycle.append('slice.end')
        return False
    monkeypatch.setattr(api, 'safe_run_once', slice_work)
    async def scenario():
        application = app()
        lifespan = application.router.lifespan_context(application)
        await lifespan.__aenter__()
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.sleep(0)
            assert not shutdown.done()
            assert 'database.close' not in lifecycle
            release.set()
            await asyncio.wait_for(shutdown, 2)
        finally:
            release.set()
    asyncio.run(scenario())
    assert lifecycle == ['activity.start', 'slice.start', 'slice.end', 'activity.close', 'database.close']


def test_activity_close_failure_still_closes_database(monkeypatch, lifecycle):
    ran = Event()
    monkeypatch.setattr(api, 'safe_run_once', lambda: ran.set() or False)
    original = api.HostActivity
    class BrokenActivity(original):
        def close(self):
            super().close()
            raise RuntimeError('activity close failed')
    monkeypatch.setattr(api, 'HostActivity', BrokenActivity)
    with pytest.raises(RuntimeError, match='activity close failed'):
        with TestClient(app()):
            assert ran.wait(2)
    assert lifecycle == ['activity.start', 'activity.close', 'database.close']


def test_active_terminal_failure_is_visible_without_exposing_error(monkeypatch, lifecycle):
    import time
    from contextlib import nullcontext
    ran = Event()
    def fail():
        ran.set()
        raise RuntimeError('private payload must stay private')
    monkeypatch.setattr(api, 'safe_run_once', fail)
    monkeypatch.setattr(api, 'connection', lambda **_: nullcontext(object()))
    monkeypatch.setattr(api.storage, 'migration_state', lambda _: ({'a': 'x'}, {'a': 'x'}))
    monkeypatch.setattr(api.HostActivity, 'request', lambda _: nullcontext(), raising=False)
    application = app()
    with pytest.raises(RuntimeError, match='private payload'):
        with TestClient(application) as client:
            assert ran.wait(2)
            deadline = time.monotonic() + 2
            while application.state.maintenance_status['failure'] is None:
                assert time.monotonic() < deadline
                time.sleep(.005)
            assert client.get('/health/live').json() == {'status': 'alive'}
            response = client.get('/health/ready')
            assert response.status_code == 503
            assert response.json() == {'status': 'unavailable', 'domain': 'maintenance',
                'reason': 'essential_maintenance_failed', 'error_class': 'RuntimeError'}
            assert 'private payload' not in response.text
    assert lifecycle == ['activity.start', 'activity.close', 'database.close']


def test_maintenance_failure_is_scoped_to_each_app_and_disabled_stays_ready(monkeypatch, lifecycle):
    import time
    from contextlib import nullcontext
    failed = Event()
    def fail():
        failed.set()
        raise ValueError('private')
    monkeypatch.setattr(api, 'safe_run_once', fail)
    monkeypatch.setattr(api, 'connection', lambda **_: nullcontext(object()))
    monkeypatch.setattr(api.storage, 'migration_state', lambda _: ({'a': 'x'}, {'a': 'x'}))
    monkeypatch.setattr(api.HostActivity, 'request', lambda _: nullcontext(), raising=False)
    first = app()
    second = api.create_app(Configuration('other', 'unused', 'unused', 'x' * 40,
        maintenance=False, host_activity_directory=None))
    with pytest.raises(ValueError, match='private'):
        with TestClient(first) as broken, TestClient(second) as normal:
            assert failed.wait(2)
            deadline = time.monotonic() + 2
            while first.state.maintenance_status['failure'] is None:
                assert time.monotonic() < deadline
                time.sleep(.005)
            assert broken.get('/health/ready').status_code == 503
            assert normal.get('/health/ready').json() == {'status': 'ready', 'workspace_id': 'other'}
            assert second.state.maintenance_status['failure'] is None
    assert lifecycle.count('activity.close') == 2
    assert lifecycle.count('database.close') == 2


def test_two_enabled_apps_keep_terminal_maintenance_state_separate(monkeypatch, lifecycle):
    import time
    from contextlib import nullcontext
    from contextvars import ContextVar
    active_workspace = ContextVar('test_workspace')
    failed, healthy_ran = Event(), Event()
    class ScopedDatabase:
        def __init__(self, config): self.workspace = config.workspace_id
        @contextmanager
        def scope(self):
            token = active_workspace.set(self.workspace)
            try: yield
            finally: active_workspace.reset(token)
        def close(self): lifecycle.append('database.close')
    def step():
        if active_workspace.get() == 'lifecycle':
            failed.set()
            raise RuntimeError('private')
        healthy_ran.set()
        return False
    monkeypatch.setattr(api, 'Database', ScopedDatabase)
    monkeypatch.setattr(api, 'safe_run_once', step)
    monkeypatch.setattr(api, 'connection', lambda **_: nullcontext(object()))
    monkeypatch.setattr(api.storage, 'migration_state', lambda _: ({'a': 'x'}, {'a': 'x'}))
    monkeypatch.setattr(api.HostActivity, 'request', lambda _: nullcontext(), raising=False)
    first = app()
    second = api.create_app(Configuration('healthy', 'unused', 'unused', 'x' * 40,
        maintenance=True, host_activity_directory=None))
    with pytest.raises(RuntimeError, match='private'):
        with TestClient(first) as broken, TestClient(second) as healthy:
            assert failed.wait(2) and healthy_ran.wait(2)
            deadline = time.monotonic() + 2
            while first.state.maintenance_status['failure'] is None:
                assert time.monotonic() < deadline
                time.sleep(.005)
            assert broken.get('/health/ready').status_code == 503
            assert healthy.get('/health/ready').json() == {'status': 'ready', 'workspace_id': 'healthy'}
    assert lifecycle.count('activity.close') == 2
    assert lifecycle.count('database.close') == 2


def test_failure_during_readiness_probe_prevents_ready_response(monkeypatch, lifecycle):
    from contextlib import nullcontext
    application = app()
    monkeypatch.setattr(api, 'connection', lambda **_: nullcontext(object()))
    def probe(_):
        application.state.maintenance_status['failure'] = 'RuntimeError'
        return ({'a': 'x'}, {'a': 'x'})
    monkeypatch.setattr(api.storage, 'migration_state', probe)
    endpoint = next(route.endpoint for route in application.routes if getattr(route, 'path', None) == '/health/ready')
    response = endpoint()
    assert response.status_code == 503
