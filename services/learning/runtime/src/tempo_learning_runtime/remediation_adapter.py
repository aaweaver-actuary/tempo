"""Learning's adapters for remediation; the gateway remains transaction owner."""

from datetime import datetime, timezone
import json

from tempo_learning_database.adapters import remediation_adapter as storage
from tempo_learning_database.adapters.remediation_adapter import BorrowedConnection
from tempo_chess_core import canonical_fen_key
from tempo_learning_remediation import (
    AcceptedDecision,
    AdmissionResult,
    AttemptAuthority,
    Candidate,
    PostgresRepository,
    ReceiveContext,
    ReviewOutcome,
    apply_admission,
    prepare_admissions,
)

from .clock import date
from tempo_learning_database.connections import current_database
from .durable_tasks import enqueue_task_in_transaction


def workspace_id():
    return current_database.get().configuration.workspace_id


def repository(database, *, background=False):
    return PostgresRepository(
        BorrowedConnection(database), workspace_id(), background=background
    )


class AcceptedDefinitions:
    def __init__(self, database):
        self.database = database

    def for_card(self, card_id):
        row = storage.card_publication_identity(self.database, card_id)
        if not row or row[0] is None:
            return None
        return self.decision(card_id, None, row[0], row[1])

    def decision(self, card_id, decision_id, publication_id, publication_generation):
        from .publication_bridge import physical_id
        authored_id = card_id
        card_id = physical_id(self.database, card_id)
        alias = storage.content_alias(self.database, card_id, publication_id)
        if alias:
            authored_id = alias[0]
        row = storage.accepted_publication_facts(self.database, card_id, publication_id, publication_generation)
        if row is None:
            return None
        document=json.loads(row[0])
        publication = document["publication"]
        if publication["workspace_id"] != workspace_id():
            return None
        definition = next(
            (fact for fact in publication["definitions"] if fact["card_id"] == authored_id),
            None,
        )
        if definition is None:
            return None
        specification = definition["specification"]
        if (
            specification["type"] != "move_line"
            or specification["grading_policy"] != "reference"
        ):
            return None
        selected_decision=decision_id or definition['decision_id']
        if document.get('schema_version')==2:
            projections={binding.get('exercise_ply',0) for route in document.get('routes',[]) for binding in route['bindings']
                if binding['card_id']==authored_id and (binding.get('decision_id') or definition['decision_id'])==selected_decision}
            if len(projections)!=1:
                return None
            offset=next(iter(projections))
        elif selected_decision==definition['decision_id']:
            offset=0
        else:
            return None
        from .publication_bridge import project_decision
        decision_fen,accepted_moves=project_decision(definition['starting_fen'],specification['accepted_lines'],offset)
        return AcceptedDecision(
            card_id,
            definition["exercise_revision"],
            selected_decision,
            publication_id,
            publication_generation,
            canonical_fen_key(decision_fen),
            accepted_moves,
            workspace_id(),
            active=bool(
                row[2]
                and not row[3]
                and not row[4]
                and row[1] == definition["exercise_revision"]
            ),
            aliases=(authored_id,) if authored_id != card_id else (),
        )


def request_remediation(database, queue_day=None):
    return enqueue_task_in_transaction(
        database,
        "remediation",
        "current",
        {"queue_date": queue_day or date.today().isoformat(), "after_card": ""},
        priority=10,
    )


def clear_priority(database, card_id):
    from .real_game_feedback import MISS_REASON, has_outstanding_real_game_miss

    if not has_outstanding_real_game_miss(database, card_id):
        storage.clear_priority(database, card_id, MISS_REASON)


def receive(database, delivery, *, background=False, historical_evidence_revision=None):
    from .admission import lock_card

    owned = repository(database, background=background)
    from .publication_bridge import physical_id
    affected = {physical_id(database,obligation.card_id) for obligation in delivery.obligations}
    for withdrawal in getattr(delivery, "withdrawals", ()):
        row = storage.withdrawal_card(database, workspace_id(), withdrawal.obligation_id)
        if row:
            affected.add(row[0])
    for card_id in sorted(affected):
        lock_card(database, card_id)
    before = storage.workspace_revision(database, workspace_id())
    context = ReceiveContext(
        workspace_id(),
        delivery.game_id,
        delivery.game_revision,
        delivery.evidence_id,
        delivery.evidence_revision,
        datetime.now(timezone.utc),
        AcceptedDefinitions(database),
    )
    acknowledgement = owned.receive(delivery, context,
        historical_evidence_revision=historical_evidence_revision,
        v1_transport_stale=True)
    if acknowledgement.status == "completed" and acknowledgement.learning_revision > (
        before[0] if before else 0
    ):
        request_remediation(database)
    return acknowledgement


def attempt_identity(queue_entry_id, revision):
    return f"queue:{queue_entry_id}:revision:{revision}"


