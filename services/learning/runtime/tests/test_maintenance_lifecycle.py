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
