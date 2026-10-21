"""Foreground queue storage capture, lock order and warming boundaries."""
import ast
from pathlib import Path

from app import queue_commands
from app import queue_attempt_origins
from app.services import durable_tasks
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.commands import queue as storage


def test_queue_command_policy_has_no_sql_or_driver_operations():
    for node in ast.walk(ast.parse(Path(queue_commands.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'execute', 'execute_native', 'executemany', 'postgres_sql'}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(sql in node.value.upper() for sql in
                           ('SELECT ', 'INSERT INTO ', 'UPDATE DAILY_QUEUE ', 'DELETE FROM '))


def test_availability_projection_is_captured_before_each_date_read(monkeypatch):
    events, reads = [], []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters):
            reads.append((statement, parameters))
            events.append('read')
            return Cursor({'id': 7 if len(reads) == 1 else 9})

    def builder(reason):
        assert reason == queue_commands.queue_application.MISS_REASON
        events.append('capture')
        return f'projection-{len(reads)}'

    class Today:
        @staticmethod
        def today():
            events.append('date')
            return Today()

        def isoformat(self):
            return 'day'

    monkeypatch.setattr(storage, 'available_attempt_sql', builder)
    monkeypatch.setattr(queue_commands, 'date', Today)
    monkeypatch.setattr(queue_attempt_origins, 'validate_failure_marker',
                        lambda *args: events.append('marker'))
    queue_commands.validate_available_attempt(Database(), 7, 'card', 3)
    assert events == ['capture', 'date', 'read', 'marker', 'capture', 'date', 'read', 'read']
    assert reads[0][0].startswith('projection-0') and reads[0][1] == ('day', 7)
    assert reads[1][0].startswith('projection-1') and reads[1][1] == ('day',)
    assert reads[2][1] == (7, 'card', 3)


def test_queue_warming_captures_compiler_before_completion_callback(monkeypatch):
    events = []
    monkeypatch.setattr(postgres_store, 'postgres_sql', lambda statement: events.append(('compile', statement)))

    def warm_completion():
        events.append(('completion', None))
        monkeypatch.setattr(postgres_store, 'postgres_sql', lambda *_: events.append(('late compiler', None)))

    monkeypatch.setattr(durable_tasks, 'warm_completion_sql', warm_completion)
    queue_commands.warm_queue_refresh_sql()
    assert events == [('completion', None), ('compile', queue_commands._QUEUE_REFRESH_PROJECTION_SQL)]


def test_absent_queue_task_is_advisory_locked_then_rechecked_before_projection(monkeypatch):
    events = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters=()):
            events.append(('read', statement, parameters))
            count = sum(event[0] == 'read' for event in events)
            if count == 1:
                return Cursor(None)
            if count == 2:
                return Cursor({'id': 'task'})
            return Cursor({'state': 'ready', 'refresh_pending': 0})

        def execute_native(self, statement, parameters):
            events.append(('advisory', statement, parameters))

    monkeypatch.setattr(storage, 'PostgresConnection', Database)
    assert queue_commands.ensure_current_queue(Database(), {'queue_date': '2026-10-21'}) == {
        'queue_date': '2026-10-21', 'refresh_pending': False,
    }
    assert [event[0] for event in events] == ['read', 'advisory', 'read', 'read']
    assert events[0][1] == events[2][1] and events[0][2] == events[2][2] == ()
    assert events[1][2] == ('tempo:daily-queue:current:ensure',)
    assert events[-1][1].endswith(' FOR UPDATE')
    assert events[-1][2] == ('2026-10-21',)
