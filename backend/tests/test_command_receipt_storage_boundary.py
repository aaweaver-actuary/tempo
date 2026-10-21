"""Receipt driver/context boundary checks without native database or process startup."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app import command_gateway as application
from app.services import queue_refresh_wakeup


class Raw:
    def __init__(self, events, replies):
        self.events, self.replies, self.calls = events, iter(replies), []
    @property
    def execute(self):
        self.events.append('execute_lookup')
        def execute(statement, parameters=()):
            self.calls.append((statement, parameters))
            self.events.append(('execute', len(self.calls)))
            value = next(self.replies, None)
            return SimpleNamespace(fetchone=lambda: value, rowcount=1)
        return execute


class Borrowed:
    def __init__(self, raw, events):
        self.driver, self.events = raw, events
    @property
    def raw(self):
        self.events.append('raw')
        return self.driver
    def __getattr__(self, name):
        raise AssertionError('Unexpected transaction/lifecycle access: ' + name)


def writer(monkeypatch, events, replies):
    raw = Raw(events, replies)
    database = Borrowed(raw, events)
    @contextmanager
    def section(background):
        events.append('enter')
        try:
            yield database
        except BaseException:
            events.append('rollback_exit')
            raise
        else:
            events.append('commit_exit')
        finally:
            events.append('closed')
    monkeypatch.setattr(application, '_writer_connection', section)
    return raw, database


@pytest.mark.parametrize('state', ['pending', 'complete', 'stale'])
def test_receipt_captures_writer_once_and_preserves_fence_savepoint_close_before_wake(monkeypatch, state):
    events = []
    command, payload = 'test.storage.boundary', {'value': 1}
    receipt = (command, application.request_digest(command, payload),
        'executing' if state == 'stale' else state, '{"saved":true}', None, 'obsolete')
    raw, database = writer(monkeypatch, events, [None, None, receipt])
    def prepare(value):
        assert value is payload
        events.append('prepare')
        return 'prepared'
    def handler(connection, value):
        assert connection is database and value == 'prepared'
        events.append('handler')
        queue_refresh_wakeup.mark_queue_refresh_requested()
        return {'saved': True}
    monkeypatch.setitem(application._preparers, command, prepare)
    monkeypatch.setitem(application._handlers, command, handler)
    monkeypatch.setattr(queue_refresh_wakeup, 'wake_queue_refresh', lambda: events.append('wake'))
    result = application.execute_command('operation', command, payload,
        attempt_token='current' if state == 'stale' else None)
    assert events[:3] == ['prepare', 'enter', 'raw'] and events.count('raw') == 1
    assert len(raw.calls) == (6 if state == 'pending' else 3)
    if state == 'pending':
        assert events.index(('execute', 4)) < events.index('handler') < events.index(('execute', 5))
        assert events[-3:] == ['commit_exit', 'closed', 'wake']
        assert json.loads(raw.calls[-1][1][0]) == {'saved': True}
    else:
        assert 'handler' not in events and 'wake' not in events
        assert events[-2:] == ['commit_exit', 'closed']
    assert result == (None if state == 'stale' else {'saved': True})


@pytest.mark.parametrize('retryable', [False, True])
def test_retry_keeps_fenced_read_clock_error_serialization_and_write_order(monkeypatch, retryable):
    events = []
    raw, _ = writer(monkeypatch, events, [(3,)])
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    def clock(zone):
        events.append('clock')
        return now
    class Failure(Exception):
        def __str__(self):
            events.append('error_text')
            return 'failure'
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=clock))
    assert application.record_operation_retry('operation', 'token', Failure(),
        retryable=retryable, background=True) == ((False, 2.0) if retryable else (True, None))
    text = events.index('error_text')
    if retryable:
        assert events[text - 2:text] == ['execute_lookup', 'clock']
    else:
        assert events[text - 1] == 'execute_lookup' and 'clock' not in events
    assert events[text + 1] == ('execute', 2)
    parameters = raw.calls[-1][1]
    assert parameters == ('retrying' if retryable else 'blocked',
        now + timedelta(seconds=2) if retryable else None,
        '{"class": "Failure", "message": "failure"}', 'operation', 'token')
    assert events.count('raw') == 2 and events[-2:] == ['commit_exit', 'closed']


def test_recovery_keeps_sorted_filters_for_each_due_stream_and_reserves_before_decode(monkeypatch):
    events = []
    raw, _ = writer(monkeypatch, events, [('operation', 'command', '{"value":1}', True)])
    result = application.claim_recoverable_operation(
        allowed_background_commands={'b', 'a'}, paused_commands={'paused'})
    assert raw.calls[0][1] == (['a', 'b'], ['paused']) * 3
    assert raw.calls[1][1] == ('operation',)
    assert result == dict(operation_id='operation', command_name='command', payload={'value': 1}, background=True)
    assert events[-2:] == ['commit_exit', 'closed']


@pytest.mark.parametrize('state', ['complete', 'failed'])
def test_terminal_attempt_does_not_restore_removed_payload_or_consume_clock(monkeypatch, state):
    events = []
    payload = {'source': 'original'}
    receipt = ('imports.pgn.admit', application.request_digest('imports.pgn.admit', payload),
               state, None, None, None, 5, 3, 1)
    raw, _ = writer(monkeypatch, events, [None, receipt])
    monkeypatch.setattr(application, 'datetime', SimpleNamespace(now=
        lambda *args: pytest.fail('Terminal delivery consumed a new attempt clock')))
    assert application.record_operation_attempt('operation', 'imports.pgn.admit', payload,
                                                background=True) == (False, payload, None, 3)
    assert len(raw.calls) == 2 and events.count('raw') == 1
    assert events[-2:] == ['commit_exit', 'closed']
