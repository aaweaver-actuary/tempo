"""Fake-only deletion storage boundaries without Main, SQLite or service startup."""
from datetime import date
import json
from types import ModuleType, SimpleNamespace
import sys

import chess
import pytest

from app import card_deletion as application


class Rows:
    def __init__(self, value=None, rows=(), events=None):
        self.value, self.rows, self.events = value, rows, events
    def fetchone(self):
        if self.events is not None:
            self.events.append('fetchone')
        return self.value
    def fetchall(self):
        if self.events is not None:
            self.events.append('fetchall')
        return list(self.rows)
    def __iter__(self):
        if self.events is not None:
            self.events.append('iterate')
        return iter(self.rows)


class Compatible:
    def __init__(self, events, replies=()):
        self.events, self.replies = events, iter(replies)
        self.calls, self.batches = [], []
    @property
    def execute(self):
        self.events.append('execute_lookup')
        return self._execute
    def _execute(self, statement, parameters=()):
        self.calls.append((statement, parameters))
        self.events.append(('execute', len(self.calls), parameters))
        return next(self.replies, Rows())
    def executemany(self, statement, parameters):
        self.batches.append((statement, parameters))
        self.events.append(('many', parameters))
    def __getattr__(self, name):
        if name == 'execute_native':
            raise AttributeError(name)
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


class Native(Compatible):
    def execute_native(self, statement, parameters=()):
        return self._execute(statement, parameters)


def module(monkeypatch, name, **attributes):
    value = ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    monkeypatch.setitem(sys.modules, name, value)


def clocks(monkeypatch, events):
    def now(zone):
        events.append('clock')
        return SimpleNamespace(isoformat=lambda: 'timestamp')
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=now))
    monkeypatch.setattr(application, 'date', SimpleNamespace(today=
        lambda: events.append('day') or date(2026, 10, 20)))


@pytest.mark.parametrize('native', [False, True])
def test_cancellation_queries_then_clocks_fetches_and_rechecks_only_exact_matches(monkeypatch, native):
    events = []
    tasks = [dict(id='one', deduplication_key='old', payload_json='null'),
        dict(id='two', deduplication_key='other', payload_json='{"repertoire_id":"old"}'),
        dict(id='three', deduplication_key='other', payload_json=json.dumps({'nested': {'repertoire_id': 'old'}, 'fen': 'old\0position'}))]
    replies = [Rows(rows=tasks, events=events)]
    if native:
        replies += [Rows(dict(deduplication_key='old', payload_json='null'), events=events), Rows(),
                    Rows(dict(deduplication_key='other', payload_json='null'), events=events)]
    database = (Native if native else Compatible)(events, replies)
    clocks(monkeypatch, events)
    application.cancel_repertoire_tasks(database, 'old')
    assert events.index('clock') < events.index('fetchall')
    assert events.index(('execute', 1, ('old', 'old'))) < events.index('clock')
    parameters = [item[1] for item in database.calls]
    assert parameters == ([('old', 'old'), ('one',), ('timestamp', 'timestamp', 'one'), ('two',)] if native else
                          [('old', 'old'), ('timestamp', 'timestamp', 'one'), ('timestamp', 'timestamp', 'two')])
    assert events.count('clock') == 1


def test_purge_preserves_identifier_reads_clock_points_fixed_targets_and_tombstones(monkeypatch):
    events = []
    database = Compatible(events, [Rows(rows=[], events=events), Rows(rows=[], events=events)])
    clocks(monkeypatch, events)
    application.purge_card_data(database, ['card'])
    assert [item[1] for item in database.calls] == [['card'], ['card'], ['card'],
        ('timestamp', 'card'), *([['card']] * 7)]
    assert events[:6] == ['execute_lookup', ('execute', 1, ['card']), 'iterate',
                           'execute_lookup', ('execute', 2, ['card']), 'iterate']
    first_clock = events.index('clock')
    assert events[first_clock - 1] == 'execute_lookup'
    assert events[first_clock + 1] == ('execute', 4, ('timestamp', 'card'))
    second_clock = events.index('clock', first_clock + 1)
    assert events[second_clock + 1] == ('many', [('card', 'timestamp')])
    assert database.batches[0][1] == [('card', 'timestamp')]
    assert events[-1] == ('execute', 11, ['card'])


