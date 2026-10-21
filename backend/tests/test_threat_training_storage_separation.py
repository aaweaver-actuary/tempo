"""Defensive exercise decisions and receipts retain fixed storage boundaries."""
import ast
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.transport.defense import DefenseRecognitionRequest
from app.services import threat_training as training
from test_defensive_threat_grading import exercise

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
DAY = NOW.date()


def candidate():
    item = exercise()
    return dict(id='candidate', finding_id='finding', incident_id='incident', game_id='game',
                card_id='card', analysis_version=1, current_analysis_version=1, superseded_at=None,
                exercise_revision=1, validation_state='engine_supported', approved_at='approved',
                dismissed_at=None, paused_at=None, source_fingerprint='source',
                evidence_json=json.dumps(dict(seed=asdict(item.seed), anchor=asdict(item.anchor))),
                validation_json=json.dumps(dict(refutation_uci=list(item.refutation_uci))),
                policy_json=json.dumps(asdict(item.policy)))


def scopes(monkeypatch, events):
    database = object()
    def section(name):
        @contextmanager
        def scope(**_options):
            events.append((name, 'open'))
            try:
                yield database
            finally:
                events.append((name, 'closed'))
        return scope
    monkeypatch.setattr(training, 'background_read_connection', section('read'))
    monkeypatch.setattr(training, 'read_connection', section('read'))
    monkeypatch.setattr(training, 'connection', section('write'))
    monkeypatch.setattr(training, '_now', lambda: events.append(('clock',)) or NOW.isoformat())
    monkeypatch.setattr(training, 'datetime', SimpleNamespace(now=lambda *_: events.append(('datetime',)) or NOW))
    monkeypatch.setattr(training, 'date', SimpleNamespace(today=lambda: events.append(('date',)) or DAY))
    return database


def recognition():
    geometry = exercise().seed.geometry
    return DefenseRecognitionRequest(attempt_id='attempt', queue_entry_id=4, exercise_revision=1,
        rubric_version=3, dangerous_piece_square=geometry.knight_from, destination_square=geometry.knight_to,
        king_square=geometry.king.square, major_square=geometry.major.square, consequence='checking_fork')


@pytest.mark.parametrize('operation,args', [('dismiss_defense_candidate', ()), ('pause_defense_candidate', (True,))])
def test_approved_candidate_rejects_manual_mutation_before_clock_or_write(monkeypatch, operation, args):
    events = []
    database = scopes(monkeypatch, events)
    monkeypatch.setattr(training.storage, 'candidate', lambda *_: candidate())
    with pytest.raises(ValueError, match='Approved|already in training'):
        getattr(training, operation)('candidate', *args, write_database=database)
    assert events == []


def test_existing_card_approval_preserves_mutation_refresh_wrapper_and_borrowed_database(monkeypatch):
    from app import card_deletion
    from app.services import repertoire_game_refresh
    events = []
    database = scopes(monkeypatch, events)
    @contextmanager
    def refresh(connection):
        assert connection is database
        events.append(('refresh', 'open'))
        yield
        events.append(('refresh', 'closed'))
    monkeypatch.setattr(repertoire_game_refresh, 'refreshing_game_scope', refresh)
    monkeypatch.setattr(card_deletion, 'require_card_not_deleted',
                        lambda *args: events.append(('not deleted', *args)))
    monkeypatch.setattr(training.storage, 'clear_card_validation',
                        lambda *args: events.append(('card', *args)))
    monkeypatch.setattr(training.storage, 'retain_approval',
                        lambda *args: events.append(('approval', *args)))
    assert training._approve_in_transaction(database, candidate(), reports_verified=True) == 'card'
    assert [event[0] for event in events] == ['refresh', 'not deleted', 'card', 'clock', 'clock', 'approval', 'refresh']
    assert events[0] == ('refresh', 'open') and events[-1] == ('refresh', 'closed')
    assert all(event[1] is database for event in events if event[0] in {'card', 'approval'})


