"""Legacy Study owner adapter. No connection, scheduler or remediation policy.

The module borrows the command/review transaction. Its projection fences retained
accepted authority against the existing source/graph generation and exact
physical executable. Shared remediation alone owns frontiers and settlement.
"""
from datetime import date, datetime, timezone
import json

from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken
from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof
from tempo_learning_remediation import (AdmissionResult, AttemptAuthority, Candidate,
    LegacyAcceptedDecision, PostgresRepository, ReviewOutcome, accepted_storage_identity,
    apply_admission)

from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.learning_bridge import remediation as storage
from tempo_legacy_database.adapters.learning_bridge.remediation import CURRENT_FACT_SQL, SOURCE_FENCE_SQL
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .legacy_game_evidence_scope import LEGACY_WORKSPACE_ID, BRIDGE_MISS_REASON


def repository(database):
    from ..game_evidence_commands import BorrowedTupleConnection
    if storage.is_read_only(database):
        return PostgresRepository(storage.read_projection(database), LEGACY_WORKSPACE_ID)
    return PostgresRepository(BorrowedTupleConnection(database), LEGACY_WORKSPACE_ID)


def current_fact(database, card_id, *, lock=False):
    rows = storage.current_facts(database, LEGACY_WORKSPACE_ID, card_id)
    if len(rows) != 1: return None
    payload = json.loads(rows[0]["payload_json"])
    # Early unscoped prototypes remain retained evidence, never a new grant.
    if "decision_scope_proof" not in payload: return None
    authority = LegacyAuthorityToken.model_validate(payload["authority"])
    sources = storage.source_fences(database, LEGACY_WORKSPACE_ID, card_id)
    if not 1 <= len(sources) <= 3 or any(not row["source_current"] for row in sources): return None
    if lock:
        # A normal review must not wait on optional bridge authority after its
        # card/day locks. Busy or changing source simply cannot grant a binding
        # or settlement. Held share locks fence the subsequent exact re-read.
        admitted = storage.try_lock_graph(database, authority.repertoire_id)
        if not admitted or not admitted[0]: return None
        held = storage.try_hold_authority(database, payload["game_id"], authority.repertoire_id)
        if not held: return None
        games = storage.try_hold_source_games(database, sources)
        if len(games) != len(sources): return None
        return current_fact(database, card_id)
    proof = LegacyScopedCatalogueProof.model_validate(payload["decision_scope_proof"])
    comparison, encounter = payload["comparison"], payload["encounter"]
    from .postgres_integrity import current_integrity_certificate
    from .legacy_game_authority import introduced_before_game
    certificate = current_integrity_certificate(database, authority.repertoire_id, authority.graph_generation)
    anchor = proof.authority_anchor(authority.repertoire_id)
    if (certificate is None or anchor is None or anchor > encounter["ply"]
            or proof.prefix_policies[encounter["ply"]] != [authority.repertoire_id]
            or not introduced_before_game(rows[0]["introduced_at"], datetime.fromisoformat(payload["occurred_at"]))):
        return None
    return LegacyAcceptedDecision(card_id, comparison["card_revision"], comparison["decision_id"], authority,
        encounter["position_key"], tuple(comparison["expected_moves"]), LEGACY_WORKSPACE_ID,
        comparison["route_id"], comparison["route_ply"], comparison["exercise_ply"],
        comparison["executable_digest"], tuple(comparison["expected_moves"]), True, True, True,
        scope_proof=proof)


def attempt_identity(entry_id, revision):
    return f"legacy-queue:{entry_id}:revision:{revision}"


def bind_first_issuance(database, entry_id, card_id, revision):
    """Called only by validated actual-head issuance, before its origin stamp."""
    if not storage.is_postgres(database): return
    origin = storage.issued_origin(database, entry_id, card_id, revision)
    if origin is None or origin[0]: return  # Never retrofit a pre-bridge/offline origin.
    current = current_fact(database, card_id, lock=True)
    if current is None or current.card_revision != revision: return
    repository(database).bind_attempt(AttemptAuthority(attempt_identity(entry_id, revision), card_id, revision,
        *accepted_storage_identity(current), datetime.now(timezone.utc)), current)


def settle_review(database, card_id, review_id, *, queue_entry_id, revision, source_attempt_id):
    """Completion/reconciliation can consume an issuance; they never create one."""
    if (not storage.is_postgres(database) or queue_entry_id is None
            or revision is None or not source_attempt_id): return False
    bound = storage.bound_attempt(database, LEGACY_WORKSPACE_ID, attempt_identity(queue_entry_id, revision))
    if bound is None: return False
    current = current_fact(database, card_id, lock=True)
    if current is None or tuple(bound)[1:5] != (card_id, revision, *accepted_storage_identity(current)): return False
    review = storage.settlement_review(database, review_id, card_id)
    origin = storage.settlement_origin(database, queue_entry_id, card_id, revision)
    if review is None or origin is None or review["source_ref"] != "attempt:" + source_attempt_id: return False
    outcome = ReviewOutcome(str(review_id), AttemptAuthority(*tuple(bound)),
        datetime.fromisoformat(review["reviewed_at"]), "correct" if review["rating"] == "correct" else "again",
        guided=bool(review["guided"]), failed=bool(origin[0]), invalidated=review["invalidated_at"] is not None,
        source_kind=review["source_kind"])
    result = repository(database).settle(outcome, current)
    clear_priority(database, card_id)
    return result


