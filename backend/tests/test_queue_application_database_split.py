"""Focused storage-boundary and captured-unlock projection checks."""
import ast
from pathlib import Path

from app.services import queue_application
from tempo_legacy_database.adapters.queue_refresh.application import eligibility


def test_queue_unlock_captures_eligible_projection_before_selected_write(monkeypatch):
    original = eligibility._OPENING_UNLOCK_ELIGIBILITY_SQL
    statements = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class BorrowedDatabase:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            if len(statements) == 1:
                return Cursor([('a',), ('b',)])
            # The write must use the projection already captured for this page.
            monkeypatch.setattr(eligibility, '_OPENING_UNLOCK_ELIGIBILITY_SQL', 'changed')
            return Cursor([('a',)])

        def execute(self, statement, parameters):
            statements.append((statement, parameters))

    assert queue_application._unlock_postgres_opening_page(
        BorrowedDatabase(), after_card_id='', batch_size=1,
    ) == 'a'
    ready = original.replace('WHERE step.card_id=cards.id',
                             "WHERE publication.state='ready' AND step.card_id=cards.id")
    assert statements[1][1] == (['a', 'b'], 1)
    assert ready in statements[1][0] and ready in statements[2][0]
    assert statements[2][1] == ['a']


def test_queue_application_policy_delegates_all_database_calls():
    source = Path(queue_application.__file__).read_text()
    for function in ast.parse(source).body:
        if isinstance(function, ast.FunctionDef):
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {'execute', 'execute_native', 'executemany'}
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not any(sql in node.value.upper() for sql in
                                   ('SELECT ', 'UPDATE CARDS ', 'INSERT INTO ', 'DELETE FROM '))


def test_queue_materialization_captures_clock_before_blocked_read(monkeypatch):
    from tempo_legacy_database.adapters.queue_refresh.application import publication

    database, events = object(), []
    diagnostics = [{'card_id': 'card', 'message': 'incomplete'}]

    def record(name, result=None):
        def apply(borrowed, *args, **kwargs):
            assert borrowed is database
            events.append((name, args, kwargs))
            return result
        return apply

    for name in ('begin_projection', 'clear_diagnostics', 'record_diagnostics', 'publish_projection'):
        monkeypatch.setattr(publication, name, record(name))
    monkeypatch.setattr(publication, 'blocked_count', record('blocked_count', 7))
    monkeypatch.setattr(queue_application, 'seed_queue', record('seed'))
    monkeypatch.setattr(queue_application, 'randomize_daily_queue', record('randomize'))
    monkeypatch.setattr(queue_application, '_quarantine_malformed_opening_cards',
                        record('quarantine', diagnostics))

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is queue_application.timezone.utc
            events.append(('clock', (), {}))
            return Clock()

        def isoformat(self):
            return 'captured-before-read'

    monkeypatch.setattr(queue_application, 'datetime', Clock)
    queue_application.materialize_daily_queue(database, 'day', preserve_through_entry_id=9)
    assert [name for name, *_ in events] == [
        'begin_projection', 'seed', 'randomize', 'quarantine', 'clear_diagnostics',
        'record_diagnostics', 'clock', 'blocked_count', 'publish_projection',
    ]
    assert events[2] == ('randomize', ('day',), {'preserve_through_entry_id': 9})
    assert events[5][1] == ([('day', 'card', 'incomplete')],)
    assert events[-1][1] == ('day', 'captured-before-read', 7)
