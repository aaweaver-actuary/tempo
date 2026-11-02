"""Explicit next-head preparation, injected by the current application assembly.

The preparation callback only reads/prepares before the command writer opens.
The selected owner unit applies in the receipt-fenced borrowed writer.
Head issuance only observes durable owner facts; it never calls a
producer, projects repertoire content or runs optional preparation in a review.
"""
from collections.abc import Callable

from app.study.interfaces.next_card import PreparedNextCardWork, NoNextCardWork, NextCardPreparation, next_card_preparation_payload

_prepare: Callable[[], PreparedNextCardWork] | None = None
_source_pending: Callable[..., bool | NextCardPreparation] | None = None


def configure_next_card_work(*, prepare: Callable[[], PreparedNextCardWork],
                             source_pending: Callable[..., bool | NextCardPreparation] | None = None):
    global _prepare, _source_pending
    _prepare, _source_pending = prepare, source_pending


def pending_next_card_preparation(database, day, *, candidate_card_id=None):
    from tempo_legacy_database.adapters.study.game_feedback import installed
    priority = None
    if installed(database):
        from tempo_legacy_database.adapters.study.game_feedback import pending_feedback_preparation, feedback_queue_priorities
        if candidate_card_id is not None:
            priority = feedback_queue_priorities(database, (candidate_card_id,)).get(candidate_card_id)
        if pending_feedback_preparation(database, day, before_priority=priority):
            return True
    source = _source_pending(database, day, before_priority=priority) if _source_pending else False
    # Pending optional obligations are not themselves an issuance veto. Old
    # bool callbacks keep their previous conservative meaning until upgraded.
    return source.issuance_blocked if isinstance(source, NextCardPreparation) else bool(source)


def prepare_next_card_command(payload):
    if payload:
        from fastapi import HTTPException
        raise HTTPException(422, 'Next-card preparation takes no source or target override')
    return _prepare() if _prepare else NoNextCardWork()


def prepared_next_card_result(database, prepared: PreparedNextCardWork):
    result = prepared.apply(database)
    return next_card_preparation_payload(result)


# This ephemeral command payload never crosses the API or enters a receipt; the
# server-owned original queue identity is the only persisted command envelope.
from dataclasses import dataclass
from collections import OrderedDict
from threading import Lock
from time import monotonic

_source_cache = OrderedDict()
_source_cache_lock = Lock()


def _cached_position_source(database, repertoire_id, epoch, digest):
    """Keep a tiny short-lived revision-aware preparation cache.

    This is only a performance hint: every new issue still acquires the source
    owner's complete in-writer fence, including authored rows and integrity.
    """
    from tempo_evidence_math.position_learning import Hold
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    from tempo_legacy_database.adapters.http_composition.queue_projection import position_source_revision
    revision = position_source_revision(database, repertoire_id)
    if revision is None:
        return Hold('position_source_revision_unavailable')
    key = (repertoire_id, epoch, digest, tuple(revision))
    now = monotonic()
    with _source_cache_lock:
        cached = _source_cache.get(key)
        if cached and now - cached[0] < 10:
            _source_cache.move_to_end(key)
            return cached[1]
    source = prepare_position_source(database, repertoire_id)
    if not isinstance(source, Hold):
        with _source_cache_lock:
            _source_cache[key] = (now, source)
            _source_cache.move_to_end(key)
            while len(_source_cache) > 2:
                _source_cache.popitem(last=False)
    return source


@dataclass(frozen=True)
class PreparedQueueHead:
    payload: dict
    mode: str
    proposal: object = None
    reason: str | None = None


