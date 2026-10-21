"""Fake borrowed-driver checks for the PGN import storage split; no database startup."""
from datetime import date
import json
from types import SimpleNamespace

import chess
import pytest

from app import pgn_import_commands as application
from tempo_legacy_database.adapters.authored_repertoires.pgn_import import storage


class Rows:
    def __init__(self, *, value=None, rows=(), events=None):
        self.value, self.rows, self.events = value, rows, events
    def fetchone(self):
        if self.events is not None:
            self.events.append('fetchone')
        return self.value
    def __iter__(self):
        if self.events is not None:
            self.events.append('iterate')
        return iter(self.rows)


class Batch:
    def __init__(self, events, result_sets):
        self.events, self.result_sets = events, iter(result_sets)
        self.calls, self.open = [], False
    def __enter__(self):
        self.open = True
        self.events.append('cursor_enter')
        return self
    def __exit__(self, *error):
        self.open = False
        self.events.append('cursor_exit')
    def executemany(self, statement, parameters, **keywords):
        self.calls.append((statement, parameters, keywords))
        self.events.append(('batch', len(self.calls)))
        if len(self.calls) == 1:
            self.current = next(self.result_sets, None)
    def fetchall(self):
        self.events.append('fetchall')
        return self.current
    def nextset(self):
        self.events.append('nextset')
        self.current = next(self.result_sets, None)
        return self.current is not None


class Borrowed:
    def __init__(self, events, replies=(), batch=None):
        self.events, self.replies, self.batch = events, iter(replies), batch
        self.raw = self
        self.native_calls, self.raw_calls = [], []
    def execute_native(self, statement, parameters=()):
        self.native_calls.append((statement, parameters))
        self.events.append(('native', len(self.native_calls)))
        return next(self.replies)
    def execute(self, statement, parameters=()):
        self.raw_calls.append((statement, parameters))
        self.events.append(('raw', len(self.raw_calls)))
        return next(self.replies)
    def cursor(self):
        self.events.append('cursor')
        return self.batch
    def __getattr__(self, name):
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


@pytest.mark.parametrize('line_rows,result_sets,expected', [
    ([('one',), ('two',)], [[('one',)], [], [('two',), ('one',)]], {'one', 'two'}),
    ([], [], set()),
])
def test_import_batches_keep_returning_sets_then_depth_annotations_and_cursor_close(line_rows, result_sets, expected):
    events = []
    batch = Batch(events, result_sets)
    database = Borrowed(events, batch=batch)
    depths, annotations = [('depth',)], [('annotation',)]
    assert storage.insert_import_rows(database, line_rows, depths, annotations) == expected
    assert [call[1] is rows for call, rows in zip(batch.calls, (line_rows, depths, annotations))] == [True] * 3
    assert [call[2] for call in batch.calls] == [{'returning': True}, {}, {}]
    returning_events = ['fetchall', 'nextset'] * len(result_sets) if line_rows else []
    assert events == ['cursor', 'cursor_enter', ('batch', 1), *returning_events,
                      ('batch', 2), ('batch', 3), 'cursor_exit']
    assert not batch.open