def bind_issued_attempt(database, queue_entry_id, card_id, revision):
    current = AcceptedDefinitions(database).for_card(card_id)
    if current is None or not current.active:
        return
    owned = repository(database)
    identity = attempt_identity(queue_entry_id, revision)
    existing = storage.attempt_issued_at(database, workspace_id(), identity)
    issued_at = existing[0] if existing else datetime.now(timezone.utc)
    owned.bind_attempt(
        AttemptAuthority(
            identity,
            card_id,
            revision,
            current.publication_id,
            current.publication_generation,
            issued_at,
        ),
        current,
    )


def settle_review(database, card_id, review_id, *, queue_entry_id=None, revision=None,
                  request_followup=None):
    """Only a previously bound, compatible authoritative queue attempt may settle."""
    if queue_entry_id is None or revision is None:
        return
    current = AcceptedDefinitions(database).for_card(card_id)
    if current is None or not current.active:
        return
    bound = storage.bound_attempt(database, workspace_id(), attempt_identity(queue_entry_id, revision))
    if not bound or tuple(bound)[1:5] != (
        current.card_id,
        current.card_revision,
        current.publication_id,
        current.publication_generation,
    ):
        return
    review = storage.settlement_review(database, review_id, card_id)
    origin = storage.issued_origin(database, queue_entry_id, card_id, revision)
    if not review or origin is None:
        return
    owned = repository(database)
    outcome = ReviewOutcome(
        str(review_id),
        AttemptAuthority(*tuple(bound)),
        datetime.fromisoformat(review[2]),
        "correct" if review[0] == "correct" else "again",
        guided=bool(review[1]),
        failed=bool(origin[0]),
        invalidated=review[4] is not None,
        source_kind=review[3],
    )
    owned.settle(outcome, current)
    clear_priority(database, card_id)
    # A miss arriving after issuance needs a new attempt, even after a clean review.
    if owned.has_current_miss(candidate(database, card_id, date.today().isoformat())):
        if request_followup is None:
            request_remediation(database)
        else:
            request_followup(date.today().isoformat())


def candidate(database, card_id, queue_day):
    current = AcceptedDefinitions(database).for_card(card_id)
    row = storage.card(database, card_id)
    if row is None or current is None:
        return None
    route = (
        storage.opening_route(database, card_id, current.publication_generation)
        if row["content_type"] == "opening"
        else None
    )
    from .admission import protected_queue_ids

    protected = protected_queue_ids(database, queue_day)
    entries = storage.queue_entries(database, card_id, queue_day)
    deleted = storage.deleted_card(database, card_id)
    return Candidate(
        card_id,
        row["revision"],
        current.publication_id,
        current.publication_generation,
        route[0] if route else row["repertoire_id"] or "",
        row["introduced_at"] is not None,
        True,
        content_valid=not row["pending_validation"]
        and (row["content_type"] != "opening" or route is not None),
        current_revision=row["revision"] == current.card_revision,
        retired=not current.active,
        deleted=deleted is not None,
        buried=any(entry[1] == "buried" for entry in entries),
        protected=any(entry[0] in protected for entry in entries),
    )


class CandidateFacts:
    def __init__(self, database, queue_day):
        self.database, self.queue_day = database, queue_day

    def candidates(self, items):
        return tuple(
            value
            for item in items
            if (value := candidate(self.database, item.card_id, self.queue_day))
            is not None
        )


def maintain_one(database, payload):
    from .admission import admit_prelocked, lock_card
    from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
    from .real_game_feedback import MISS_REASON

    owned = repository(database, background=True)
    day = payload["queue_date"]
    prepared = prepare_admissions(
        owned,
        CandidateFacts(database, day),
        payload.get("after_card", ""),
        page_size=1,
        max_pages=1,
    )
    if prepared.cursor and prepared.cursor != payload.get("after_card", ""):
        # The cursor includes settled/rejected cards too. Clear their presentation
        # priority under the same bounded lock order, without discarding history.
        lock_card(database, prepared.cursor)
        lock_queue_date_for_position(database, day)
        owned.lock()
        clear_priority(database, prepared.cursor)
        selected = candidate(database, prepared.cursor, day)
        if prepared.candidates and selected is not None:

            class Writer:
                def apply(self, proposal):
                    applied = admit_prelocked(
                        database, proposal.card_id, day, MISS_REASON, proposal=proposal
                    )
                    entry = storage.admitted_entry(database, proposal.card_id, day)
                    return AdmissionResult(
                        "applied" if applied else "rejected",
                        None if applied else "admission_recheck_rejected",
                        entry[0] if applied and entry else None,
                    )

            apply_admission(owned, selected, Writer())
    payload["after_card"] = prepared.cursor
    return prepared.has_more