def prepare_queue_head(payload):
    """Read an exact replay or prepare one position head before the writer opens."""
    from datetime import date, datetime, timezone
    from uuid import NAMESPACE_URL, uuid5
    from tempo_evidence_math.position_learning import Hold
    from tempo_legacy_database.connections import read_connection
    from tempo_legacy_database.adapters.study.position_due_candidates import PositionDueCandidateReader
    from tempo_legacy_database.adapters.study.position_issued_plans import (replay_exact_attempt,
        read_clean_presentation_proof, read_applicable_clean_proofs)
    from app.study.position_learning.issued_plan_contracts import SelectionRequest
    from app.study.position_learning.issued_plan_service import prepare_position_issuance
    from app.study.position_learning.route_planner import choose_plan
    from .position_queue_admission import select_carrier

    def held(reason):
        return PreparedQueueHead(payload, 'hold', reason=reason)

    with read_connection() as db:
        rows = db.execute_native("""SELECT p.attempt_id FROM study_position_issued_plans p
            WHERE p.queue_entry_id=%s AND p.card_id=%s AND p.card_revision=%s LIMIT 2""",
            (payload['entry_id'], payload['card_id'], payload['expected_revision'])).fetchall()
        if len(rows) > 1:
            return held('position_original_admission_ambiguous')
        if rows:
            plan = replay_exact_attempt(db, rows[0]['attempt_id'])
            if isinstance(plan, Hold) or plan is None:
                return held(plan.reason if isinstance(plan, Hold) else 'position_plan_missing')
            return PreparedQueueHead(payload, 'replay', plan)
        origin = db.execute_native("""SELECT o.admission_repertoire_id,o.issued_as_head,c.content_type
            FROM queue_attempt_origins o JOIN cards c ON c.id=o.card_id
            WHERE o.queue_entry_id=%s AND o.card_id=%s AND o.revision=%s""",
            (payload['entry_id'], payload['card_id'], payload['expected_revision'])).fetchone()
        if not origin:
            return held('position_original_admission_unavailable')
        if origin['content_type'] != 'opening':
            return PreparedQueueHead(payload, 'legacy')
        rep = origin['admission_repertoire_id']
        if not rep:
            owners = db.execute_native("""SELECT DISTINCT o.repertoire_id,o.mode FROM study_position_owners o
                JOIN cards c ON c.id=%s WHERE (c.repertoire_id=o.repertoire_id OR EXISTS
                  (SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=c.id AND rc.repertoire_id=o.repertoire_id))
                AND o.mode IN ('active','staged','paused') LIMIT 2""", (payload['card_id'],)).fetchall()
            return held('position_admission_scope_ambiguous') if owners else PreparedQueueHead(payload, 'legacy')
        owner = db.execute_native("""SELECT o.mode,o.epoch,e.source_digest FROM study_position_owners o
            JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.repertoire_id=%s""", (rep,)).fetchone()
        if not owner or owner['mode'] in ('inactive', 'shadow'):
            return PreparedQueueHead(payload, 'legacy')
        if owner['mode'] != 'active':
            return held('position_owner_' + owner['mode'])
        if origin['issued_as_head']:
            return held('position_issued_head_plan_missing')
        source = _cached_position_source(db, rep, owner['epoch'], owner['source_digest'])
        if isinstance(source, Hold):
            return held(source.reason)
        if source.authored_token.authored_digest != owner['source_digest']:
            return held('position_owner_source_changed')
        instant, day = datetime.now(timezone.utc), date.fromisoformat(payload['queue_date'])
        reader = PositionDueCandidateReader(db, repertoire_id=rep, owner_epoch=owner['epoch'],
            source_digest=owner['source_digest'], issued_at=instant, study_day=day)
        target, cursor = None, None
        # Bound the work independently of catalogue size; a larger backlog stays
        # held for explicit queue refresh rather than falling back to card FSRS.
        for _ in range(10):
            page = reader.read_due_page(after=cursor, limit=100)
            if isinstance(page, Hold):
                return held(page.reason)
            for candidate in page.candidates:
                carrier = select_carrier(source, candidate.state.definition, card_id=payload['card_id'])
                if not isinstance(carrier, Hold) and carrier.presentation_revision == payload['expected_revision']:
                    target = candidate.state.definition
                    break
            if target or page.next_cursor is None:
                break
            cursor = page.next_cursor
        if target is None:
            return held('position_due_target_unavailable')
        proofs = []
        prior = db.execute_native("""SELECT attempt_id FROM study_position_issued_plans
            WHERE repertoire_id=%s AND owner_epoch=%s AND issue_study_day=%s
            ORDER BY issued_at DESC,attempt_id LIMIT 20""", (rep, owner['epoch'], day)).fetchall()
        for row in prior:
            proof = read_clean_presentation_proof(db, row['attempt_id'])
            if not isinstance(proof, Hold):
                proofs.append(proof)
        proofs = read_applicable_clean_proofs(db, [p.attempt_id for p in proofs],
            target=target, owner_epoch=owner['epoch'], study_day=day)
        if isinstance(proofs, Hold):
            return held(proofs.reason)
        route = choose_plan(target.key, source.routes, clean_proofs=proofs,
                            study_day=day, owner_epoch=owner['epoch'])
        if isinstance(route, Hold):
            return held(route.reason)
        keys = tuple(dict.fromkeys(d.definition.key for d in route.decision_occurrences))
        witnesses = reader.read_context(keys)
        if isinstance(witnesses, Hold):
            return held(witnesses.reason)
        if any(isinstance(w, Hold) for w in witnesses):
            return held(next(w.reason for w in witnesses if isinstance(w, Hold)))
        attempt = str(uuid5(NAMESPACE_URL, 'tempo:position-study:%s:%s:%s' %
                            (payload['entry_id'], payload['card_id'], payload['expected_revision'])))
        request = SelectionRequest(target, source.routes, tuple(witnesses), tuple(proofs),
            payload['card_id'], payload['expected_revision'], payload['entry_id'], attempt)
        proposal = prepare_position_issuance(source, request)
        return held(proposal.reason) if isinstance(proposal, Hold) else PreparedQueueHead(payload, 'position', proposal)
