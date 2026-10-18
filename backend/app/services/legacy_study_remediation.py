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

from ..postgres_store import PostgresConnection
from ..queue_position_lock import lock_queue_date_for_position
from .legacy_game_evidence_scope import LEGACY_WORKSPACE_ID, BRIDGE_MISS_REASON


def repository(database):
    from ..game_evidence_commands import BorrowedTupleConnection
    if database.read_only:
        from psycopg.rows import tuple_row
        class ReadProjection:
            info = database.raw.info
            def execute(self, statement, parameters=()):
                if not statement.lstrip().upper().startswith("SELECT "):
                    raise RuntimeError("Legacy status is a read-only projection")
                return database.raw.cursor(row_factory=tuple_row).execute(statement, parameters)
        return PostgresRepository(ReadProjection(), LEGACY_WORKSPACE_ID)
    return PostgresRepository(BorrowedTupleConnection(database), LEGACY_WORKSPACE_ID)


# Only owner-controlled SQL, never a producer-supplied predicate. The retained
# proof is not a claim that legacy cards have Content publication columns.
CURRENT_FACT_SQL = """SELECT DISTINCT ON (contribution.publication_id,contribution.publication_generation)
    contribution.payload_json,card.introduced_at
 FROM remediation_obligation_heads head
 JOIN remediation_contributions contribution USING(workspace_id,obligation_id,obligation_revision)
 JOIN cards card ON card.id=contribution.card_id AND card.revision=contribution.card_revision
 JOIN repertoires repertoire ON repertoire.id=contribution.payload_json::jsonb->'authority'->>'repertoire_id'
 JOIN repertoire_game_scope scope ON scope.id=1
 JOIN legacy_learning_identity identity ON identity.id=1
 JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
 JOIN imported_games game ON game.id=contribution.game_id
 WHERE head.workspace_id=%s AND head.card_id=%s AND head.active
 AND contribution.publication_id LIKE 'legacy-graph:%%'
 AND contribution.payload_json::jsonb->'authority'->>'kind'='legacy_graph'
 AND contribution.payload_json::jsonb->'authority'->>'workspace_id'=identity.workspace_id
 AND contribution.payload_json::jsonb->'authority'->>'target_id'=identity.target_id
 AND repertoire.scope_source_revision=(contribution.payload_json::jsonb->'authority'->>'scope_source_revision')::bigint
 AND repertoire.canonical_prefix_revision=(contribution.payload_json::jsonb->'authority'->>'canonical_prefix_revision')::bigint
 AND repertoire.canonical_prefix_moves_json::jsonb='[]'::jsonb
 AND scope.generation=(contribution.payload_json::jsonb->'authority'->>'catalogue_generation')::bigint
 AND publication.generation=contribution.publication_generation AND publication.state='ready'
 AND game.adaptive_excluded=0
 AND game.provider=contribution.payload_json::jsonb->'game_source'->>'provider'
 AND game.provider_game_id=contribution.payload_json::jsonb->'game_source'->>'provider_game_id'
 AND game.content_hash=contribution.payload_json::jsonb->'game_source'->>'content_hash'
 AND card.content_type='opening' AND card.revision=1 AND card.introduced_at IS NOT NULL
 AND card.state IN ('learning','mature')
 AND card.archived=0 AND card.pending_validation=0 AND card.superseded_by IS NULL
 AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id)
 AND NOT EXISTS(SELECT 1 FROM repertoire_cards other WHERE other.card_id=card.id AND other.repertoire_id<>repertoire.id)
 AND (card.repertoire_id IS NULL OR card.repertoire_id=repertoire.id)
 AND (card.repertoire_id=repertoire.id OR EXISTS(SELECT 1 FROM repertoire_cards link
     WHERE link.card_id=card.id AND link.repertoire_id=repertoire.id))
 AND EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=repertoire.id
     AND step.generation=publication.generation AND step.card_id=card.id
     AND step.line_id=contribution.payload_json::jsonb->'comparison'->>'route_id'
     AND step.starting_fen=card.start_fen AND step.moves_json::jsonb=card.moves_json::jsonb
     AND step.trained_color=card.trained_color)
 ORDER BY contribution.publication_id,contribution.publication_generation,contribution.obligation_revision DESC
 LIMIT 2"""

