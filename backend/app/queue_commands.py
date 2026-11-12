"""Foreground queue edits with PostgreSQL row locks and durable receipts."""
from __future__ import annotations
from datetime import date
import json
from typing import Any
from fastapi import HTTPException
from .services import queue_application
from .services.queue_ordering import PRESENTATION_VERSION
from .command_gateway import register_command
from app.contracts.review_conflicts import ReviewConflict
from tempo_storage.postgres import PostgresConnection
from tempo_storage.queue_position_lock import lock_queue_date_for_position
from .services.durable_tasks import enqueue_task_in_transaction
from .study.queue_order import capture_daily_queue_order, preserve_daily_queue_order
_QUEUE_REFRESH_PROJECTION_SQL = queue_application._QUEUE_REFRESH_PROJECTION_SQL
from tempo_storage.commands import queue as queue_storage
from tempo_storage.commands.queue import _lock_attempt_card
_AVAILABLE_ATTEMPT_SQL = queue_application._AVAILABLE_ATTEMPT_SQL

def available_attempt_sql() -> str:
    return queue_application.available_attempt_sql()

def validate_available_attempt(database, entry_id: int, card_id=None, expected_revision=None, *, current_day=None) -> None:
    """A retained, identified attempt remains operable after queue promotion."""
    identified = card_id is not None and expected_revision is not None
    read_available = queue_storage.prepare_available_attempt(database, identified, entry_id, queue_application.MISS_REASON)
    day = (queue_storage.issued_origin_day(database, entry_id, card_id, expected_revision) if identified else None) or current_day or current_study_day(database)
    available = read_available(day)
    if not available or available['id'] != entry_id:
        raise ReviewConflict('queue_attempt_inactive', 'This queue attempt is no longer active')
    from .queue_attempt_origins import validate_failure_marker
    validate_failure_marker(database, entry_id, card_id, expected_revision)
    if identified:
        read_head = queue_storage.prepare_queue_head(database, queue_application.MISS_REASON)
        head = read_head(day)
        issued_origin = queue_storage.issued_origin(database, entry_id, card_id, expected_revision)
        if (not head or head['id'] != entry_id) and (not issued_origin):
            raise ReviewConflict('queue_attempt_inactive', 'This queue attempt was never issued as the active attempt')

def current_study_day(database, *, now=None):
    from .services.study_calendar import capture_study_calendar
    return capture_study_calendar(database, now=now).day.isoformat()

def issue_queue_head(database, payload: dict[str, Any]) -> dict[str, Any]:
    """Commit one head's issuance before returning it; never stamp prefetch."""
    from .services.queue_next_card import PreparedQueueHead
    from .services.background_runtime import training_timing_stage
    prepared = payload if isinstance(payload, PreparedQueueHead) else PreparedQueueHead(payload, 'legacy')
    payload = prepared.payload
    entry_id = int(payload['entry_id'])
    current_day = current_study_day(database)
    if payload['queue_date'] != current_day:
        return {'issued': False}
    position_plan = None
    if prepared.mode == 'hold':
        return {'issued': False, 'position_study_mode': 'position', 'position_presentation': None, 'position_study_hold': prepared.reason}
    if prepared.mode == 'position':
        from tempo_evidence_math.position_learning import Hold
        from tempo_storage.authored_repertoires.position_study_source import fence_position_source
        from app.study.position_learning.issuance import issue_in_borrowed_transaction
        with training_timing_stage('position_source_fence'):
            held = fence_position_source(database, prepared.proposal.source)
        if held is not None:
            return {'issued': False, 'position_study_mode': 'position', 'position_presentation': None, 'position_study_hold': held.reason}
        with training_timing_stage('position_issue_apply'):
            position_plan = issue_in_borrowed_transaction(database, prepared.proposal)
        if isinstance(position_plan, Hold):
            return {'issued': False, 'position_study_mode': 'position', 'position_presentation': None, 'position_study_hold': position_plan.reason}
    elif prepared.mode == 'replay':
        position_plan = prepared.proposal
    else:
        from tempo_storage.study.position_owner_epoch import owner_mode
        owners = queue_storage.issuance_owner_repertoires(database, entry_id, payload['card_id'], payload['expected_revision'])
        for row in owners:
            decision = owner_mode(database, row['repertoire_id'], None)
            if decision.disposition != 'legacy':
                return {'issued': False, 'position_study_mode': 'position', 'position_presentation': None, 'position_study_hold': decision.reason}
    _lock_attempt_card(database, entry_id)
    lock_queue_date_for_position(database, payload['queue_date'])
    if queue_storage.retired_issuance_target(database, entry_id, payload['card_id'], payload['expected_revision']) and (not queue_storage.issued_origin(database, entry_id, payload['card_id'], payload['expected_revision'])):
        if position_plan is not None:
            raise ReviewConflict('position_head_changed', 'Position head changed during issuance', retryable=True)
        return {'issued': False, 'reason': 'retired_queue_target'}
    read_head = queue_storage.prepare_queue_head(database, queue_application.MISS_REASON)
    head = read_head(payload['queue_date'])
    if not head or head['id'] != entry_id:
        if position_plan is not None:
            raise ReviewConflict('position_head_changed', 'Position head changed during issuance', retryable=True)
        return {'issued': False}
    try:
        validate_available_attempt(database, entry_id, payload['card_id'], payload['expected_revision'], current_day=current_day)
    except ReviewConflict:
        if position_plan is not None:
            raise ReviewConflict('position_head_changed', 'Position head changed during issuance', retryable=True)
        return {'issued': False}
    changed = queue_storage.stamp_issued_head(database, entry_id, payload['card_id'], payload['expected_revision'])
    if not changed and position_plan is not None:
        raise ReviewConflict('position_head_changed', 'Position head changed during issuance', retryable=True)
    if position_plan is not None:
        return {'issued': bool(changed), 'position_study_mode': 'position', 'position_presentation': position_plan.to_wire(), 'position_study_hold': None}
    return {'issued': bool(changed)}

