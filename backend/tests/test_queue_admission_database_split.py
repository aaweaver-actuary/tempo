"""Legacy admission storage boundary, clock order and lazy route facts."""
import ast
from pathlib import Path

import pytest

from app.services import opening_capacity_admission, queue_admission_adapter
from tempo_legacy_database.adapters.queue_refresh.application import admission


def test_admission_composition_contains_no_sql_or_driver_calls():
    for module in (queue_admission_adapter, opening_capacity_admission):
        for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {'execute', 'execute_native', 'executemany'}
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any(sql in node.value.upper() for sql in
                               ('SELECT ', 'INSERT INTO ', 'UPDATE CARDS ', 'DELETE FROM '))


def test_admission_keeps_two_clock_reads_after_retain_hook(monkeypatch):
    database, events = object(), []
    adapter = queue_admission_adapter.QueueAdmissionAdapter(database)
    proposal = {'card_id': 'card', 'reason': 'priority', 'repertoire_id': 'rep'}
    monkeypatch.setattr(admission, 'append_opening', lambda *args: events.append(('append', args)))
    monkeypatch.setattr(adapter, 'retain_unseen', lambda *args: events.append(('retain', args)))
    monkeypatch.setattr(admission, 'resolve_opportunity', lambda *args: events.append(('resolve', args)))

    class Clock:
        def __init__(self, number):
            self.number = number

        @staticmethod
        def now(zone):
            assert zone is queue_admission_adapter.timezone.utc
            number = sum(event[0] == 'clock' for event in events) + 1
            events.append(('clock', number))
            return Clock(number)

        def isoformat(self):
            return f'time-{self.number}'

    monkeypatch.setattr(queue_admission_adapter, 'datetime', Clock)
    adapter.introduce_opening('day', proposal, 3)
    assert [event[0] for event in events] == ['append', 'retain', 'clock', 'clock', 'resolve']
    assert events[0][1] == (database, 'day', 'card', 3, 'priority', 'rep')
    assert events[1][1] == ('day', 'card')
    assert events[-1][1] == (database, 'time-1', 'time-2', 'rep', 'card')


@pytest.mark.parametrize('entrypoint', ['borrowed_rows', 'adapter', 'reader_wrapper'])
def test_capacity_rows_remain_lazy_and_interleave_with_same_route_policy(monkeypatch, entrypoint):
    events = []
    route_rows = [
        ('child', 'rep', 2, 'z-line', 1, 'parent', 0),
        ('child', 'rep', 2, 'a-line', 1, 'parent', 1),
    ]

    class Database:
        def execute(self, statement, parameters):
            assert statement == 'fixed-projection' and parameters == ('rep',)
            events.append('query')
            return self

        def fetchall(self):
            return [tuple(row) for row in route_rows]

    def rows(read, repertoire_id, parents, batch_size):
        assert repertoire_id == 'rep' and parents == ('parent',)
        assert batch_size == opening_capacity_admission.CAPACITY_READ_BATCH_SIZE
        for number, row in enumerate(read('fixed-projection', ('rep',))):
            events.append(f'row-{number}')
            yield row
        events.append('exhausted')

    original_route = opening_capacity_admission.CapacityRoute

    def route(*args):
        events.append('policy')
        return original_route(*args)

    monkeypatch.setattr(admission, 'read_capacity_route_rows', rows)
    monkeypatch.setattr(opening_capacity_admission.capacity_storage, 'read_capacity_route_rows', rows)
    monkeypatch.setattr(opening_capacity_admission, 'CapacityRoute', route)
    database = Database()
    if entrypoint == 'borrowed_rows':
        facts = admission.borrowed_capacity_route_rows(
            database, 'rep', ('parent',), opening_capacity_admission.CAPACITY_READ_BATCH_SIZE,
        )
        assert events == []
        result = opening_capacity_admission.capacity_routes_from_rows(facts)
    elif entrypoint == 'adapter':
        result = queue_admission_adapter.QueueAdmissionAdapter(database).capacity_routes('rep', ('parent',))
    else:
        result = opening_capacity_admission.read_capacity_routes(
            lambda statement, parameters: database.execute(statement, parameters).fetchall(),
            'rep', ('parent',),
        )
    assert events == ['query', 'row-0', 'policy', 'row-1', 'policy', 'exhausted']
    assert len(result) == 1 and result[0][0] == 'child'
    assert result[0][1].line_id == 'a-line' and result[0][1].parent_introduced