@pytest.mark.parametrize('policy', ['keep', 'delete'])
def test_repertoire_delete_keeps_shared_owner_retention_policy_and_refresh_after_writes(monkeypatch, policy):
    events = []
    owned = [dict(id='learned', replacement=None, introduced_at='day', first_correct_at=None,
                  has_reviews=True, effective_trained_color=None, start_fen=chess.STARTING_FEN, moves_json='["e2e4"]'),
             dict(id='unlearned', replacement=None, introduced_at=None, first_correct_at=None, has_reviews=False),
             dict(id='shared', replacement='other')]
    replies = [Rows(dict(id='old', is_main=1)), Rows(rows=owned, events=events)]
    replies += [Rows()] * (9 if policy == 'keep' else 5)
    replies += [Rows(('replacement-main',)), Rows()]
    database = Compatible(events, replies)
    clocks(monkeypatch, events)
    monkeypatch.setattr(application, 'cancel_repertoire_tasks', lambda *args: events.append('cancel'))
    monkeypatch.setattr(application, 'purge_card_data', lambda connection, identifiers: events.append(('purge', identifiers)))
    module(monkeypatch, 'app.queue_commands', request_queue_refresh_in_transaction=
           lambda *args: events.append(('queue', args)))
    module(monkeypatch, 'app.services.durable_tasks', enqueue_task_in_transaction=
           lambda *args, **keywords: events.append(('task', args, keywords)))
    result = application.delete_repertoire_data(database, 'old', policy)
    assert events[0] == 'cancel'
    assert ('purge', ['unlearned'] if policy == 'keep' else ['learned', 'unlearned']) in events
    parameters = [item[1] for item in database.calls]
    if policy == 'keep':
        retained = application.RETAINED_REPERTOIRE_ID
        assert parameters[2:6] == [(retained, 'timestamp'), (retained, 'learned'),
                                  ('white', 'learned'), (retained, retained, retained, 'learned')]
        clock = events.index('clock')
        assert events[clock - 1] == 'execute_lookup'
        assert events[clock + 1] == ('execute', 3, (retained, 'timestamp'))
    assert ('other', 'other', application.RETAINED_REPERTOIRE_ID, 'shared') in parameters
    assert parameters[-2:] == [application.SYSTEM_REPERTOIRE_IDS, ('replacement-main',)]
    assert events[-3:] == ['day', ('queue', (database, '2026-10-20')),
        ('task', (database, 'repertoire_game_refresh', 'all', {'after_game_id': ''}), {'priority': 90})]
    assert result == dict(deleted=True, id='old')


def test_permanent_delete_reparents_occurrences_before_graph_purge_and_refresh(monkeypatch):
    class Route(dict):
        def __iter__(self):
            return iter(self.values())
    events = []
    route = Route(repertoire_id='rep', generation=4, line_id='line')
    dependencies = [(0, 'ancestor', None), (1, 'deleted', 'ancestor'), (2, 'child', 'deleted'),
                    (3, 'deleted', 'child'), (4, 'tail', 'deleted')]
    database = Native(events, [Rows(), Rows(dict(revision=2)), Rows(rows=[route]),
                                Rows(rows=[dict(decision_index=index, card_id=card, parent_card_id=parent)
                                           for index, card, parent in dependencies], events=events), Rows()])
    clocks(monkeypatch, events)
    monkeypatch.setattr(application, 'purge_card_data', lambda connection, identifiers: events.append(('purge', identifiers)))
    module(monkeypatch, 'app.queue_commands', request_queue_refresh_in_transaction=
           lambda *args: events.append(('queue', args)))
    assert application.permanent_delete_card(database, 'deleted', 2) == dict(deleted=True, card_id='deleted')
    assert database.calls[0][1] == ('tempo:card-edit:deleted',)
    assert database.calls[3][1] == ('rep', 4, 'line')
    assert database.batches[0][1] == [('ancestor', 'rep', 4, 'line', 2), ('child', 'rep', 4, 'line', 4)]
    assert events.index(('many', database.batches[0][1])) < events.index(('execute', 5, (['deleted'],)))
    assert events[-3:] == [('purge', ['deleted']), 'day', ('queue', (database, '2026-10-20'))]


def test_permanent_delete_stale_revision_stops_after_lock_and_read():
    events = []
    database = Native(events, [Rows(), Rows(dict(revision=3))])
    with pytest.raises(application.HTTPException) as error:
        application.permanent_delete_card(database, 'card', 2)
    assert error.value.status_code == 409
    assert len(database.calls) == 2 and database.batches == []
