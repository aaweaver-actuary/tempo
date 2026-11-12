"""Position Study grade effects inside a caller-owned writer transaction.

The transaction owner must fence the original queue, card, attempt and authored
source before calling this adapter. This module never commits or opens a
connection. Its decision helper accepts only facts reconstructed by the adapter
from immutable issuance and first-response receipts, not browser claims.
"""
from __future__ import annotations

from tempo_storage.study import position_grade_effects as storage

from dataclasses import dataclass

from app.study.position_learning.daily_credit import (
    AcceptedGradeReceipt, CurrentPositionState, GradeAcceptance, accept_grade,
)
from app.study.position_learning.first_response_policy import propose_grade
from tempo_evidence_math.position_learning import AcceptedEvidence, DueAtIssuance, Hold
from tempo_scheduling import PositionSchedule, schedule_position_review


@dataclass(frozen=True)
class _Decision:
    acceptance: GradeAcceptance
    schedule: PositionSchedule | None


def _decide(
    evidence: AcceptedEvidence,
    due: DueAtIssuance,
    current: CurrentPositionState,
    receipts: tuple[AcceptedGradeReceipt, ...],
    fsrs_json: str,
) -> _Decision | Hold:
    """Apply the shared policy to locked, server-verified facts only."""
    proposal = propose_grade(evidence, due)
    if isinstance(proposal, Hold):
        return proposal
    accepted = accept_grade(proposal, current, receipts, evidence=evidence)
    if isinstance(accepted, Hold):
        return accepted
    schedule = None
    if accepted.schedule_grade is not None:
        schedule = schedule_position_review(
            accepted.schedule_grade, fsrs_card_json=fsrs_json,
            accepted_at=evidence.accepted_at, review_day=evidence.accepted_study_day,
        )
    return _Decision(accepted, schedule)


def _lock_states(database, *, repertoire_id, owner_epoch, authored_digest, positions):
    """Fence the active owner and lock each requested state in stable key order.

    The caller already holds F3's shared owner advisory lock, original Learning
    locks, and B1's authored-source fence, in that order. SKIP LOCKED makes a
    concurrent owner hold retryable; it must never be interpreted as absence.
    """
    owner = storage.locked_owner(database, (repertoire_id,)).fetchone()
    if owner is None:
        return Hold('position_owner_busy_or_missing')
    if (owner['epoch'], owner['mode'], owner['source_digest']) != (owner_epoch, 'active', authored_digest):
        return Hold('position_owner_or_source_changed')
    locked = {}
    for key in sorted(set(positions)):
        if key.repertoire_id != repertoire_id:
            return Hold('position_scope_mismatch')
        row = storage.locked_state(database, (key.repertoire_id, key.variant, key.position)).fetchone()
        if row is None:
            return Hold('position_state_busy_or_missing')
        if (row['owner_epoch'], row['source_digest']) != (owner_epoch, authored_digest):
            return Hold('position_state_owner_or_source_changed')
        locked[key] = row
    return locked


def _load_plan(database, attempt_id, authored_digest):
    """Read and digest-check the original issued record, never a browser echo."""
    from app.study.position_learning.issued_plan_contracts import decode_issued_record

    row = storage.issued_plan(database, (attempt_id,)).fetchone()
    if row is None:
        return Hold('issued_plan_missing')
    raw = row['plan_json']
    if not isinstance(raw, dict):
        return Hold('issued_plan_invalid')
    plan = decode_issued_record(raw)
    if isinstance(plan, Hold):
        return plan
    token = raw.get('authored_source_token')
    if (not isinstance(token, dict) or token.get('authored_digest') != authored_digest
            or row['source_digest'] != authored_digest
            or (row['attempt_id'], row['queue_entry_id'], row['card_id'], row['card_revision'],
                row['repertoire_id'], row['variant'], row['owner_epoch'], row['issue_study_day'],
                row['plan_digest']) !=
               (plan.attempt_id, plan.queue_entry_id, plan.original_card_id, plan.original_card_revision,
                plan.target.key.repertoire_id, plan.target.key.variant, plan.owner_epoch, plan.local_day,
                plan.plan_id)):
        return Hold('issued_plan_scope_or_source_mismatch')
    return plan


