"""Borrowed-transaction, non-serving position effect prototype.

Only fixture authorities exist here. No activation, source certification, HTTP
registration, connection creation or commits. The application injects its pure
grade policy; this database owner reconstructs the policy's authoritative facts.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import chess
from psycopg.pq import TransactionStatus
from tempo_evidence_math.position_learning import (
    AcceptedEvidence, DueAtIssuance, FirstResponse, Hold, PositionDefinition, PositionKey,
)


@dataclass(frozen=True)
class PositionHold:
    reason: str
    classification: str = 'safety_hold'


@dataclass(frozen=True)
class StoredPositionReceipt:
    effect_id: str
    event_id: str
    attempt_id: str
    decision_index: int
    operation_id: str
    request_digest: str
    grade: str
    accepted_at: datetime
    accepted_study_day: date
    fsrs_before_json: str
    fsrs_after_json: str
    version_before: int
    version_after: int
    checkpoint: int
    replayed: bool = False


def digest_bytes(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def owner_day(accepted_at: datetime, timezone: str) -> date:
    if not isinstance(accepted_at, datetime) or accepted_at.utcoffset() is None:
        raise ValueError('accepted_at_aware_required')
    return accepted_at.astimezone(ZoneInfo(timezone)).date()


_RECEIPT = ('effect_id,event_id,attempt_id,decision_index,operation_id,request_digest,'
            'grade,accepted_at,accepted_study_day,fsrs_before_json,fsrs_after_json,'
            'version_before,version_after,checkpoint')


def _row(database, query, args=()):
    cursor = database.execute(query, args)
    row = cursor.fetchone()
    return None if row is None else dict(zip((d.name for d in cursor.description), row))


def _conflict(reason):
    return PositionHold(reason, 'immutable_conflict')


def apply_position_effect(database, *, decide, current_state_type, attempt_id,
                          event_id, operation_id, request_digest, accepted_at,
                          expected_owner_epoch, expected_source_digest,
                          accepted_study_day=None, source_kind='study'):
    """Return a stored receipt only after all effects exist in caller's scope.

    Caller must commit before acknowledging it and rollback on every exception.
    Policy holds occur before writes. SQL errors propagate without converting
    contention into success. Different transport IDs may replay one logical
    event; the original operation ID is preserved. No games/human conflation.
    """
    if database.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('borrowed_transaction_required')
    if source_kind != 'study':
        return PositionHold('separate_game_lapse_authority_required', 'unavailable_dependency')
    if not all(isinstance(s, str) and s for s in
               (attempt_id,event_id,operation_id,request_digest,expected_owner_epoch,expected_source_digest)):
        return _conflict('identity_required')
    if not isinstance(accepted_at, datetime) or accepted_at.utcoffset() is None:
        return _conflict('accepted_at_aware_required')
    # Same namespace/order as gateway; caller may already hold this lock.
    database.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                     (operation_id,))
    # This isolated owner has no card/day/queue writers. Attempt serializes its
    # immutable original bindings; source authority then position match legacy
    # attempt -> authored source -> owner -> state ordering.
    plan = _row(database, 'SELECT * FROM position_effect_attempts WHERE attempt_id=%s FOR UPDATE', (attempt_id,))
    if plan is None:
        return PositionHold('original_issued_attempt_missing')
    # Freeze original evidence before any source/state wait. Insert-only storage
    # also preserves this binding after commit; locks alone cannot do that.
    response = _row(database, 'SELECT * FROM position_effect_responses WHERE event_id=%s FOR SHARE', (event_id,))
    if response is None:
        return PositionHold('durable_first_response_missing')
    if response['attempt_id'] != attempt_id:
        return _conflict('first_response_attempt_mismatch')
    try:
        if digest_bytes(plan['plan_json']) != plan['plan_digest']:
            return _conflict('issued_plan_digest_mismatch')
        # Explicit typed projection is checked against the original frozen bytes.
        binding = {k: plan[k] for k in ('attempt_id','queue_entry_id','card_id','card_revision',
                   'repertoire_id','variant','position','expected_uci','policy_version','owner_epoch',
                   'source_digest','due','issued_version')}
        binding.update(issued_day=plan['issued_day'].isoformat(),issued_at=plan['issued_at'].isoformat())
        if json.loads(plan['plan_json']) != binding:
            return _conflict('issued_plan_binding_mismatch')
        if digest_bytes(response['payload_json']) != response['payload_digest']:
            return _conflict('first_response_digest_mismatch')
        payload = json.loads(response['payload_json'])
        if set(payload) != {'event_id','attempt_id','decision_index','actual_uci','assistance_before','manual_failure','observed_at','disposition'}:
            return _conflict('first_response_payload_shape')
        if (payload['event_id'],payload['attempt_id'],payload['decision_index']) != (
                event_id,attempt_id,response['decision_index']):
            return _conflict('first_response_binding_mismatch')
        if any(type(payload[k]) is not bool for k in ('assistance_before','manual_failure')):
            return _conflict('first_response_boolean_required')
        # Compatibility with current legacy receipt identity/digest.
        expected_request = digest_bytes(plan['plan_digest'] + ':' + response['payload_digest'])
        if request_digest != expected_request:
            return _conflict('request_digest_mismatch')
        effect_id = digest_bytes('study:first_response:' + event_id)
        cursor = database.execute('SELECT '+_RECEIPT+' FROM position_effect_receipts WHERE event_id=%s OR (attempt_id=%s AND decision_index=%s) OR operation_id=%s',
            (event_id,attempt_id,response['decision_index'],operation_id))
        prior = cursor.fetchall()
        if prior:
            if len(prior) != 1:
                return _conflict('receipt_identity_collision')
            receipt = StoredPositionReceipt(*prior[0])
            if (receipt.effect_id,receipt.event_id,receipt.attempt_id,receipt.decision_index,receipt.request_digest) != (
                    effect_id,event_id,attempt_id,response['decision_index'],request_digest):
                return _conflict('receipt_payload_conflict')
            return replace(receipt,replayed=True)
        authority = _row(database, 'SELECT * FROM position_effect_authorities WHERE repertoire_id=%s FOR SHARE', (plan['repertoire_id'],))
        if authority is None or authority['scope'] != 'disposable-prototype':
            return PositionHold('source_authority_unavailable', 'unavailable_dependency')
        day = owner_day(accepted_at,authority['study_timezone'])
        if accepted_study_day is not None and (type(accepted_study_day) is not date or accepted_study_day != day):
            return PositionHold('accepted_study_day_mismatch')
        if (authority['owner_epoch'],authority['source_digest']) != (expected_owner_epoch,expected_source_digest):
            return PositionHold('owner_or_source_changed')
        if (plan['owner_epoch'],plan['source_digest']) != (expected_owner_epoch,expected_source_digest):
            return PositionHold('issued_owner_or_source_changed')
        state = _row(database, 'SELECT * FROM position_effect_states WHERE repertoire_id=%s AND variant=%s AND position=%s FOR UPDATE',
                     (plan['repertoire_id'],plan['variant'],plan['position']))
        if state is None:
            return PositionHold('position_state_missing')
        if any(state[k] != plan[k] for k in ('owner_epoch','source_digest','expected_uci','policy_version')):
            return PositionHold('position_definition_or_source_changed')
        definition = PositionDefinition(PositionKey(plan['repertoire_id'],plan['position'],plan['variant']),plan['expected_uci'],plan['policy_version'])
        board = chess.Board(plan['position'])
        if chess.Move.from_uci(plan['expected_uci']) not in board.legal_moves:
            return PositionHold('authored_response_illegal')
        first = FirstResponse(attempt_id,response['decision_index'],definition,payload['actual_uci'],payload['assistance_before'],
                              datetime.fromisoformat(payload['observed_at']),event_id,payload['manual_failure'])
        if accepted_at < response['accepted_at'] or first.observed_at > response['accepted_at']:
            return PositionHold('acceptance_precedes_first_response')
        if payload['disposition'] == 'wrong' and (first.actual_uci is None or chess.Move.from_uci(first.actual_uci) not in board.legal_moves):
            return PositionHold('wrong_requires_legal_move')
        if payload['disposition'] == 'illegal' and first.actual_uci is not None and chess.Move.from_uci(first.actual_uci) in board.legal_moves:
            return PositionHold('illegal_disposition_mismatch')
        fsrs = json.loads(state['fsrs_json'])
        if type(fsrs['card_id']) is not int or fsrs['card_id'] != state['fsrs_internal_card_id']:
            return PositionHold('position_fsrs_identity_mismatch')
        last = datetime.fromisoformat(fsrs['last_review']) if fsrs['last_review'] else None
        good = _row(database, "SELECT event_id,attempt_id,decision_index FROM position_effect_receipts WHERE repertoire_id=%s AND variant=%s AND position=%s AND accepted_study_day=%s AND grade='good'",
                    (plan['repertoire_id'],plan['variant'],plan['position'],day))
        history = () if good is None else (SimpleNamespace(
            proposal=SimpleNamespace(event_id=good['event_id']),
            evidence=SimpleNamespace(first_response=SimpleNamespace(attempt_id=good['attempt_id'],index=good['decision_index'])),
            final=SimpleNamespace(position=definition.key,study_day=day,grade='good')),)
        saved = plan['completion_receipt_id'] is not None
        if saved and plan['completion_saved_at'] > accepted_at:
            return PositionHold('completion_after_acceptance')
        evidence = AcceptedEvidence(first,True,payload['disposition'],saved,saved,plan['owner_epoch'],plan['source_digest'],accepted_at,day)
        due = DueAtIssuance(definition,plan['due'],plan['issued_day'],plan['owner_epoch'],plan['issued_version'],plan['issued_at'])
        current = current_state_type(definition,authority['owner_epoch'],state['version'],day,last)
        decision = decide(evidence,due,current,history,state['fsrs_json'])
        if isinstance(decision,Hold):
            return PositionHold(decision.reason,'pending_operation' if decision.reason == 'awaiting_saved_completion' else 'safety_hold')
    except (ValueError,TypeError,KeyError,OverflowError) as error:
        return PositionHold('invalid_original_position_evidence:' + type(error).__name__)
    grade = decision.acceptance.receipt.final.grade
    schedule = decision.schedule
    before = state['fsrs_json']
    after = schedule.fsrs_card_json if schedule else before
    version = state['version'] + int(schedule is not None)
    if schedule:
        updated = database.execute('UPDATE position_effect_states SET fsrs_json=%s,due_at=%s,version=%s WHERE repertoire_id=%s AND variant=%s AND position=%s AND version=%s',
            (after,schedule.due_at,version,plan['repertoire_id'],plan['variant'],plan['position'],state['version']))
        if updated.rowcount != 1:
            raise RuntimeError('position_state_version_changed')
    checkpoint = plan['checkpoint'] + 1
    receipt = StoredPositionReceipt(effect_id,event_id,attempt_id,response['decision_index'],operation_id,request_digest,
        grade,accepted_at,day,before,after,state['version'],version,checkpoint)
    database.execute('INSERT INTO position_effect_receipts ('+_RECEIPT+',repertoire_id,variant,position,owner_epoch) VALUES ('+','.join(['%s']*18)+')',
        tuple(getattr(receipt,k) for k in _RECEIPT.split(',')) + (plan['repertoire_id'],plan['variant'],plan['position'],plan['owner_epoch']))
    updated = database.execute('UPDATE position_effect_attempts SET checkpoint=%s WHERE attempt_id=%s AND checkpoint=%s',
        (checkpoint,attempt_id,plan['checkpoint']))
    if updated.rowcount != 1:
        raise RuntimeError('attempt_checkpoint_changed')
    return receipt
