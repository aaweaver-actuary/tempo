"""Position Study grade effects inside a caller-owned writer transaction.

The transaction owner must fence the original queue, card, attempt and authored
source before calling this adapter. This module never commits or opens a
connection. Its decision helper accepts only facts reconstructed by the adapter
from immutable issuance and first-response receipts, not browser claims.
"""
from __future__ import annotations

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
    owner = database.execute_native(
        """SELECT o.epoch, o.mode, e.source_digest
             FROM study_position_owners o
             JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.repertoire_id=%s FOR SHARE OF o,e SKIP LOCKED""",
        (repertoire_id,),
    ).fetchone()
    if owner is None:
        return Hold('position_owner_busy_or_missing')
    if (owner['epoch'], owner['mode'], owner['source_digest']) != (owner_epoch, 'active', authored_digest):
        return Hold('position_owner_or_source_changed')
    locked = {}
    for key in sorted(set(positions)):
        if key.repertoire_id != repertoire_id:
            return Hold('position_scope_mismatch')
        row = database.execute_native(
            """SELECT repertoire_id,variant,position,expected_uci,policy_version,
                      owner_epoch,source_digest,version,fsrs_internal_card_id,fsrs_json,due_at
                 FROM study_position_states
                WHERE repertoire_id=%s AND variant=%s AND position=%s
                  FOR UPDATE SKIP LOCKED""",
            (key.repertoire_id, key.variant, key.position),
        ).fetchone()
        if row is None:
            return Hold('position_state_busy_or_missing')
        if (row['owner_epoch'], row['source_digest']) != (owner_epoch, authored_digest):
            return Hold('position_state_owner_or_source_changed')
        locked[key] = row
    return locked