def _load_events(database, plan, event_ids):
    """Revalidate each durable G3 receipt against the original decoded plan."""
    from datetime import datetime
    from app.study.position_learning.first_response_codec import decode_position_first_response
    from tempo_evidence_math.position_learning import FirstResponse

    if not event_ids or len(event_ids) > len(plan.decision_slots) or len(set(event_ids)) != len(event_ids):
        return Hold('invalid_position_event_selection')
    events = []
    seen_indices = set()
    for event_id in event_ids:
        row = storage.first_response(database, (event_id,)).fetchone()
        if row is None:
            return Hold('first_response_missing')
        try:
            raw = decode_position_first_response(row['payload_json'], row['payload_digest'], plan)
            index = raw['decision_index']
            observed_at = datetime.fromisoformat(raw['observed_at'].replace('Z', '+00:00'))
            definition = plan.decision_slots[index].definition
            if ((row['event_id'], row['attempt_id'], row['decision_index'], row['repertoire_id'],
                 row['variant'], row['position'], row['owner_epoch'], row['observed_at']) !=
                (raw['event_id'], plan.attempt_id, index, definition.key.repertoire_id,
                 definition.key.variant, definition.key.position, plan.owner_epoch, observed_at)
                    or index in seen_indices):
                return Hold('first_response_scope_mismatch')
            response = FirstResponse(plan.attempt_id, index, definition, raw['actual_uci'],
                                     raw['assistance_before'], observed_at, event_id,
                                     raw['manual_failure'])
        except (ValueError, TypeError, KeyError, IndexError):
            return Hold('first_response_invalid')
        seen_indices.add(index)
        events.append((response, raw['disposition'], row['payload_digest'], row['accepted_at']))
    return tuple(events)


@dataclass(frozen=True)
class CompletionAuthority:
    """Identity of Learning's saved, completed aggregate; verified again in SQL."""
    attempt_id: str
    queue_entry_id: int
    card_id: str
    card_revision: int
    review_id: int


@dataclass(frozen=True)
class GradeReceipt:
    effect_id: str
    event_id: str
    grade: str
    accepted_study_day: object
    accepted_at: object
    fsrs_before_json: str | None
    fsrs_after_json: str | None
    version_before: int | None
    version_after: int | None
    replayed: bool = False


class GradeEffectError(RuntimeError):
    """Abort the borrowed transaction; the caller must roll it back."""


def _completion_saved(database, plan, authority):
    if not isinstance(authority, CompletionAuthority):
        return False
    if (authority.attempt_id, authority.queue_entry_id, authority.card_id, authority.card_revision) != (
            plan.attempt_id, plan.queue_entry_id, plan.original_card_id, plan.original_card_revision):
        return False
    row = storage.saved_completion(database, (authority.attempt_id, authority.queue_entry_id, authority.card_id, authority.card_revision,
         authority.review_id, 'attempt:' + authority.attempt_id)).fetchone()
    return row is not None