def mark_attempt_failed(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    entry_id = int(payload['entry_id'])
    _lock_attempt_card(database, entry_id)
    validate_available_attempt(database, entry_id, payload.get('card_id'), payload.get('expected_revision'))
    changed = queue_storage.mark_attempt_failed(database, entry_id)
    if not changed:
        raise ReviewConflict('queue_attempt_inactive', 'This queue attempt is no longer active')
    return {'attempt_failed': True}

def bury_queue_entry(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    entry_id = int(payload['entry_id'])
    queue_date = current_study_day(database)
    _lock_attempt_card(database, entry_id)
    original_day = queue_storage.issued_origin_day(database, entry_id, payload['card_id'], payload['expected_revision']) if payload.get('card_id') is not None and payload.get('expected_revision') is not None else None
    for day in sorted({queue_date, original_day or queue_date}):
        lock_queue_date_for_position(database, day)
    validate_available_attempt(database, entry_id, payload.get('card_id'), payload.get('expected_revision'), current_day=queue_date)
    previous_order = capture_daily_queue_order(database, queue_date)
    queue_storage.bury_attempt_card(database, queue_date, entry_id)
    preserve_daily_queue_order(database, queue_date, previous=previous_order)
    if original_day and original_day != queue_date:
        preserve_daily_queue_order(database, original_day)
    return {'buried': True, 'queue_entry_id': entry_id}

def ensure_current_queue(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Request one durable queue refresh from the foreground Celery worker."""
    try:
        queue_date = date.fromisoformat(str(payload['queue_date'])).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, 'A valid queue_date is required') from error
    active_task = queue_storage.lock_current_queue_task(database)
    projection = queue_storage.current_projection(database, queue_date, active_task)
    if projection and projection['state'] == 'ready' and (not projection['refresh_pending']) and (not projection['has_queued_entries'] or (projection['presentation_membership_hash'] or '').startswith(PRESENTATION_VERSION + ':')):
        return {'queue_date': queue_date, 'refresh_pending': False}
    if active_task and active_task['state'] in {'queued', 'leased', 'retrying'} and (json.loads(active_task['payload_json']).get('queue_date') == queue_date):
        queue_storage.mark_active_refresh(database, queue_date, active_task['last_error'])
        return {'queue_date': queue_date, 'refresh_pending': True, 'task_id': active_task['id']}
    task = request_queue_refresh_in_transaction(database, queue_date)
    return {'queue_date': queue_date, 'refresh_pending': True, 'task_id': task['id']}

def request_queue_refresh_in_transaction(database: PostgresConnection, queue_date: str, *, preserve_through_entry_id: int | None=None) -> dict[str, Any]:
    return queue_application.request_queue_refresh_in_transaction(database, queue_date, preserve_through_entry_id=preserve_through_entry_id)
from .services.queue_next_card import prepare_queue_head
register_command('queue.issue_head', issue_queue_head, prepare=prepare_queue_head)
register_command('queue.attempt_failed', mark_attempt_failed)
register_command('queue.bury', bury_queue_entry)
register_command('queue.ensure_current', ensure_current_queue)