def invalidate_review(database, card_id, review_id, reason):
    """Borrow the invalidation writer's card/day locks and transaction."""
    retained = storage.invalidated_review(database, review_id, card_id)
    if not retained or retained[0] is None:
        raise ValueError("The review owner must invalidate the actual review in this transaction")
    from tempo_legacy_database.adapters.study.game_feedback import invalidate_saved_review
    invalidate_saved_review(database, card_id, review_id)
    repository(database).invalidate_settlement(str(review_id), reason)
    clear_priority(database, card_id)


def candidate(current, *, buried=False, protected=False):
    return Candidate(current.card_id, current.card_revision, *accepted_storage_identity(current), current.route_id,
        True, True, buried=buried, protected=protected)


def has_current_miss(database, card_id):
    current = current_fact(database, card_id)
    return bool(current and repository(database).has_current_miss(candidate(current)))


def clear_priority(database, card_id):
    if not has_current_miss(database, card_id):
        storage.clear_priority(database, card_id, BRIDGE_MISS_REASON)


def admit_card(database, payload):
    """One bounded command; existing backend writer is a prelocked capability."""
    card_id, day = payload["card_id"], date.today().isoformat()
    storage.lock_card(database, card_id)
    lock_queue_date_for_position(database, day)
    return admit_prelocked(database, card_id, day)


def admit_prelocked(database, card_id, day):
    """Card and day locks must already be held, before any remediation lock."""
    current = current_fact(database, card_id, lock=True)
    owned = repository(database)
    if current is None:
        result = AdmissionResult("deferred", "legacy_authority_not_current")
        owned.lock()
        owned.record_admission(card_id, result)
        clear_priority(database, card_id)
    else:
        from .queue_application import presentation_rows
        from . import frozen_opening_order
        # This port only admits already-introduced current review cards. Frozen
        # ranks/legacy-origin uncertainty select new introductions, not whether
        # an existing card can be reviewed. Keep the activation epoch fence and
        # protected-slot presenter, without applying introduction-only validity.
        frozen_token = frozen_opening_order.activation(database)
        frozen_valid = frozen_token == frozen_opening_order.activation(database, lock=True)
        rows = presentation_rows(database, day)
        target = [row for row in rows if row["card_id"] == card_id]
        from .queue_ordering import _protected_queue_ids
        protected = _protected_queue_ids(rows)
        buried = storage.buried_entry(database, card_id, day) is not None
        class PrelockedWriter:
            def apply(self, proposal):
                from .review_service import ensure_card_queued_after
                if not frozen_valid:
                    return AdmissionResult("deferred", "frozen_order_changed")
                ensure_card_queued_after(database, card_id, 0, priority_reason=BRIDGE_MISS_REASON,
                    queue_date=day, protect_presented=True, preserve_order=False)
                entry = storage.queued_entry(database, card_id, day)
                return AdmissionResult("applied", queue_entry_id=entry[0])
        result = apply_admission(owned, candidate(current, buried=buried,
            protected=any(row["id"] in protected for row in target)), PrelockedWriter())
    return {"status": result.status, "reason": result.reason, "queue_entry_id": result.queue_entry_id}


def card_status(database, card_id):
    """Read-only target inspection; never admit, issue, stamp or review."""
    target = storage.target_identity(database)
    work = storage.card_work(database, LEGACY_WORKSPACE_ID, card_id)
    result = dict(target_id=target[0] if target else None, workspace_id=LEGACY_WORKSPACE_ID,
                  card_id=card_id, accepted=work is not None, current=False, admission=None, settlement=None)
    actual = storage.current_revision(database, card_id)
    result["actual_card_revision"] = actual[0] if actual else None
    if work is None: return result
    current = current_fact(database, card_id)
    active = storage.active_obligations(database, LEGACY_WORKSPACE_ID, card_id)
    outstanding = repository(database).has_outstanding(card_id)
    retained = storage.latest_contribution(database, LEGACY_WORKSPACE_ID, card_id)
    payload = json.loads(retained[0])
    day = date.today().isoformat()
    queue = storage.queued_entry(database, card_id, day)
    from .queue_application import MISS_REASON
    head = storage.available_head(database, day, MISS_REASON)
    from .queue_ordering import _protected_queue_ids
    from .queue_application import presentation_rows
    protected = _protected_queue_ids(presentation_rows(database, day))
    origin = storage.issued_origin(database, queue[0], card_id, payload["comparison"]["card_revision"]) if queue else None
    bound = storage.attempt_binding_exists(database, LEGACY_WORKSPACE_ID, attempt_identity(queue[0], payload["comparison"]["card_revision"])) if queue else None
    result.update(current=current is not None, repertoire_id=payload["authority"]["repertoire_id"],
        card_revision=payload["comparison"]["card_revision"], played_move=payload["encounter"]["observed_move"],
        expected_moves=payload["comparison"]["expected_moves"],
        admission=dict(state="queued" if queue else "deferred" if work["work_state"] == "deferred" else "not_queued",
            last_result=work["work_state"], reason=work["reason"], queue_entry_id=queue[0] if queue else None,
            requires_fresh_attempt=bool(origin and origin[0] and not bound),
            waiting_behind_protected_head=bool(queue and head and head[0] != queue[0] and head[0] in protected)),
        settlement="unsettled" if outstanding else "settled" if active else "inactive")
    return result
