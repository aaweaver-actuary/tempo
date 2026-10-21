"""Study HTTP query lifetime, cursor failure timing and callback order."""
import ast
from contextlib import contextmanager
from pathlib import Path
import re

import pytest

from app import study_routes
from tempo_legacy_database.adapters.commands.studies import routes as storage


def test_exercise_row_query_is_eager_but_later_cursor_failure_stays_lazy():
    events = []

    class Cursor:
        def __iter__(self):
            events.append('iterator')
            return self

        def __next__(self):
            if events.count('read') == 1:
                events.append('failure')
                raise RuntimeError('later cursor failure')
            events.append('read')
            return ('exercise',)

    class Database:
        def execute(self, statement, parameters):
            assert parameters == ('chapter',)
            events.append('query')
            return Cursor()

    rows = storage.chapter_exercise_rows(Database(), 'chapter')
    assert events == ['query', 'iterator']
    assert next(rows) == ('exercise',)
    events.append('application callback')
    with pytest.raises(RuntimeError, match='later cursor failure'):
        next(rows)
    assert events == ['query', 'iterator', 'read', 'application callback', 'failure']


def test_chapter_payload_callbacks_interleave_before_read_context_closes(monkeypatch):
    database, events = object(), []

    @contextmanager
    def read_context():
        events.append('open')
        try:
            yield database
        finally:
            events.append('closed')

    def exercise_rows(borrowed, chapter):
        assert borrowed is database and chapter == 'chapter'
        events.append('query')

        def rows():
            for identifier in ('first', 'second'):
                events.append('row-' + identifier)
                yield (identifier,)
        return rows()

    def exercise_payload(borrowed, identifier):
        assert borrowed is database and 'closed' not in events
        events.append('payload-' + identifier)
        return {'id': identifier}

    monkeypatch.setattr(study_routes, 'read_connection', read_context)
    monkeypatch.setattr(study_routes, '_require', lambda *_: {'study_id': 'study'})
    monkeypatch.setattr(study_routes, '_exercise_payload', exercise_payload)
    monkeypatch.setattr(storage, 'chapter_exercise_rows', exercise_rows)
    for name in ('source_rows', 'position_rows', 'link_rows'):
        monkeypatch.setattr(storage, name, lambda *_: [])
    result = study_routes.get_chapter('study', 'chapter')
    assert result['exercises'] == [{'id': 'first'}, {'id': 'second'}]
    assert events == ['open', 'query', 'row-first', 'payload-first',
                      'row-second', 'payload-second', 'closed']


def test_study_http_keeps_database_operations_in_root_and_driver_alias_identity():
    assert study_routes.postgres_store is storage.postgres_store
    for node in ast.walk(ast.parse(Path(study_routes.__file__).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'execute', 'execute_native', 'executemany', 'configured'}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Preserve the original English "Select at least one source record".
            assert not re.search(r'\bSELECT\b[\s\S]*\bFROM\b', node.value, re.I)
            assert not any(sql in node.value.upper() for sql in
                           ('INSERT INTO ', 'UPDATE CARDS ', 'UPDATE STUDY_', 'DELETE FROM '))