@pytest.mark.parametrize('matching', [False, True])
def test_native_completed_attempt_locks_card_then_day_before_replay_without_rescheduling(monkeypatch, matching):
    events = []
    database = scopes(monkeypatch, events)
    monkeypatch.setattr(training.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(training.storage, 'lock_card', lambda *args: events.append(('card lock', *args)))
    monkeypatch.setattr(training.storage, 'queue_day', lambda *_: events.append(('day fact',)) or (DAY.isoformat(),))
    monkeypatch.setattr(training, 'lock_queue_date_for_position', lambda *args: events.append(('day lock', *args)))
    monkeypatch.setattr(training.storage, 'candidate', lambda *_: events.append(('candidate',)) or candidate())
    prior = dict(candidate_id='candidate' if matching else 'different', move_uci='e1f2',
                 queue_entry_id=4, grade_json='{"status":"correct"}')
    monkeypatch.setattr(training.storage, 'attempt', lambda *_: events.append(('receipt',)) or prior)
    arguments = dict(attempt_id='attempt', exercise_revision=1, queue_entry_id=4,
                     move_uci='e1f2', response=dict(status='correct'), light_first_interval_days=2)
    if matching:
        assert training._complete_defense_in_transaction(database, candidate(), **arguments) == dict(status='correct', idempotent=True)
    else:
        with pytest.raises(ValueError, match='different submission'):
            training._complete_defense_in_transaction(database, candidate(), **arguments)
    assert [event[0] for event in events] == ['card lock', 'day fact', 'day lock', 'candidate', 'receipt']


@pytest.mark.parametrize('requeue,after', [(False, None), (True, None), (True, 2)])
def test_completion_preserves_review_queue_requeue_order_and_receipt_last(monkeypatch, requeue, after):
    events = []
    database = scopes(monkeypatch, events)
    monkeypatch.setattr(training.postgres_store, 'configured', lambda: False)
    monkeypatch.setattr(training.storage, 'attempt', lambda *_: None)
    monkeypatch.setattr(training.storage, 'queued_entry', lambda *_: dict(id=4))
    monkeypatch.setattr(training, 'capture_daily_queue_order', lambda *_: events.append(('capture',)) or 'order')
    scheduling = dict(requeue_today=requeue, requeue_after_cards=after)
    monkeypatch.setattr(training, 'apply_scheduling_review', lambda *args, **kwargs: events.append(('review', args, kwargs)) or scheduling)
    monkeypatch.setattr(training.storage, 'complete_queue_entry', lambda *args: events.append(('complete', *args)))
    monkeypatch.setattr(training.storage, 'next_cycle', lambda *_: events.append(('cycle',)) or (3,))
    monkeypatch.setattr(training.storage, 'final_position', lambda *_: events.append(('position',)) or (9,))
    monkeypatch.setattr(training.storage, 'append_reinforcement', lambda *args: events.append(('append', *args)))
    monkeypatch.setattr(training, 'ensure_card_queued_after', lambda *args, **kwargs: events.append(('canonical requeue', args, kwargs)))
    monkeypatch.setattr(training, 'preserve_daily_queue_order', lambda *args, **kwargs: events.append(('preserve', args, kwargs)))
    monkeypatch.setattr(training.storage, 'record_attempt', lambda *args: events.append(('receipt', *args)))
    result = training._complete_defense_in_transaction(database, candidate(), attempt_id='attempt',
        exercise_revision=1, queue_entry_id=4, move_uci='e1f2', response=dict(status='correct'),
        light_first_interval_days=2)
    assert result == dict(status='correct', idempotent=False)
    choices = [event[0] for event in events if event[0] not in {'date', 'datetime'}]
    assert choices == ['capture', 'review', 'complete'] + (
        ['cycle', 'position', 'append'] if requeue and after is None else
        ['canonical requeue'] if requeue else []) + ['preserve', 'receipt']
    review = next(event for event in events if event[0] == 'review')
    assert review[2]['source_ref'] == 'defense:attempt' and review[2]['guided'] is False
    assert events[-1][1] is database and events[-1][-1] == NOW.isoformat()


@pytest.mark.parametrize('same_answer', [False, True])
def test_recognition_read_replay_returns_or_rejects_without_entering_writer(monkeypatch, same_answer):
    events = []
    scopes(monkeypatch, events)
    request = recognition()
    monkeypatch.setattr(training.storage, 'candidate', lambda *_: candidate())
    monkeypatch.setattr(training, '_exercise_from_rows', lambda *_: exercise())
    monkeypatch.setattr(training.storage, 'recognition_submission', lambda *_: dict(candidate_id='candidate',
        queue_entry_id=4, exercise_revision=1, answer_json=json.dumps(request.model_dump(), sort_keys=True) if same_answer else 'changed'))
    monkeypatch.setattr(training.storage, 'completed_grade', lambda *_: dict(grade_json='{"status":"correct"}'))
    if same_answer:
        assert training.submit_defense_recognition('candidate', request) == dict(status='correct', idempotent=True)
    else:
        with pytest.raises(ValueError, match='different answer'):
            training.submit_defense_recognition('candidate', request)
    assert events == [('read', 'open'), ('read', 'closed')]


@pytest.mark.parametrize('current_revision', [1, 2])
def test_new_move_analysis_is_computed_after_read_close_and_rechecks_revision_before_persistence(monkeypatch, current_revision):
    from app.services import defensive_analysis
    events = []
    scopes(monkeypatch, events)
    candidates = iter([candidate(), {**candidate(), 'exercise_revision': current_revision}])
    monkeypatch.setattr(training.storage, 'candidate', lambda *_: next(candidates))
    monkeypatch.setattr(training.storage, 'attempt', lambda *_: None)
    monkeypatch.setattr(training, '_exercise_from_rows', lambda *_: exercise())
    monkeypatch.setattr(training.storage, 'source_game_url', lambda *_: dict(game_url='source'))
    original = training.grade_defense_move
    def grade(*args):
        assert events[-1] == ('read', 'closed')
        events.append(('grade',))
        return original(*args)
    monkeypatch.setattr(training, 'grade_defense_move', grade)
    monkeypatch.setattr(training.storage, 'retain_analysis_request', lambda *args: events.append(('request', *args)))
    monkeypatch.setattr(training.storage, 'retain_attempt_request', lambda *args: events.append(('relation', *args)))
    monkeypatch.setattr(defensive_analysis, 'analysis_enabled', lambda *_: events.append(('enabled',)) or False)
    arguments = dict(attempt_id='attempt', exercise_revision=1, queue_entry_id=4, move_uci='a2a4', light_first_interval_days=2)
    if current_revision == 1:
        result = training.submit_defense_attempt('candidate', **arguments)
        assert result['status'] == 'needs_analysis' and 'paused' in result['diagnostic']
        assert [event[0] for event in events if event[0] in {'request', 'relation', 'enabled'}] == ['request', 'relation', 'enabled']
    else:
        with pytest.raises(ValueError, match='Exercise revision changed'):
            training.submit_defense_attempt('candidate', **arguments)
        assert not any(event[0] in {'clock', 'request', 'relation', 'enabled'} for event in events)
    assert events.index(('read', 'closed')) < events.index(('grade',)) < events.index(('write', 'open'))


def test_admission_wrong_day_never_opens_read_or_write_scope(monkeypatch):
    events = []
    scopes(monkeypatch, events)
    assert training.execute_defense_admission_slice(dict(payload=dict(queue_date='1900-01-01'))) is False
    assert events == [('date',)]


def test_training_contains_no_sql_driver_calls_and_root_operations_no_application_imports():
    tree = ast.parse(Path(training.__file__).read_text())
    forbidden = {'execute', 'execute_native', 'cursor', 'fetchone', 'fetchall', 'fetchmany', 'rowcount', 'commit', 'rollback'}
    assert not any(isinstance(node, ast.Attribute) and node.attr in forbidden for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and any(word in node.value.upper() for word in ('SELECT ', 'INSERT INTO ', 'UPDATE ', 'DELETE FROM '))
                   for node in ast.walk(tree))
    for node in ast.walk(ast.parse(Path(training.storage.__file__).read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or '').startswith('app')
        elif isinstance(node, ast.Import):
            assert all(not name.name.startswith('app') for name in node.names)
