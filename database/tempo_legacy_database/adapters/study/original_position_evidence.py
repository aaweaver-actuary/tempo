"""Read original position evidence within one caller-owned PostgreSQL snapshot.

This capability neither establishes source finality nor authorizes grades. JSONB
text is a transport of the stored value, not the original input bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, Any
from collections.abc import Mapping
import json

from tempo_evidence_math.position_learning import Hold
from .position_issued_plans import replay_exact_attempt


class NativeEvidenceReadScope(Protocol):
    @property
    def in_transaction(self) -> bool: ...
    @property
    def read_only(self) -> bool: ...
    def execute_native(self, statement: str, parameters: tuple | list = ()) -> Any: ...


@dataclass(frozen=True)
class EvidenceProvenance:
    database: str
    schema: str
    schema_version: int
    snapshot: str
    backend_pid: int
    transaction_started_at: datetime


@dataclass(frozen=True)
class EvidenceReadBlocked:
    status: str
    code: str
    attempt_id: str
    decision_index: int | None = None


@dataclass(frozen=True)
class OriginalPlanRowEvidence:
    provenance: EvidenceProvenance
    attempt_id: str
    queue_entry_id: int
    card_id: str
    card_revision: int
    repertoire_id: str
    variant: str
    owner_epoch: str
    authored_digest: str
    source_digest: str
    issue_study_day: date
    issued_at: datetime
    stored_record_json: str
    original_plan_digest: str
    storage_encoding: str = 'postgres-jsonb-value'
    canonical_codec: str = 'legacy-issued-plan-record-v1'
    original_raw_bytes: bytes | None = None
    raw_encoding: str | None = None


@dataclass(frozen=True)
class OriginalResponseRowEvidence:
    provenance: EvidenceProvenance
    event_id: str
    attempt_id: str
    decision_index: int
    stored_payload_json: str
    payload_digest: str
    observed_at: datetime
    accepted_at: datetime
    effect_id: str | None
    accepted_study_day: date | None
    operation_id: str | None = None
    storage_encoding: str = 'postgres-jsonb-value'
    canonical_codec: str = 'legacy-position-first-response-v1'
    original_raw_bytes: bytes | None = None
    raw_encoding: str | None = None


@dataclass(frozen=True)
class OriginalCompletionRowEvidence:
    provenance: EvidenceProvenance
    status: str
    attempt_id: str
    queue_entry_id: int | None
    card_id: str
    card_revision: int
    review_id: int | None
    contiguous_sequence: int
    terminal_json: str | None
    review_result_json: str | None
    receipt_result_json: str | None
    outcome: str | None
    guided: int | None
    retired_operation_id: str | None
    invalidated_at: str | None
    completed_at: datetime | None
    study_timezone: str


def _scope(tx: NativeEvidenceReadScope, attempt_id: str):
    if not isinstance(attempt_id, str) or not attempt_id:
        return EvidenceReadBlocked('invalid', 'attempt_identity_required', str(attempt_id))
    if not tx.in_transaction or not tx.read_only:
        return EvidenceReadBlocked('invalid', 'active_read_only_transaction_required', attempt_id)
    row = tx.execute_native("""SELECT current_database() AS db, current_schema() AS schema,
        current_setting('transaction_isolation') AS isolation,
        current_setting('transaction_read_only') AS read_only,
        pg_current_snapshot()::text AS snapshot, pg_backend_pid() AS pid,
        transaction_timestamp() AS started,
        (SELECT max(version) FROM public.tempo_schema_migrations) AS version""").fetchone()
    if row['isolation'] != 'repeatable read' or row['read_only'] != 'on' or row['schema'] != 'public':
        return EvidenceReadBlocked('invalid', 'read_only_repeatable_read_public_required', attempt_id)
    return EvidenceProvenance(row['db'], row['schema'], row['version'], row['snapshot'], row['pid'], row['started'])


def read_original_plan(tx: NativeEvidenceReadScope, *, attempt_id: str) -> OriginalPlanRowEvidence | EvidenceReadBlocked:
    provenance = _scope(tx, attempt_id)
    if isinstance(provenance, EvidenceReadBlocked): return provenance
    row = tx.execute_native('SELECT *,plan_json::text AS stored_json FROM public.study_position_issued_plans WHERE attempt_id=%s', (attempt_id,)).fetchone()
    if row is None: return EvidenceReadBlocked('absent', 'plan_absent', attempt_id)
    plan = replay_exact_attempt(tx, attempt_id)
    if plan is None or isinstance(plan, Hold): return EvidenceReadBlocked('conflict', 'plan_invalid', attempt_id)
    return OriginalPlanRowEvidence(provenance, row['attempt_id'], row['queue_entry_id'], row['card_id'],
        row['card_revision'], row['repertoire_id'], row['variant'], row['owner_epoch'], row['source_digest'],
        plan.source_digest, row['issue_study_day'], row['issued_at'], row['stored_json'], row['plan_digest'])


def read_original_completion(tx: NativeEvidenceReadScope, *, attempt_id: str) -> OriginalCompletionRowEvidence | EvidenceReadBlocked:
    plan = read_original_plan(tx, attempt_id=attempt_id)
    if isinstance(plan, EvidenceReadBlocked): return plan
    row = tx.execute_native('''SELECT a.*, rr.card_id AS receipt_card, rr.queue_entry_id AS receipt_queue,
        rr.review_id AS receipt_review, rr.outcome, rr.guided, rr.scheduling_status, rr.result_json AS receipt_result_json,
        r.card_id AS review_card, r.source_kind, r.source_ref, r.invalidated_at
        FROM public.opening_evidence_attempts a
        LEFT JOIN public.review_attempt_receipts rr ON rr.attempt_id=a.attempt_id
        LEFT JOIN public.reviews r ON r.id=rr.review_id WHERE a.attempt_id=%s''', (attempt_id,)).fetchone()
    if row is None: return EvidenceReadBlocked('absent', 'completion_absent', attempt_id)
    if row['retired_operation_id'] is not None or row['invalidated_at'] is not None:
        status = 'invalidated'
    elif row['state'] != 'complete':
        if row['review_id'] is not None or row['receipt_review'] is not None:
            return EvidenceReadBlocked('conflict', 'inconsistent_pending_receipt', attempt_id)
        status = 'pending'
    else:
        try:
            terminal = json.loads(row['terminal_json'])
            events = tx.execute_native('SELECT count(*) AS n, min(sequence) AS first, max(sequence) AS last FROM public.opening_evidence_events WHERE attempt_id=%s', (attempt_id,)).fetchone()
            valid = (isinstance(terminal, dict) and terminal.get('state') == 'complete'
                and type(terminal.get('final_sequence')) is int and terminal['final_sequence'] == row['contiguous_sequence']
                and events['n'] == row['contiguous_sequence'] and (events['n'] == 0 or (events['first'] == 1 and events['last'] == events['n']))
                and row['review_id'] is not None and row['review_id'] == row['receipt_review']
                and row['scheduling_status'] == 'canonical'
                and (row['card_id'], row['card_revision'], row['queue_entry_id'], row['repertoire_id']) ==
                    (plan.card_id, plan.card_revision, plan.queue_entry_id, plan.repertoire_id)
                and (row['receipt_card'], row['receipt_queue'], row['review_card']) ==
                    (plan.card_id, plan.queue_entry_id, plan.card_id)
                and row['source_kind'] in ('study', 'position_study')
                and row['source_ref'] == 'attempt:' + attempt_id
                and row['completed_at'] is not None and row['review_result_json'] is not None
                and row['receipt_result_json'] is not None)
        except (TypeError, ValueError): valid = False
        if not valid: return EvidenceReadBlocked('conflict', 'completion_mismatch', attempt_id)
        status = 'committed'
    return OriginalCompletionRowEvidence(plan.provenance, status, attempt_id, row['queue_entry_id'], row['card_id'],
        row['card_revision'], row['review_id'], row['contiguous_sequence'], row['terminal_json'],
        row['review_result_json'], row['receipt_result_json'], row['outcome'], row['guided'],
        row['retired_operation_id'], row['invalidated_at'], row['completed_at'], row['study_timezone'])


class OriginalResponseDecoder(Protocol):
    def __call__(self, payload: dict[str, object], digest: str,
                 plan_record: Mapping[str, object], /) -> Mapping[str, object]: ...


def read_original_first_responses(
    tx: NativeEvidenceReadScope, *, attempt_id: str, decode_response: OriginalResponseDecoder,
) -> tuple[OriginalResponseRowEvidence, ...] | EvidenceReadBlocked:
    plan = read_original_plan(tx, attempt_id=attempt_id)
    if isinstance(plan, EvidenceReadBlocked):
        if plan.code == 'plan_absent':
            return EvidenceReadBlocked('absent', 'LP_ORIGINAL_PLAN_MISSING', attempt_id)
        return plan
    record = json.loads(plan.stored_record_json)
    decoded_plan = replay_exact_attempt(tx, attempt_id)
    rows = tx.execute_native(
        """SELECT r.*,r.payload_json::text AS stored_json,g.effect_id,g.accepted_study_day
        FROM public.study_position_first_responses r
        LEFT JOIN public.study_position_grades g ON g.event_id=r.event_id AND g.effect_kind='first_response'
        WHERE r.attempt_id=%s ORDER BY r.decision_index LIMIT 21""", (attempt_id,)).fetchall()
    if len(rows) > 20:
        return EvidenceReadBlocked('conflict', 'LP_RESPONSE_ROW_MISMATCH', attempt_id)
    result = []
    for row in rows:
        index = row['decision_index']
        try:
            payload = json.loads(row['stored_json'])
            if not isinstance(payload, dict): raise ValueError('invalid_response')
            decoded = decode_response(payload, row['payload_digest'], record)
        except ValueError:
            return EvidenceReadBlocked('conflict', 'LP_RESPONSE_INVALID', attempt_id, index)
        if (not isinstance(decoded, Mapping) or decoded != json.loads(row['stored_json']) or type(index) is not int or index < 0
                or index >= len(decoded_plan.decision_slots)):
            return EvidenceReadBlocked('conflict', 'LP_RESPONSE_ROW_MISMATCH', attempt_id, index)
        slot = decoded_plan.decision_slots[index]
        try:
            observed = datetime.fromisoformat(decoded['observed_at'].replace('Z', '+00:00'))
        except (KeyError, AttributeError, TypeError, ValueError):
            return EvidenceReadBlocked('conflict', 'LP_RESPONSE_ROW_MISMATCH', attempt_id, index)
        expected = {'event_id': row['event_id'], 'attempt_id': attempt_id, 'decision_index': index,
                    'repertoire_id': row['repertoire_id'], 'variant': row['variant'],
                    'position': row['position'], 'owner_epoch': row['owner_epoch']}
        if (any(decoded.get(key) != value for key, value in expected.items())
                or (row['repertoire_id'], row['variant'], row['position'], row['owner_epoch']) !=
                   (slot.definition.key.repertoire_id, slot.definition.key.variant,
                    slot.definition.key.position, plan.owner_epoch)
                or observed != row['observed_at']):
            return EvidenceReadBlocked('conflict', 'LP_RESPONSE_ROW_MISMATCH', attempt_id, index)
        result.append(OriginalResponseRowEvidence(plan.provenance, row['event_id'], attempt_id, index,
            row['stored_json'], row['payload_digest'], row['observed_at'], row['accepted_at'], row['effect_id'], row['accepted_study_day']))
    return tuple(result)
