"""Italian issuance: immutable receipt incarnations, pending wire and retry fences.

Storage/transport doubles prove selection policy only. Real worker/PostgreSQL
claim and immutable-plan evidence belongs to the position owner native suite.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app import command_gateway as gateway
from app.contracts.command_identity import request_digest
from app.contracts.queue_issuance import (
    issuance_operation_id, issuance_generation, MAX_GENERATION, IMMUTABLE_CONFLICTS,
)

PAYLOAD = {'queue_date': '2026-11-03', 'entry_id': 17, 'card_id': 'synthetic-card', 'expected_revision': 2}


@pytest.fixture
def latest(monkeypatch):
    row = [None]
    calls = []
    @contextmanager
    def reader():
        yield object()
    def read(database, first, last):
        calls.append((first, last))
        return row[0]
    monkeypatch.setattr(gateway.storage, 'foreground_receipt_reader', reader)
    monkeypatch.setattr(gateway.storage, 'latest_queue_issuance', read)
    return row, calls


def receipt(state='complete', result=None, generation=0, seconds_ago=2):
    return (issuance_operation_id(PAYLOAD, generation), 'queue.issue_head',
            request_digest('queue.issue_head', PAYLOAD), state,
            json.dumps(result if result is not None else {'issued': False}),
            datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))


def test_italian_initial_arrivals_choose_same_exact_family_without_receipt(latest):
    row, calls = latest
    first = gateway.select_queue_issuance_operation(PAYLOAD)
    assert first == gateway.select_queue_issuance_operation(dict(reversed(list(PAYLOAD.items()))))
    assert first == issuance_operation_id(PAYLOAD, 0)
    assert len(first) <= 128
    assert calls == [(first, issuance_operation_id(PAYLOAD, MAX_GENERATION))] * 2
    for key, value in [('entry_id', 18), ('expected_revision', 3), ('card_id', 'different'), ('queue_date', '2026-11-04')]:
        assert issuance_operation_id({**PAYLOAD, key: value}) != first


@pytest.mark.parametrize('state', ['queued', 'executing', 'retrying', 'pending', 'blocked', 'failed'])
def test_italian_live_or_failed_receipt_keeps_identity_not_new_preparation(latest, state):
    row, _ = latest
    row[0] = receipt(state=state, generation=4)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 4)


@pytest.mark.parametrize('hold', [
    'position_card_busy', 'position_attempt_busy', 'position_source_graph_busy',
    'position_owner_busy', 'position_original_admission_unavailable',
    'position_due_target_unavailable', 'position_owner_paused',
    'position_owner_source_changed', 'position_admission_scope_ambiguous',
])
def test_italian_completed_hold_allows_one_throttled_successor_on_same_original_head(latest, hold):
    row, _ = latest
    result = {'issued': False, 'position_study_mode': 'position', 'position_study_hold': hold}
    row[0] = receipt(result=result, generation=5, seconds_ago=0)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 5)
    row[0] = receipt(result=result, generation=5)
    original = row[0]
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 6)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 6)
    assert row[0] == original  # old completed receipt/history is never reset
    row[0] = receipt(state='executing', generation=6)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 6)


@pytest.mark.parametrize('result', [
    {'issued': False}, {'issued': False, 'reason': 'study_feedback_preparation_pending'},
])
def test_italian_other_completed_business_outcomes_remain_retryable(latest, result):
    row, _ = latest
    row[0] = receipt(result=result)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 1)


@pytest.mark.parametrize('hold', sorted(IMMUTABLE_CONFLICTS))
def test_italian_genuine_immutable_conflict_never_rotates_or_overwrites_receipt(latest, hold):
    row, _ = latest
    row[0] = receipt(result={'issued': False, 'position_study_hold': hold})
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD)


def test_italian_success_receipt_does_not_create_another_command(latest):
    row, _ = latest
    row[0] = receipt(result={'issued': True}, generation=9)
    assert gateway.select_queue_issuance_operation(PAYLOAD) == issuance_operation_id(PAYLOAD, 9)


def test_italian_selector_rejects_conflicting_receipt_hash_and_generation_overflow(latest):
    row, _ = latest
    bad = list(receipt()); bad[2] = 'different'
    row[0] = bad
    with pytest.raises(gateway.CommandConflict):
        gateway.select_queue_issuance_operation(PAYLOAD)
    row[0] = receipt(generation=MAX_GENERATION)
    with pytest.raises(ValueError, match='exhausted'):
        gateway.select_queue_issuance_operation(PAYLOAD)
    for invalid in [-1, True, 1.0]:
        with pytest.raises(ValueError):
            issuance_operation_id(PAYLOAD, invalid)
    with pytest.raises(ValueError):
        issuance_generation('other-family:000000000000', PAYLOAD)


def test_italian_dispatch_pending_keeps_family_handle_and_location(monkeypatch):
    from app import command_dispatch as dispatch
    sent = []
    operation_id = issuance_operation_id(PAYLOAD)
    def send(name, **options):
        sent.append((name, options))
        return SimpleNamespace(get=lambda **options: None)
    monkeypatch.setattr(dispatch.celery_app, 'send_task', send)
    monkeypatch.setattr(gateway, 'select_queue_issuance_operation', lambda payload: operation_id)
    monkeypatch.setattr(dispatch, 'read_operation', lambda *args, **options: {'state': 'executing'})
    response = dispatch.dispatch_command('queue.issue_head', PAYLOAD, idempotency_key=None)
    assert response.status_code == 202
    assert json.loads(response.body) == {'operation_id': operation_id, 'state': 'executing'}
    assert response.headers['location'] == f'/api/operations/{operation_id}'
    assert response.headers['retry-after'] == '1'
    assert sent[0][1]['args'] == [operation_id, 'queue.issue_head', PAYLOAD]


def test_queue_prepared_propagates_pending_response_without_queue_envelope(monkeypatch):
    from fastapi.responses import JSONResponse
    from app import main
    pending = JSONResponse(status_code=202, content={'operation_id': 'synthetic', 'state': 'queued'})
    monkeypatch.setattr(main, '_queue_payload', lambda **options: pending)
    assert main.prepared_queue() is pending
    assert main.queue_today() is pending


@pytest.mark.parametrize('result', [
    {'issued': True, 'position_study_mode': 'position', 'position_presentation': {'stale': True}},
    {'issued': False, 'position_study_mode': 'position', 'position_study_hold': 'position_attempt_payload_conflict'},
])
def test_italian_http_uses_authoritative_plan_or_preserves_genuine_conflict(monkeypatch, result):
    from app import main, command_dispatch
    from app.services import queue_next_card
    from fastapi import HTTPException
    @contextmanager
    def reader():
        yield object()
    monkeypatch.setattr(main, 'read_connection', reader)
    monkeypatch.setattr(main.queue_projection_storage, 'issued_head', lambda *args: None)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda *args, **options: result)
    prepared = []
    def replay(payload):
        prepared.append(payload)
        return queue_next_card.PreparedQueueHead(payload, 'replay', SimpleNamespace(to_wire=lambda: {'attempt_id': 'original', 'plan_id': 'immutable'}))
    monkeypatch.setattr(queue_next_card, 'prepare_queue_head', replay)
    card = {'id': PAYLOAD['card_id'], 'queue_entry_id': PAYLOAD['entry_id'], 'revision': PAYLOAD['expected_revision']}
    if result['issued']:
        response = main._issue_returned_queue_head(PAYLOAD['queue_date'], card)
        assert response['position_presentation'] == {'attempt_id': 'original', 'plan_id': 'immutable'}
        assert prepared == [PAYLOAD]
    else:
        with pytest.raises(HTTPException) as failure:
            main._issue_returned_queue_head(PAYLOAD['queue_date'], card)
        assert failure.value.status_code == 409
        assert failure.value.detail['code'] == 'position_attempt_payload_conflict'
        assert prepared == []


def test_unrelated_dispatch_and_explicit_queue_keys_do_not_use_family_selector(monkeypatch):
    from app import command_dispatch as dispatch
    sent = []
    monkeypatch.setattr(gateway, 'select_queue_issuance_operation', lambda *_args: pytest.fail('Unexpected queue family selection'))
    monkeypatch.setattr(dispatch.celery_app, 'send_task', lambda name, **options: sent.append(options) or SimpleNamespace(get=lambda **options: None))
    monkeypatch.setattr(dispatch, 'read_operation', lambda *args, **options: {'state': 'complete', 'response': {'ok': True}})
    assert dispatch.dispatch_command('cards.review', {}, idempotency_key='original-review') == {'ok': True}
    assert dispatch.dispatch_command('queue.issue_head', PAYLOAD, idempotency_key='explicit-key') == {'ok': True}
    assert [options['args'][0] for options in sent] == ['original-review', 'explicit-key']