def apply_in_borrowed_transaction(database, attempt_id, event_ids, *, owner_epoch,
                                  authored_digest, accepted_study_day, accepted_at,
                                  completion=None):
    """Apply original first responses under the caller's locked writer transaction.

    Required locks: F3 shared advisory, original card/day/queue/attempt, then
    B1 authored-source fence. The caller must roll back the entire transaction
    on *any* exception. No commit, connection or savepoint is owned here.
    """
    import hashlib
    import json
    from datetime import date, datetime
    from types import SimpleNamespace

    if (getattr(database, 'read_only', True)
            or database.raw.info.transaction_status.name != 'INTRANS'):
        raise GradeEffectError('borrowed_owner_writer_transaction_required')
    if (not isinstance(accepted_study_day, date) or isinstance(accepted_study_day, datetime)
            or not isinstance(accepted_at, datetime) or accepted_at.utcoffset() is None):
        raise GradeEffectError('invalid_acceptance_time')
    plan = _load_plan(database, attempt_id, authored_digest)
    if isinstance(plan, Hold):
        raise GradeEffectError(plan.reason)
    if plan.owner_epoch != owner_epoch:
        raise GradeEffectError('owner_epoch_changed')
    events = _load_events(database, plan, event_ids)
    if isinstance(events, Hold):
        raise GradeEffectError(events.reason)
    # A complete retry returns immutable original outcomes before current owner
    # or schedule checks; it creates no new effect and does not regrade a prior
    # negative when a saved aggregate is attached later.
    replays = []
    for response, _disposition, payload_digest, _accepted in events:
        digest = hashlib.sha256((plan.plan_id + ':' + payload_digest).encode('ascii')).hexdigest()
        effect_id = hashlib.sha256(('study:first_response:' + response.event_id).encode('utf-8')).hexdigest()
        prior = storage.prior_grade(database, (response.event_id, response.attempt_id, response.index)).fetchall()
        if not prior:
            break
        if (len(prior) != 1 or prior[0]['event_id'] != response.event_id
                or prior[0]['effect_id'] != effect_id or prior[0]['request_digest'] != digest):
            raise GradeEffectError('first_response_receipt_conflict')
        old = prior[0]
        replays.append(GradeReceipt(*(old[k] for k in ('effect_id','event_id','grade',
            'accepted_study_day','accepted_at','fsrs_before_json','fsrs_after_json',
            'version_before','version_after')), replayed=True))
    if len(replays) == len(events):
        return tuple(replays)
    states = _lock_states(database, repertoire_id=plan.target.key.repertoire_id,
                          owner_epoch=owner_epoch, authored_digest=authored_digest,
                          positions=tuple(e[0].definition.key for e in events))
    if isinstance(states, Hold):
        raise GradeEffectError(states.reason)
    saved = _completion_saved(database, plan, completion) if completion is not None else False
    if completion is not None and not saved:
        raise GradeEffectError('saved_completion_unavailable')
    # Plan the batch against successive state, while retaining the all-holds
    # before-writes boundary. A legal route can revisit the same exact position.
    states = {key: dict(row) for key, row in states.items()}
    staged_goods = {}
    decisions = []
    for response, disposition, payload_digest, event_accepted_at in sorted(events, key=lambda event: event[0].index):
        row = states[response.definition.key]
        if (row['expected_uci'], row['policy_version']) != (response.definition.expected_uci,
                                                             response.definition.policy_version):
            raise GradeEffectError('response_definition_changed')
        digest = hashlib.sha256((plan.plan_id + ':' + payload_digest).encode('ascii')).hexdigest()
        effect_id = hashlib.sha256(('study:first_response:' + response.event_id).encode('utf-8')).hexdigest()
        prior = storage.prior_grade(database, (response.event_id, response.attempt_id, response.index)).fetchall()
        if prior:
            if (len(prior) != 1 or prior[0]['event_id'] != response.event_id
                    or prior[0]['effect_id'] != effect_id or prior[0]['request_digest'] != digest):
                raise GradeEffectError('first_response_receipt_conflict')
            old = prior[0]
            decisions.append(GradeReceipt(*(old[k] for k in ('effect_id','event_id','grade',
                'accepted_study_day','accepted_at','fsrs_before_json','fsrs_after_json',
                'version_before','version_after')), replayed=True))
            continue
        if accepted_at < event_accepted_at:
            raise GradeEffectError('grade_precedes_first_response_acceptance')
        due = next((d for d in plan.due_positions if d.definition == response.definition), None)
        if due is None:
            raise GradeEffectError('issued_due_witness_missing')
        try:
            native = json.loads(row['fsrs_json'])
            if native['card_id'] != row['fsrs_internal_card_id']:
                raise ValueError('fsrs identity mismatch')
            last_review = native['last_review']
            last_transition = datetime.fromisoformat(last_review) if last_review else None
        except (ValueError, TypeError, KeyError):
            raise GradeEffectError('position_fsrs_invalid') from None
        history = storage.daily_good_grades(database, (response.definition.key.repertoire_id, response.definition.key.variant,
             response.definition.key.position, accepted_study_day)).fetchall()
        receipts = tuple(SimpleNamespace(
            proposal=SimpleNamespace(event_id=h['event_id']),
            evidence=SimpleNamespace(first_response=SimpleNamespace(attempt_id=h['attempt_id'], index=h['decision_index'])),
            final=SimpleNamespace(position=response.definition.key, study_day=h['accepted_study_day'], grade=h['grade']),
        ) for h in history) + tuple(staged_goods.get(response.definition.key, ()))
        evidence = AcceptedEvidence(response, True, disposition, saved, saved, owner_epoch,
                                    plan.source_digest, accepted_at, accepted_study_day)
        current = CurrentPositionState(response.definition, owner_epoch, row['version'],
                                       accepted_study_day, last_transition)
        decision = _decide(evidence, due, current, receipts, row['fsrs_json'])
        if isinstance(decision, Hold):
            if decision.reason == 'awaiting_saved_completion':
                return decision
            raise GradeEffectError(decision.reason)
        final = decision.acceptance.receipt.final
        schedule = decision.schedule
        before = row['fsrs_json']
        after = schedule.fsrs_card_json if schedule else before
        vb = row['version']
        va = vb + 1 if schedule else vb
        decisions.append((effect_id, response, digest, final, before, after, vb, va, schedule))
        if final.grade == 'good':
            staged_goods.setdefault(response.definition.key, []).append(decision.acceptance.receipt)
        if schedule is not None:
            row['fsrs_json'], row['version'] = after, va

    # All policy holds have been checked before the first write. Any SQL or
    # scheduling failure below must propagate to the caller and abort its scope.
    result = []
    for item in decisions:
        if isinstance(item, GradeReceipt):
            result.append(item)
            continue
        effect_id, response, digest, final, before, after, vb, va, schedule = item
        key = response.definition.key
        if schedule is not None:
            updated = storage.update_schedule(database, (after, schedule.due_at, va, key.repertoire_id, key.variant, key.position, vb))
            if updated.rowcount != 1:
                raise GradeEffectError('position_state_version_changed')
        storage.insert_grade(database, (effect_id, response.event_id, response.attempt_id, response.index, key.repertoire_id,
             key.variant, key.position, owner_epoch, response.definition.expected_uci,
             response.definition.policy_version, authored_digest, accepted_study_day, final.grade,
             digest, accepted_at, before, after, vb, va))
        result.append(GradeReceipt(effect_id, response.event_id, final.grade, accepted_study_day,
                                   accepted_at, before, after, vb, va))
    return tuple(result)