# Current imported-game authority is supplied by its owner. If any active source
# for this card is corrected/excluded/missing, defer the whole card until the
# producer replaces or withdraws it. Do not let another game's valid projection
# launder that stale incident through a card-level shared predicate.
SOURCE_FENCE_SQL = """SELECT DISTINCT contribution.game_id,
    COALESCE(game.provider=contribution.payload_json::jsonb->'game_source'->>'provider'
      AND game.provider_game_id=contribution.payload_json::jsonb->'game_source'->>'provider_game_id'
      AND game.content_hash=contribution.payload_json::jsonb->'game_source'->>'content_hash'
      AND game.color=contribution.payload_json::jsonb->'decision_scope_proof'->'scope'->>'learner_color'
      AND game.result IN ('win','loss','draw','1-0','0-1','1/2-1/2')
      AND game.adaptive_excluded=0,false) source_current
 FROM remediation_obligation_heads head
 JOIN remediation_contributions contribution USING(workspace_id,obligation_id,obligation_revision)
 JOIN remediation_game_heads accepted_game ON accepted_game.workspace_id=head.workspace_id
    AND accepted_game.game_id=contribution.game_id AND accepted_game.game_revision=contribution.game_revision
 LEFT JOIN imported_games game ON game.id=contribution.game_id
 WHERE head.workspace_id=%s AND head.card_id=%s AND head.active
 ORDER BY contribution.game_id LIMIT 4"""


