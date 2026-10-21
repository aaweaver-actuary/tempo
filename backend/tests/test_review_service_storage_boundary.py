"""Borrowed review/requeue operations preserve application decisions and order."""
from datetime import date
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.services import review_service as application


class Cursor:
    def __init__(self, value=None, rows=(), event=None):
        self.value, self.rows, self.event = value, rows, event
    def fetchone(self):
        return self.value
    def fetchall(self):
        return list(self.rows)
    def __iter__(self):
        if self.event:
            self.event()
        return iter(self.rows)


class Borrowed:
    def __init__(self, replies, events):
        self.replies, self.events = iter(replies), events
    @property
    def execute(self):
        self.events.append('execute_lookup')
        def execute(statement, parameters=()):
            self.events.append(('execute', parameters))
            return next(self.replies)
        return execute
    def __getattr__(self, name):
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


def fake_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_review_rebuild_empty_history_keeps_reset_clock_inside_borrowed_write(monkeypatch):
    events = []
    database = Borrowed([Cursor(rows=[]), Cursor()], events)
    def today():
        events.append('today')
        return date(2026, 10, 20)
    monkeypatch.setattr(application, 'date', SimpleNamespace(today=today))
    application.rebuild_defense_schedule(database, 'card', 7)
    assert events == ['execute_lookup', ('execute', ('card',)),
                      'execute_lookup', 'today', ('execute', ('2026-10-20', 'card'))]


def test_review_rebuild_replays_in_order_then_saves_the_same_schedule(monkeypatch):
    events, calls = [], []
    rows = [dict(rating='correct', reviewed_at='2026-10-19T10:00:00'),
            dict(rating='correct', reviewed_at='2026-10-20T10:00:00')]
    database = Borrowed([Cursor(rows=rows), Cursor()], events)
    def schedule(outcome, **facts):
        calls.append((outcome, facts))
        return SimpleNamespace(due_date=date(2026, 11, 1), interval_days=len(calls),
            fsrs_card_json='state-' + str(len(calls)), first_correct_at='first',
            reinforcement_pending=True, stability=5.0, scheduling_mode='light',
            hard_correct_streak=2, recent_attempts=('correct',))
    def unlock(stability, successful_days, outcomes):
        assert (stability, successful_days, outcomes) == (5.0, 2, ['correct', 'correct'])
        events.append('unlock')
        return True
    monkeypatch.setattr(application, 'schedule_review', schedule)
    monkeypatch.setattr(application, 'unlock_ready', unlock)
    application.rebuild_defense_schedule(database, 'card', 7)
    assert calls[0][1]['interval_days'] == 0 and calls[1][1]['interval_days'] == 1
    assert calls[1][1]['fsrs_card_json'] == 'state-1'
    assert calls[1][1]['review_day'] == date(2026, 10, 20)
    assert events[-3:] == ['unlock', 'execute_lookup', ('execute',
        ('2026-11-01', 2, 'state-2', 'first', 1, 5.0, 'mature', 'light', 2, '["correct"]', 'card'))]


@pytest.mark.parametrize('existing', [False, True])
def test_review_requeue_keeps_lazy_cursor_positions_classification_and_receipt(monkeypatch, existing):
    events = []
    replies = [Cursor(value={'id': 7} if existing else None)]
    replies += [Cursor()] if existing else [Cursor(value=(3,)), Cursor(value={'id': 7})]
    replies += [Cursor(rows=[(2,), (3,)], event=lambda: events.append('iterate'))]
    replies += [Cursor(), Cursor(), Cursor(), Cursor()]
    database = Borrowed(replies, events)
    monkeypatch.setattr(application, 'capture_daily_queue_order',
                        lambda connection, day: events.append('capture') or 'receipt')
    monkeypatch.setattr(application, 'preserve_daily_queue_order',
                        lambda connection, day, previous: events.append(('preserve', connection is database, previous)))
    application.ensure_card_queued_after(database, 'card', 1, priority_reason='reason', queue_date='day')
    writes = [event[1] for event in events if isinstance(event, tuple) and event[0] == 'execute']
    assert events[0] == 'capture'
    if existing:
        assert writes[:2] == [('day', 'card'), ('reason', 7)]
    else:
        assert writes[:3] == [('day', 'card'), ('day', 'card'), ('day', 'card', 3, 2_000_000_000, 'gameplay', 'reason')]
    assert writes[-4:] == [(0, 2), (1, 7), (2, 3), (7,)]
    assert events.index('iterate') < events.index(('execute', (0, 2)))
    assert events[-1] == ('preserve', True, 'receipt')


def test_review_protected_insert_keeps_nested_position_read_and_positional_order(monkeypatch):
    events = []
    database = Borrowed([Cursor(), Cursor(value=(1,)), Cursor(value=(9,)),
                         Cursor(value={'id': 7}), Cursor(), Cursor(), Cursor()], events)
    rows = [dict(id=2, position=0), dict(id=7, position=9)]
    fake_module(monkeypatch, 'app.services.queue_application',
                presentation_rows=lambda connection, day: rows)
    fake_module(monkeypatch, 'app.services.queue_ordering',
                _priority_queue_order=lambda value: list(reversed(value)))
    application.ensure_card_queued_after(database, 'card', queue_date='day',
                                        protect_presented=True, preserve_order=False)
    parameters = [event[1] for event in events if isinstance(event, tuple)]
    assert parameters == [('day', 'card'), ('day', 'card'), ('day',),
                          ('day', 'card', 1, 9, 'gameplay', None), (0, 7), (9, 2), (7,)]


def test_review_receipt_clock_and_seed_follow_provenance_and_precede_borrowed_write(monkeypatch):
    events = []
    database = Borrowed([Cursor()], events)
    fake_module(monkeypatch, 'app.services.queue_ordering',
                _preserved_queue_fingerprint=lambda *facts, previous: events.append(('fingerprint', facts, previous)) or 'hash')
    monkeypatch.setattr(application, '_queue_order_inputs', lambda connection, day: ('entries', 'saved', 'activation'))
    def now(zone):
        events.append('clock')
        return SimpleNamespace(isoformat=lambda: 'timestamp')
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=now))
    application.preserve_daily_queue_order(database, 'day', previous='before')
    seed = int(hashlib.sha256(b'day').hexdigest()[:15], 16)
    assert events == [('fingerprint', ('entries', 'saved', 'activation'), 'before'),
                      'execute_lookup', 'clock', ('execute', ('day', seed, 'hash', 'timestamp'))]