@pytest.mark.parametrize('existing_repertoire', [False, True])
def test_import_keeps_clock_identity_lazy_reads_certification_and_callback_order(monkeypatch, existing_repertoire):
    events = []
    batch = Batch(events, [])
    database = Borrowed(events, replies=[
        Rows(), Rows(value=('repertoire',) if existing_repertoire else None, events=events), Rows(), Rows(),
        Rows(rows=[dict(id='old-line', start_fen=chess.STARTING_FEN, moves_json='["e2e4"]')], events=events),
        Rows(rows=[('existing',)], events=events), Rows(rows=[], events=events),
    ], batch=batch)
    def now(zone):
        events.append('clock')
        return SimpleNamespace(isoformat=lambda: 'timestamp')
    def new_id():
        events.append('uuid')
        return 'repertoire'
    def scope(connection, lines):
        assert connection is database and not batch.open
        assert lines == [('repertoire', chess.STARTING_FEN, ['e2e4']),
                         ('repertoire', chess.STARTING_FEN, ['d2d4'])]
        events.append('scope')
        return ['old-route', 'new-route']
    original_executemany = batch.executemany
    def executemany(statement, parameters, **keywords):
        if not batch.calls:
            # Use the exact ID prepared by the application, rather than replacing its hash rule.
            batch.result_sets = iter([[(parameters[1][0],)]])
        original_executemany(statement, parameters, **keywords)
    def certify(connection, repertoire, route):
        assert connection is database and repertoire == 'repertoire' and not batch.open
        assert route == 'new-route'
        events.append('certify')
    monkeypatch.setattr(batch, 'executemany', executemany)
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=now))
    monkeypatch.setattr(application.uuid, 'uuid4', new_id)
    monkeypatch.setattr(application, 'date', SimpleNamespace(today=lambda: events.append('day') or date(2026, 10, 20)))
    monkeypatch.setattr(application, 'ensure_batch_lines_in_scope', scope)
    monkeypatch.setattr(application, 'certify_admitted_route', certify)
    monkeypatch.setattr(application, 'invalidate_integrity_in_transaction', lambda *args: events.append('invalidate'))
    monkeypatch.setattr(application, 'request_graph_rebuild_in_transaction', lambda *args: events.append(('graph', args)))
    monkeypatch.setattr(application, 'integrity_summary', lambda *args: events.append('summary') or {'state': 'pending'})
    payload = dict(source_name='book.pgn', trained_color='white', depth=3, games_found=2,
        unique_lines=2, imported_segments_count=2, decision_segments_count=1, prefix_segments_count=1,
        segment_ids=['existing', 'new'], prefix_segment_ids=['existing'], lines=[
            dict(starting_fen=chess.STARTING_FEN, moves=['e2e4'], annotations=[]),
            dict(starting_fen=chess.STARTING_FEN, moves=['d2d4'], annotations=[dict(
                fen_key='fen', comment='text', arrows=[dict(color='G')], squares=[])]),
        ])
    # The pre-existing outer Games refresh decorator is unchanged and outside this split.
    result = application.admit_pgn_import.__wrapped__(database, payload)
    assert events[:3] == ['clock', ('native', 1), ('native', 2)]
    assert events.count('uuid') == (0 if existing_repertoire else 1)
    assert database.native_calls[3][1] == ('repertoire', 'book', 'book.pgn', 'timestamp')
    assert batch.calls[0][1][0][0] == 'old-line'
    assert batch.calls[1][1] == [('old-line', 3), (batch.calls[0][1][1][0], 3)]
    assert batch.calls[2][1] == [('repertoire', 'fen', 'text', '[{"color": "G"}]', '[]', 'timestamp')]
    assert events.index('cursor_exit') < events.index('certify') < events.index(('native', 6))
    assert events[-4:] == ['invalidate', 'day', ('graph', (database, 'repertoire', '2026-10-20')), 'summary']
    assert events.count('iterate') == 3
    assert result['cards_created'] == 1 and result['decision_cards_created'] == 1
    assert result['prefix_cards_created'] == 0 and result['shared_prefixes_reused'] == 1
    assert result['integrity'] == {'state': 'pending'} and result['graph_state'] == 'refreshing'


@pytest.mark.parametrize('receipt,outcome,status', [
    (('imports.pgn.admit', 'queued', None), 'discarded', None),
    (('imports.pgn.admit', 'complete', '{"repertoire_id":"saved"}'), 'already_complete', None),
    (('other.command', 'failed', None), None, 409),
])
def test_discard_keeps_same_raw_lock_insert_read_before_original_decision(receipt, outcome, status):
    events = []
    database = Borrowed(events, replies=[Rows(), Rows(), Rows(value=receipt, events=events), Rows()])
    if status:
        with pytest.raises(application.HTTPException) as error:
            application.discard_pgn_import(database, {'operation_id': 'operation'})
        assert error.value.status_code == status
    else:
        result = application.discard_pgn_import(database, {'operation_id': 'operation'})
        assert result['outcome'] == outcome
        if outcome == 'already_complete':
            assert result['import_result'] == {'repertoire_id': 'saved'}
    expected_writes = 4 if outcome == 'discarded' else 3
    assert events == [('raw', 1), ('raw', 2), ('raw', 3), 'fetchone'] + ([('raw', 4)] if expected_writes == 4 else [])
    assert len(database.raw_calls) == expected_writes
    assert database.raw_calls[0][1] == ('operation',)
    assert database.raw_calls[1][1][:2] == ('operation', 'discarded:operation')
    assert json.loads(database.raw_calls[1][1][2]) == dict(
        code='import_discarded', message='PGN import discarded.', status_code=410)
    if outcome == 'discarded':
        assert database.raw_calls[-1][1] == (database.raw_calls[1][1][2], 'operation')


def test_discard_rejects_invalid_identity_before_any_storage():
    database = Borrowed([])
    with pytest.raises(application.HTTPException) as error:
        application.discard_pgn_import(database, {'operation_id': ''})
    assert error.value.status_code == 422
    assert database.raw_calls == []