def current_fact(database, card_id, *, lock=False):
    rows = database.execute_native(CURRENT_FACT_SQL, (LEGACY_WORKSPACE_ID, card_id)).fetchall()
    if len(rows) != 1: return None
    payload = json.loads(rows[0]["payload_json"])
    # Early unscoped prototypes remain retained evidence, never a new grant.
    if "decision_scope_proof" not in payload: return None
    authority = LegacyAuthorityToken.model_validate(payload["authority"])
    sources = database.execute_native(SOURCE_FENCE_SQL, (LEGACY_WORKSPACE_ID, card_id)).fetchall()
    if not 1 <= len(sources) <= 3 or any(not row["source_current"] for row in sources): return None
    if lock:
        # A normal review must not wait on optional bridge authority after its
        # card/day locks. Busy or changing source simply cannot grant a binding
        # or settlement. Held share locks fence the subsequent exact re-read.
        admitted = database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
            ("tempo:opening-graph:" + authority.repertoire_id,)).fetchone()
        if not admitted or not admitted[0]: return None
        held = database.execute_native("""SELECT 1 FROM repertoires repertoire
            CROSS JOIN repertoire_game_scope scope CROSS JOIN legacy_learning_identity identity
            JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
            JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
            JOIN background_tasks graph ON graph.kind='opening_graph_rebuild' AND graph.deduplication_key=repertoire.id
            JOIN background_tasks scan ON scan.kind='integrity_scan' AND scan.deduplication_key=repertoire.id
            JOIN imported_games game ON game.id=%s
            WHERE repertoire.id=%s AND scope.id=1 AND identity.id=1
            FOR SHARE OF repertoire,scope,identity,publication,integrity,graph,scan,game SKIP LOCKED""",
            (payload["game_id"], authority.repertoire_id)).fetchone()
        if not held: return None
        games = database.execute_native("SELECT id FROM imported_games WHERE id IN (" +
            ",".join("%s" for _ in sources) + ") ORDER BY id FOR SHARE SKIP LOCKED",
            tuple(row["game_id"] for row in sources)).fetchall()
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
    if not isinstance(database, PostgresConnection): return
    origin = database.execute("SELECT issued_as_head FROM queue_attempt_origins "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?", (entry_id, card_id, revision)).fetchone()
    if origin is None or origin[0]: return  # Never retrofit a pre-bridge/offline origin.
    current = current_fact(database, card_id, lock=True)
    if current is None or current.card_revision != revision: return
    repository(database).bind_attempt(AttemptAuthority(attempt_identity(entry_id, revision), card_id, revision,
        *accepted_storage_identity(current), datetime.now(timezone.utc)), current)


def settle_review(database, card_id, review_id, *, queue_entry_id, revision, source_attempt_id):
    """Completion/reconciliation can consume an issuance; they never create one."""
    if (not isinstance(database, PostgresConnection) or queue_entry_id is None
            or revision is None or not source_attempt_id): return False
    bound = database.execute_native("""SELECT attempt_id,card_id,card_revision,publication_id,publication_generation,issued_at
        FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""",
        (LEGACY_WORKSPACE_ID, attempt_identity(queue_entry_id, revision))).fetchone()
    if bound is None: return False
    current = current_fact(database, card_id, lock=True)
    if current is None or tuple(bound)[1:5] != (card_id, revision, *accepted_storage_identity(current)): return False
    review = database.execute("SELECT rating,guided,reviewed_at,source_kind,invalidated_at,source_ref FROM reviews "
        "WHERE id=? AND card_id=?", (review_id, card_id)).fetchone()
    origin = database.execute("SELECT attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? "
        "AND card_id=? AND revision=? AND issued_as_head=1", (queue_entry_id, card_id, revision)).fetchone()
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
    retained = database.execute("SELECT invalidated_at FROM reviews WHERE id=? AND card_id=?",
                                (review_id, card_id)).fetchone()
    if not retained or retained[0] is None:
        raise ValueError("The review owner must invalidate the actual review in this transaction")
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
        database.execute("UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? "
                         "AND gameplay_priority_reason=?", (card_id, BRIDGE_MISS_REASON))


def admit_card(database, payload):
    """One bounded command; existing backend writer is a prelocked capability."""
    card_id, day = payload["card_id"], date.today().isoformat()
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:card-edit:" + card_id,))
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
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
        buried = database.execute("SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=? AND status='buried' LIMIT 1",
                                  (card_id, day)).fetchone() is not None
        class PrelockedWriter:
            def apply(self, proposal):
                from .review_service import ensure_card_queued_after
                if not frozen_valid:
                    return AdmissionResult("deferred", "frozen_order_changed")
                ensure_card_queued_after(database, card_id, 0, priority_reason=BRIDGE_MISS_REASON,
                    queue_date=day, protect_presented=True, preserve_order=False)
                entry = database.execute("SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? "
                    "AND status='queued' ORDER BY position,id LIMIT 1", (card_id, day)).fetchone()
                return AdmissionResult("applied", queue_entry_id=entry[0])
        result = apply_admission(owned, candidate(current, buried=buried,
            protected=any(row["id"] in protected for row in target)), PrelockedWriter())
    return {"status": result.status, "reason": result.reason, "queue_entry_id": result.queue_entry_id}


def card_status(database, card_id):
    """Read-only target inspection; never admit, issue, stamp or review."""
    target = database.execute_native("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1").fetchone()
    work = database.execute_native("SELECT sequence,work_state,reason,queue_entry_id FROM remediation_cards "
        "WHERE workspace_id=%s AND card_id=%s", (LEGACY_WORKSPACE_ID, card_id)).fetchone()
    result = dict(target_id=target[0] if target else None, workspace_id=LEGACY_WORKSPACE_ID,
                  card_id=card_id, accepted=work is not None, current=False, admission=None, settlement=None)
    actual = database.execute("SELECT revision FROM cards WHERE id=?", (card_id,)).fetchone()
    result["actual_card_revision"] = actual[0] if actual else None
    if work is None: return result
    current = current_fact(database, card_id)
    active = database.execute_native("SELECT COUNT(*) FROM remediation_obligation_heads WHERE workspace_id=%s "
        "AND card_id=%s AND active", (LEGACY_WORKSPACE_ID, card_id)).fetchone()[0]
    outstanding = repository(database).has_outstanding(card_id)
    retained = database.execute_native("SELECT payload_json FROM remediation_contributions WHERE workspace_id=%s "
        "AND card_id=%s ORDER BY obligation_revision DESC,obligation_id LIMIT 1",
        (LEGACY_WORKSPACE_ID, card_id)).fetchone()
    payload = json.loads(retained[0])
    day = date.today().isoformat()
    queue = database.execute("SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued' "
        "ORDER BY position,id LIMIT 1", (card_id, day)).fetchone()
    from .queue_application import available_attempt_sql
    head = database.execute(available_attempt_sql() + " ORDER BY q.position,q.id LIMIT 1", (day,)).fetchone()
    from .queue_ordering import _protected_queue_ids
    from .queue_application import presentation_rows
    protected = _protected_queue_ids(presentation_rows(database, day))
    origin = database.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=? "
        "AND card_id=? AND revision=?", (queue[0], card_id, payload["comparison"]["card_revision"])).fetchone() if queue else None
    bound = database.execute_native("SELECT 1 FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s",
        (LEGACY_WORKSPACE_ID, attempt_identity(queue[0], payload["comparison"]["card_revision"]))).fetchone() if queue else None
    result.update(current=current is not None, repertoire_id=payload["authority"]["repertoire_id"],
        card_revision=payload["comparison"]["card_revision"], played_move=payload["encounter"]["observed_move"],
        expected_moves=payload["comparison"]["expected_moves"],
        admission=dict(state="queued" if queue else "deferred" if work["work_state"] == "deferred" else "not_queued",
            last_result=work["work_state"], reason=work["reason"], queue_entry_id=queue[0] if queue else None,
            requires_fresh_attempt=bool(origin and origin[0] and not bound),
            waiting_behind_protected_head=bool(queue and head and head[0] != queue[0] and head[0] in protected)),
        settlement="unsettled" if outstanding else "settled" if active else "inactive")
    return result
