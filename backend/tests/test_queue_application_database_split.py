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


def test_queue_eligibility_and_seed_policy_delegate_database_calls():
    source = Path(queue_application.__file__).read_text()
    names = {'_unlock_postgres_opening_page', '_unlock_eligible_opening_cards', 'seed_queue'}
    for function in ast.parse(source).body:
        if isinstance(function, ast.FunctionDef) and function.name in names:
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {'execute', 'execute_native', 'executemany'}
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not any(sql in node.value.upper() for sql in
                                   ('SELECT ', 'UPDATE CARDS ', 'INSERT INTO ', 'DELETE FROM '))