def _load_plan(database, attempt_id, authored_digest):
    """Read and digest-check the original issued record, never a browser echo."""
    from app.study.position_learning.issued_plan_contracts import decode_issued_record

    row = database.execute_native(
        """SELECT attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,
                  owner_epoch,source_digest,issue_study_day,plan_digest,plan_json
             FROM study_position_issued_plans WHERE attempt_id=%s""", (attempt_id,),
    ).fetchone()
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
    from app.services.postgres_opening_evidence import decode_position_first_response
    from tempo_evidence_math.position_learning import FirstResponse

    if not event_ids or len(event_ids) > len(plan.decision_slots) or len(set(event_ids)) != len(event_ids):
        return Hold('invalid_position_event_selection')
    events = []
    seen_indices = set()
    for event_id in event_ids:
        row = database.execute_native(
            """SELECT event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,
                      payload_digest,payload_json,observed_at,accepted_at
                 FROM study_position_first_responses WHERE event_id=%s""", (event_id,),
        ).fetchone()
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
    row = database.execute_native(
        """SELECT 1 FROM opening_evidence_attempts e
             JOIN review_attempt_receipts r ON r.attempt_id=e.attempt_id
             JOIN reviews v ON v.id=r.review_id
            WHERE e.attempt_id=%s AND e.queue_entry_id=%s AND e.card_id=%s AND e.card_revision=%s
              AND e.state='complete' AND e.review_id=%s
              AND r.queue_entry_id=e.queue_entry_id AND r.card_id=e.card_id
              AND r.review_id=e.review_id AND r.scheduling_status='canonical'
              AND v.card_id=e.card_id AND v.source_kind IN ('study','position_study') AND v.source_ref=%s
              AND v.invalidated_at IS NULL""",
        (authority.attempt_id, authority.queue_entry_id, authority.card_id, authority.card_revision,
         authority.review_id, 'attempt:' + authority.attempt_id),
    ).fetchone()
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
        prior = database.execute_native(
            """SELECT effect_id,event_id,grade,accepted_study_day,accepted_at,fsrs_before_json,
                      fsrs_after_json,version_before,version_after,request_digest
                 FROM study_position_grades WHERE event_id=%s OR
                  (attempt_id=%s AND decision_index=%s AND effect_kind='first_response')""",
            (response.event_id, response.attempt_id, response.index),
        ).fetchall()
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
    decisions = []
    for response, disposition, payload_digest, event_accepted_at in events:
        row = states[response.definition.key]
        if (row['expected_uci'], row['policy_version']) != (response.definition.expected_uci,
                                                             response.definition.policy_version):
            raise GradeEffectError('response_definition_changed')
        digest = hashlib.sha256((plan.plan_id + ':' + payload_digest).encode('ascii')).hexdigest()
        effect_id = hashlib.sha256(('study:first_response:' + response.event_id).encode('utf-8')).hexdigest()
        prior = database.execute_native(
            """SELECT effect_id,event_id,grade,accepted_study_day,accepted_at,fsrs_before_json,
                      fsrs_after_json,version_before,version_after,request_digest
                 FROM study_position_grades WHERE event_id=%s OR
                  (attempt_id=%s AND decision_index=%s AND effect_kind='first_response')""",
            (response.event_id, response.attempt_id, response.index),
        ).fetchall()
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
        history = database.execute_native(
            """SELECT event_id,attempt_id,decision_index,grade,accepted_study_day
                 FROM study_position_grades WHERE repertoire_id=%s AND variant=%s AND position=%s
                   AND grade='good' AND accepted_study_day=%s""",
            (response.definition.key.repertoire_id, response.definition.key.variant,
             response.definition.key.position, accepted_study_day),
        ).fetchall()
        receipts = tuple(SimpleNamespace(
            proposal=SimpleNamespace(event_id=h['event_id']),
            evidence=SimpleNamespace(first_response=SimpleNamespace(attempt_id=h['attempt_id'], index=h['decision_index'])),
            final=SimpleNamespace(position=response.definition.key, study_day=h['accepted_study_day'], grade=h['grade']),
        ) for h in history)
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
            database.execute_native(
                """UPDATE study_position_states SET fsrs_json=%s,due_at=%s,version=%s
                     WHERE repertoire_id=%s AND variant=%s AND position=%s AND version=%s""",
                (after, schedule.due_at, va, key.repertoire_id, key.variant, key.position, vb),
            )
        database.execute_native(
            """INSERT INTO study_position_grades
                (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,
                 variant,position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,
                 grade,request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES (%s,'study',%s,%s,%s,'first_response',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (effect_id, response.event_id, response.attempt_id, response.index, key.repertoire_id,
             key.variant, key.position, owner_epoch, response.definition.expected_uci,
             response.definition.policy_version, authored_digest, accepted_study_day, final.grade,
             digest, accepted_at, before, after, vb, va),
        )
        result.append(GradeReceipt(effect_id, response.event_id, final.grade, accepted_study_day,
                                   accepted_at, before, after, vb, va))
    return tuple(result)


def position_completion_matches(database, effect, completion):
    """Verify the saved plan and exact target response, never aggregate rating alone."""
    from tempo_evidence_math.position_learning import Hold

    row = database.execute_native("""SELECT r.attempt_id,r.queue_entry_id,p.source_digest
        FROM review_attempt_receipts r JOIN study_position_issued_plans p ON p.attempt_id=r.attempt_id
        WHERE r.review_id=%s AND r.card_id=%s AND r.scheduling_status='canonical'""",
        (completion.review_id, completion.card_id)).fetchone()
    if row is None:
        return False
    plan = _load_plan(database, row['attempt_id'], row['source_digest'])
    if isinstance(plan, Hold):
        return False
    authority = CompletionAuthority(row['attempt_id'], row['queue_entry_id'], completion.card_id,
                                    completion.revision, completion.review_id)
    if not _completion_saved(database, plan, authority):
        return False
    selected = effect.qualification.selection if effect.qualification else None
    if selected is not None and selected.decision is not None:
        repertoire, position, expected = (selected.decision.repertoire_id, selected.position_key,
                                           tuple(selected.expected_uci))
    elif effect.lapse is not None:
        finding = effect.lapse.finding
        repertoire, position, expected = finding.repertoire_id, finding.position_key, tuple(finding.expected_uci)
    else:
        return False
    if len(expected) != 1:
        return False
    rows = database.execute_native("""SELECT event_id FROM study_position_first_responses
        WHERE attempt_id=%s AND repertoire_id=%s AND position=%s""",
        (plan.attempt_id, repertoire, position)).fetchall()
    if len(rows) != 1:
        return False
    events = _load_events(database, plan, (rows[0]['event_id'],))
    if isinstance(events, Hold):
        return False
    response, disposition, _digest, _accepted = events[0]
    if (response.definition.expected_uci != expected[0] or disposition != 'expected'
            or response.assistance_before or response.manual_failure or not response.correct):
        return False
    # A saved accepted response can be Good or Observe (e.g. one Good/day).
    grade = database.execute_native("""SELECT grade FROM study_position_grades
        WHERE event_id=%s AND source_kind='study' AND grade IN ('good','observe')""",
        (response.event_id,)).fetchone()
    if grade is None:
        return False
    reset = database.execute_native("""SELECT version_after,repertoire_id,variant,position
        FROM study_position_grades WHERE game_effect_key=%s AND source_kind='game_lapse'""",
        (effect.key.logical_id(),)).fetchone()
    if reset is not None:
        witness = next((d for d in plan.due_positions if d.definition == response.definition), None)
        key = response.definition.key
        if (witness is None or (reset['repertoire_id'], reset['variant'], reset['position']) !=
                (key.repertoire_id, key.variant, key.position)
                or witness.position_revision < reset['version_after']):
            return False
    return True
