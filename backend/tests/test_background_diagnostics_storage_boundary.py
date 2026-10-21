"""Moved diagnostics reader keeps clocks, lazy reads and unavailable results."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import background_diagnostics as application
from app.services.background_runtime import RuntimeSnapshot
from tempo_legacy_database.adapters.background_diagnostics import storage


def prepared(monkeypatch, *, postgres=False, error=None, expired=False):
    clock = SimpleNamespace(value=0.0, reads=0)
    events = []
    handlers = []
    now = datetime(2026, 10, 20, 23, 58, tzinfo=timezone.utc)
    queues = [
        [('2026-10-20T00:00:00+00:00',)],
        [dict(diagnostic_state='delayed', underlying_state='retrying', count=1,
              pending_since=now.isoformat(), generation_started_at=now.isoformat(),
              next_attempt_at='2026-10-21T00:00:00+00:00', estimated=1)],
        [dict(diagnostic_state='queued', count=2, origin=now.isoformat(), generation_started_at=now.isoformat(), estimated=1)],
        [dict(diagnostic_state='paused', count=3, origin=now.isoformat())],
        [dict(kind='engine_game', **{name: 1 for name in application.COUNT_NAMES + application.DURATION_NAMES})],
    ]
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz):
            return now
    def monotonic():
        clock.reads += 1
        return clock.value
    class Database:
        def execute_native(self, statement, parameters=()):
            if 'set_config' in statement:
                events.append(('config', statement, parameters))
                return SimpleNamespace(fetchall=lambda: [])
            return self.execute(statement, parameters)
        def execute(self, statement, parameters=()):
            if error:
                if expired:
                    clock.value = 1.0
                raise error
            index = sum(event[0] == 'query' for event in events)
            events.append(('query', index, parameters))
            clock.value += 0.01
            return SimpleNamespace(fetchall=lambda: queues[index])
        def set_progress_handler(self, handler, interval):
            assert interval == 1000
            handlers.append(handler)
    @contextmanager
    def connection(timeout=None):
        events.append(('enter', timeout))
        try:
            yield Database()
        finally:
            events.append(('exit',))
    def translate(statement):
        events.append(('translate',))
        return statement
    postgres_module = SimpleNamespace(configured=lambda: postgres,
                                     diagnostic_read_connection=connection, postgres_sql=translate)
    monkeypatch.setattr(application, 'postgres_store', postgres_module)
    monkeypatch.setattr(application, 'read_connection', connection)
    monkeypatch.setattr(application, 'datetime', FixedDatetime)
    monkeypatch.setattr(application, 'time', SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(application, 'background_runtime', SimpleNamespace(snapshot=lambda: RuntimeSnapshot(available=False)))
    return clock, events, handlers


@pytest.mark.parametrize('postgres', [False, True])
def test_diagnostics_semantic_reads_preserve_scope_budget_and_public_projection(monkeypatch, postgres):
    clock, events, handlers = prepared(monkeypatch, postgres=postgres)
    result = application.snapshot()
    assert result.available and result.collection_started_at == '2026-10-20T00:00:00+00:00'
    assert [(queue.queue, queue.state, queue.count) for queue in result.queues] == [
        ('durable', 'delayed', 1), ('engine_game', 'queued', 2), ('engine_defense', 'paused', 3)]
    assert result.queues[0].next_eligibility_seconds == 120.0
    assert result.queues[0].underlying_state == 'retrying'
    assert result.counters[0].useful_completion_unit == 'accepted_position'
    assert type(result.counters[0].counts.claims) is int
    assert type(result.counters[0].counts.engine_successful_seconds) is float
    assert [event[1] for event in events if event[0] == 'query'] == list(range(5))
    assert clock.reads == 7 and result.query_duration_seconds == pytest.approx(.05)
    assert events[0] == ('enter', .1 if postgres else None) and events[-1] == ('exit',)
    if postgres:
        configs = [event for event in events if event[0] == 'config']
        assert configs[0][1:] == ("SELECT set_config('jit', 'off', true)", ())
        assert len(configs) == 6 and handlers == []
        assert len([event for event in events if event[0] == 'translate']) == 5
    else:
        assert len(handlers) == len({id(handler) for handler in handlers}) == 5
        clock.value = 1
        assert handlers[0]() == 1
        clock.value = 0
        assert handlers[0]() == 0


@pytest.mark.parametrize('postgres', [False, True])
def test_diagnostics_exhausted_budget_closes_scope_before_runtime_projection(monkeypatch, postgres):
    clock, events, handlers = prepared(monkeypatch, postgres=postgres)
    monkeypatch.setattr(application, 'QUERY_BUDGET_SECONDS', 0)
    result = application.snapshot()
    assert not result.available and result.unavailable_reason == 'query_deadline'
    assert result.queues == result.counters == []
    assert not any(event[0] == 'query' for event in events) and handlers == []
    assert events[-1] == ('exit',)


@pytest.mark.parametrize('expired', [False, True])
def test_diagnostics_storage_error_retains_elapsed_budget_classification(monkeypatch, expired):
    clock, events, handlers = prepared(monkeypatch, error=storage.sqlite3.OperationalError('synthetic'), expired=expired)
    result = application.snapshot()
    assert not result.available
    assert result.unavailable_reason == ('query_deadline' if expired else 'storage_unavailable')
    assert events[-1] == ('exit',)


def test_diagnostics_engine_reads_stay_lazy_between_application_rows():
    events = []
    reader = storage.DiagnosticReader(lambda statement, parameters: events.append(parameters) or [])
    rows = reader.engine_rows('SELECT request.id,request.request_json FROM eligible request WHERE true', lambda identifier: 'allowed')
    assert events == []
    assert next(rows) == ('engine_game', [])
    assert events == [('game_analysis',)]
    assert next(rows) == ('engine_defense', [])
    assert events == [('game_analysis',), ('threat_analysis',)]
    with pytest.raises(StopIteration):
        next(rows)


def test_diagnostics_compatibility_error_and_connection_exports_preserve_identity():
    assert application.sqlite3 is storage.sqlite3 and application.psycopg is storage.psycopg
    assert application.PoolTimeout is storage.PoolTimeout
    assert application.read_connection is storage.read_connection
    assert application.postgres_store is storage.postgres_store
