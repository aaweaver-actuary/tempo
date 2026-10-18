"""Learning's adapters for remediation; the gateway remains transaction owner."""

from datetime import datetime, timezone
import json

from psycopg.rows import tuple_row
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
from .database import current_database
from .durable_tasks import enqueue_task_in_transaction


class BorrowedConnection:
    """Per-cursor tuple rows without changing the runtime's shared row factory."""

    def __init__(self, database):
        self.database = database
        self.info = database.raw.info

    def execute(self, statement, parameters=()):
        return self.database.raw.cursor(row_factory=tuple_row).execute(
            statement, parameters
        )


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
        row = self.database.execute(
            "SELECT publication_id,publication_generation FROM cards WHERE id=?",
            (card_id,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return self.decision(card_id, None, row[0], row[1])

    def decision(self, card_id, decision_id, publication_id, publication_generation):
        from .publication_bridge import physical_id
        authored_id = card_id
        card_id = physical_id(self.database, card_id)
        alias = self.database.execute("SELECT content_card_id FROM content_card_aliases WHERE learning_card_id=? AND publication_id=?", (card_id,publication_id)).fetchone()
        if alias:
            authored_id = alias[0]
        row = self.database.execute(
            """SELECT publication.payload_json,card.revision,card.serving_active,card.archived,
                      card.superseded_by FROM cards card
               JOIN accepted_publications publication ON publication.publication_id=card.publication_id
                 AND publication.generation=card.publication_generation
               JOIN serving_publications serving ON serving.publication_id=publication.publication_id
                 AND serving.generation=publication.generation
               WHERE card.id=? AND card.publication_id=? AND card.publication_generation=?
                 AND publication.state='active'""",
            (card_id, publication_id, publication_generation),
        ).fetchone()
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
        database.execute(
            "UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? AND gameplay_priority_reason=?",
            (card_id, MISS_REASON),
        )


def receive(database, delivery, *, background=False, historical_evidence_revision=None):
    from .admission import lock_card

    owned = repository(database, background=background)
    from .publication_bridge import physical_id
    affected = {physical_id(database,obligation.card_id) for obligation in delivery.obligations}
    for withdrawal in getattr(delivery, "withdrawals", ()):
        row = database.execute_native(
            "SELECT card_id FROM remediation_obligation_heads WHERE workspace_id=%s AND obligation_id=%s",
            (workspace_id(), withdrawal.obligation_id),
        ).fetchone()
        if row:
            affected.add(row[0])
    for card_id in sorted(affected):
        lock_card(database, card_id)
    before = database.execute_native(
        "SELECT revision FROM remediation_workspaces WHERE workspace_id=%s",
        (workspace_id(),),
    ).fetchone()
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
    existing = database.execute_native(
        "SELECT issued_at FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s",
        (workspace_id(), identity),
    ).fetchone()
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
    bound = database.execute_native(
        """SELECT attempt_id,card_id,card_revision,publication_id,publication_generation,issued_at
           FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""",
        (workspace_id(), attempt_identity(queue_entry_id, revision)),
    ).fetchone()
    if not bound or tuple(bound)[1:5] != (
        current.card_id,
        current.card_revision,
        current.publication_id,
        current.publication_generation,
    ):
        return
    review = database.execute(
        "SELECT rating,guided,reviewed_at,source_kind,invalidated_at FROM reviews WHERE id=? AND card_id=?",
        (review_id, card_id),
    ).fetchone()
    origin = database.execute(
        "SELECT attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? AND revision=? AND issued_as_head=1",
        (queue_entry_id, card_id, revision),
    ).fetchone()
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
    row = database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if row is None or current is None:
        return None
    route = (
        database.execute(
            """SELECT step.repertoire_id FROM opening_graph_steps step
           JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
             AND publication.generation=step.generation
           JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
           JOIN repertoire_lines line ON line.id=step.line_id
           WHERE step.card_id=? AND step.generation=? AND publication.state='ready'
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                 WHERE block.card_id=step.card_id AND block.repertoire_id=step.repertoire_id)
           ORDER BY repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index LIMIT 1""",
            (card_id, current.publication_generation),
        ).fetchone()
        if row["content_type"] == "opening"
        else None
    )
    from .admission import protected_queue_ids

    protected = protected_queue_ids(database, queue_day)
    entries = database.execute(
        "SELECT id,status FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card_id, queue_day),
    ).fetchall()
    deleted = database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
    ).fetchone()
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
    from .queue_position_lock import lock_queue_date_for_position
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
                    entry = database.execute(
                        "SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued' ORDER BY position,id LIMIT 1",
                        (proposal.card_id, day),
                    ).fetchone()
                    return AdmissionResult(
                        "applied" if applied else "rejected",
                        None if applied else "admission_recheck_rejected",
                        entry[0] if applied and entry else None,
                    )

            apply_admission(owned, selected, Writer())
    payload["after_card"] = prepared.cursor
    return prepared.has_more
