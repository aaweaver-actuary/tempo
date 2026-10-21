"""Card edit/archive fake borrowed-transaction boundaries; no application startup."""
from datetime import date
from types import ModuleType, SimpleNamespace
import sys

import chess
import pytest

from app import card_commands as application
from tempo_legacy_database.adapters.authored_repertoires.card_edits import storage


class Rows:
    def __init__(self, value=None, rows=(), events=None):
        self.value, self.rows, self.events = value, rows, events
    def fetchone(self):
        if self.events is not None:
            self.events.append('fetchone')
        return self.value
    def __iter__(self):
        if self.events is not None:
            self.events.append('iterate')
        return iter(self.rows)


class Borrowed:
    def __init__(self, events, native=(), compatible=()):
        self.events, self.native, self.compatible = events, iter(native), iter(compatible)
        self.native_calls, self.compatible_calls = [], []
    def execute_native(self, statement, parameters=()):
        self.native_calls.append((statement, parameters))
        self.events.append(('native', len(self.native_calls)))
        return next(self.native)
    def execute(self, statement, parameters=()):
        self.compatible_calls.append((statement, parameters))
        self.events.append(('compatible', len(self.compatible_calls)))
        return next(self.compatible)
    def __getattr__(self, name):
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


def fake_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


@pytest.mark.parametrize('branch', ['same', 'existing', 'new_reset'])
def test_revision_preserves_scope_sorted_locks_history_branch_certification_and_refresh(monkeypatch, branch):
    events = []
    replacement = 'z-source' if branch == 'same' else 'a-replacement'
    old = dict(revision=2, archived=0, superseded_by=None, start_fen=chess.STARTING_FEN,
               moves_json='["e2e4"]', due_date='old-day', interval_days=40,
               fsrs_card_json='old-schedule', state='mature')
    existing = dict(id=replacement, revision=7) if branch == 'existing' else None
    lock_count = 1 if branch == 'same' else 2
    branch_writes = {'same': 1, 'existing': 4, 'new_reset': 3}[branch]
    native = [Rows()] * (1 + lock_count) + [Rows(old), Rows(existing), Rows()]
    native += [Rows()] * (branch_writes + (0 if branch == 'same' else 2))
    native += [Rows(rows=[('rep',)], events=events), Rows()]
    database = Borrowed(events, native, [Rows(rows=[('rep',)], events=events), Rows()])
    def scope(connection, repertoire, fen, moves, *, remember):
        assert connection is database and repertoire == 'rep' and moves == ['d2d4']
        assert remember is False
        events.append('scope')
        return 'validated-route'
    def certify(connection, repertoire, route):
        assert (connection, repertoire, route) == (database, 'rep', 'validated-route')
        events.append('certify')
    fake_module(monkeypatch, 'app.services.canonical_prefix',
                ensure_line_in_scope=scope, certify_admitted_route=certify)
    fake_module(monkeypatch, 'app.card_deletion', require_card_not_deleted=
                lambda connection, identifier: events.append(('not_deleted', identifier)))
    monkeypatch.setattr(application, 'card_id', lambda *args: replacement)
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=
        lambda zone: events.append('clock') or SimpleNamespace(isoformat=lambda: 'timestamp')))
    monkeypatch.setattr(application, 'date', SimpleNamespace(today=
        lambda: events.append('day') or date(2026, 10, 20)))
    columns = ['id', 'due_date', 'interval_days', 'fsrs_card_json', 'state', 'moves_json']
    monkeypatch.setattr(application, 'card_columns',
                        lambda connection: events.append('columns') or columns)
    monkeypatch.setattr(application, '_request_current_integrity_scan',
                        lambda *args: events.append(('refresh', args)))
    result = application.revise_card.__wrapped__(database, dict(card_id='z-source', request=dict(
        starting_fen=chess.STARTING_FEN, moves=['D2D4'], expected_revision=2,
        history_mode='reset' if branch == 'new_reset' else 'preserve')))
    assert events.index('scope') < events.index(('native', 1))
    lock_parameters = [parameters for _, parameters in database.native_calls[1:1 + lock_count]]
    assert lock_parameters == [(f'tempo:card-edit:{identifier}',) for identifier in sorted({'z-source', replacement})]
    assert events.index(('native', 1 + lock_count)) < events.index(('not_deleted', 'z-source'))
    assert events.index(('not_deleted', replacement)) < events.index(('native', 2 + lock_count))
    revision_write = 3 + lock_count
    assert events.index('clock') < events.index(('native', revision_write + 1))
    assert database.native_calls[revision_write][1] == (
        'z-source', 3, chess.STARTING_FEN, '["e2e4"]', result['history_mode'], 'timestamp')
    writes = [parameters for _, parameters in database.native_calls[revision_write + 1:]]
    if branch == 'same':
        assert writes[0] == (chess.STARTING_FEN, '["d2d4"]', None, 3, 'z-source')
    elif branch == 'existing':
        assert writes[:4] == [(replacement,), (replacement, 'z-source'), ('z-source',), (replacement, 'z-source')]
    else:
        assert events.index('clock') < events.index('day') < events.index('columns')
        assert writes[:3] == [(replacement, '2026-10-20', 0, None, 'new', '["d2d4"]'),
                              (replacement, 'z-source'), (replacement, 'z-source')]
    assert database.compatible_calls[-1][1] == ('rep', replacement)
    assert events.index(('compatible', 2)) < events.index('certify') < events.index(('native', len(native) - 1))
    assert events[-2:] == ['day', ('refresh', (database, 'rep', '2026-10-20'))]
    assert result == dict(card_id=replacement, replaced=branch != 'same',
        history_mode='reset' if branch == 'new_reset' else 'preserve', revision=7 if branch == 'existing' else 3)


@pytest.mark.parametrize('publication,task,expected', [
    ((8, 'complete'), None, 'scan'),
    (None, ('running',), None),
    ((8, 'queued'), ('failed',), 'graph'),
])
def test_integrity_refresh_keeps_invalidation_read_and_conditional_callback_order(monkeypatch, publication, task, expected):
    events = []
    database = Borrowed(events, [Rows(publication, events=events), Rows(task, events=events)])
    monkeypatch.setattr(application, 'invalidate_integrity_in_transaction', lambda *args: events.append('invalidate'))
    monkeypatch.setattr(application, 'request_integrity_scan_in_transaction',
                        lambda *args: events.append(('scan', args)))
    monkeypatch.setattr(application, 'request_graph_rebuild_in_transaction',
                        lambda *args: events.append(('graph', args)))
    application._request_current_integrity_scan(database, 'rep', 'day')
    assert events[:3] == ['invalidate', ('native', 1), 'fetchone']
    assert len(database.native_calls) == (1 if expected == 'scan' else 2)
    if expected == 'scan':
        assert events[-1] == ('scan', (database, 'rep', 8, 'day'))
    elif expected == 'graph':
        assert events[-1] == ('graph', (database, 'rep', 'day'))
    else:
        assert events[-1] == 'fetchone'


def test_archive_keeps_lazy_membership_queue_completion_before_refresh_and_summary(monkeypatch):
    events = []
    database = Borrowed(events, [Rows(), Rows(), Rows(('card',)),
        Rows(rows=[('rep',)], events=events), Rows(), Rows()])
    monkeypatch.setattr(application, 'date', SimpleNamespace(today=
        lambda: events.append('day') or date(2026, 10, 20)))
    monkeypatch.setattr(application, '_request_current_integrity_scan',
                        lambda *args: events.append(('refresh', args)))
    monkeypatch.setattr(application, 'integrity_summary',
                        lambda *args: events.append('summary') or dict(status='unchecked'))
    result = application.archive_card.__wrapped__(database, dict(card_id='card'))
    assert database.native_calls[1][1] == ('tempo:card-edit:card',)
    assert events.index('iterate') < events.index(('native', 5))
    assert events[-4:] == [('native', 6), 'day', ('refresh', (database, 'rep', '2026-10-20')), 'summary']
    assert result == dict(archived=True, integrity={'rep': {'status': 'unchecked'}})


def test_replacement_insert_keeps_database_column_order_and_missing_values():
    database = Borrowed([], [Rows()])
    storage.insert_replacement(database, ['state', 'id', 'absent'], dict(id='replacement', state='new'))
    assert database.native_calls == [('INSERT INTO cards(state,id,absent) VALUES(%s,%s,%s)',
                                      ('new', 'replacement', None))]
