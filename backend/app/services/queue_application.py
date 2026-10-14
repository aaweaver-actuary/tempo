"""Queue eligibility and projection effects on the caller-owned connection.

Transaction, lock, issuance and durable-slice ownership stay with existing callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, NotRequired, TypedDict

from ..postgres_store import PostgresConnection
from .durable_tasks import enqueue_task_in_transaction
from .opening_capacity_frontier import plan_prioritized_opening_admissions
from . import frozen_opening_order
from .opening_progression import PRACTICED_OPENING_PARENT_SQL
from .pgn import ends_on_trained_move
from .queue_ordering import _queue_membership_hash, _plan_daily_queue_order
from .real_game_feedback import (
    MISS_REASON, outstanding_miss_query, outstanding_miss_sql,
    has_outstanding_real_game_miss, promote_real_game_card,
    clear_satisfied_miss_priority, prioritize_queued_misses,
)
from .review_service import preserve_daily_queue_order
from .tactical_catalog import seed_tactical_introductions


class OpeningCapacityRoutePayload(TypedDict):
    repertoire_id: str
    generation: int
    line_id: str
    decision_index: int
    parent_card_id: str
    parent_introduced: bool
    earlier_unreviewed_card_ids: tuple[str, ...]
    current_ready_publication: bool
    structurally_valid: bool
    single_decision: bool


class AdmittedOpeningProposal(TypedDict):
    """Existing durable opening-plan shape; annotations do not alter checkpoints."""

    repertoire_id: str
    card_id: str
    reason: str | None
    capacity_route: NotRequired[OpeningCapacityRoutePayload]


def reconcile_unseen_queue(db, day, limit, repertoire_limits=None):
    """Trim legacy queues that eagerly admitted every unseen card."""
    if frozen_opening_order.activation(db)[0] is not None:
        return
    rows = db.execute(
        """
        SELECT q.id,q.card_id,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id FROM daily_queue q
        JOIN cards c ON c.id=q.card_id
        WHERE q.queue_date=? AND q.status='queued' AND c.content_type='opening'
          AND c.repertoire_id<>'__retained_cards__'
          AND COALESCE(q.admission_kind,'')!='explicit'
          AND (c.introduced_at IS NULL OR c.introduced_at=?)
          AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
        ORDER BY q.position,q.id
    """,
        (day, day),
    ).fetchall()
    # Historical introductions consume allowance even if eligibility changes later.
    introduced_by_repertoire = dict(
        db.execute(
            """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id) FROM cards c
               LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0
               WHERE c.content_type='opening' AND c.introduced_at=?
                 AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
               GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
            (day,),
        ).fetchall()
    )
    for row in rows:
        repertoire_id = row["repertoire_id"]
        introduced = introduced_by_repertoire.get(repertoire_id, 0)
        daily_limit = repertoire_limits.get(repertoire_id, limit) if repertoire_limits is not None else limit
        if introduced < daily_limit or has_outstanding_real_game_miss(db, row["card_id"]):
            db.execute(
                "UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",
                (day, row["card_id"]),
            )
            introduced_by_repertoire[repertoire_id] = introduced + 1
        else:
            db.execute("DELETE FROM daily_queue WHERE id=?", (row["id"],))
            db.execute(
                "UPDATE cards SET introduced_at=NULL,state='new' WHERE id=?",
                (row["card_id"],),
            )


_ACTIVE_OPENING_MISS_SQL = outstanding_miss_sql(postgres=True)


_PRIORITY_OPENING_CANDIDATE_BODY = """SELECT DISTINCT c.id,linked.id repertoire_id,c.moves_json,c.due_date,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ?
                    WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
                    'Priority introduction · reached ' || json_extract(opportunity.evidence_json,'$.encounter_count') ||
                    ' times in games, missed ' || json_extract(opportunity.evidence_json,'$.miss_count') || ' times'
                    ELSE p.reason END gameplay_priority_reason,
                  CASE WHEN active_miss.card_id IS NOT NULL THEN ? ELSE p.priority_date END priority_date,
                  COALESCE(published_priority.priority_score,legacy_priority.priority_score) priority_score,
                  COALESCE(published_priority.completed_line_ids_json,legacy_priority.completed_line_ids_json) completed_line_ids_json,
                  COALESCE(published_priority.frontier_decisions_json,legacy_priority.frontier_decisions_json) frontier_decisions_json
           FROM cards c
           JOIN repertoires linked ON (
               linked.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards candidate_link
                   WHERE candidate_link.card_id=c.id
                     AND candidate_link.repertoire_id=linked.id
               )
           )
           LEFT JOIN current_gameplay_card_priorities p ON p.card_id=c.id AND p.priority_date<=?
           LEFT JOIN active_miss ON active_miss.card_id=c.id
           LEFT JOIN current_repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
             AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
             AND opportunity.status='active'
             AND json_extract(opportunity.evidence_json,'$.analysis_based') IS NULL
           LEFT JOIN repertoire_priority_publications publication
             ON publication.repertoire_id=linked.id
           LEFT JOIN current_repertoire_card_priority_generations published_priority
             ON published_priority.card_id=c.id
            AND published_priority.repertoire_id=linked.id
            AND published_priority.generation=publication.generation
           LEFT JOIN current_repertoire_card_introduction_priorities legacy_priority
             ON legacy_priority.card_id=c.id AND legacy_priority.repertoire_id=linked.id
           WHERE c.content_type='opening' AND (c.due_date<=? OR p.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL OR opportunity.id IS NOT NULL)
             AND (c.state='new' OR (c.state='locked' AND (opportunity.id IS NOT NULL OR active_miss.card_id IS NOT NULL)))
             AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
             AND EXISTS(SELECT 1 FROM repertoires r_ok
                        WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                         WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id))
             AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?)"""


def admit_prioritized_opening_cards(db, day: str, limit: int | dict[str, int], maximum: int) -> int:
    """Admit unseen opening cards by impact without changing the active queue."""

    candidates = db.execute(
        f"WITH active_miss AS ({outstanding_miss_query(db)}) " + _PRIORITY_OPENING_CANDIDATE_BODY,
        (MISS_REASON, day, day, day, day),
    ).fetchall()
    # Historical introductions consume allowance even if eligibility changes later.
    introduced_by_repertoire = dict(
        db.execute(
            """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
               FROM daily_queue q JOIN cards c ON c.id=q.card_id
               WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?
               GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
            (day, day),
        ).fetchall()
    )
    candidates = frozen_opening_order.annotate(db, candidates)
    normal_plan = plan_prioritized_opening_admissions(candidates, introduced_by_repertoire, day, limit, miss_reason=MISS_REASON)
    admission_plan = list(normal_plan)
    from .opening_capacity_admission import (
        capacity_expansion_enabled, remaining_opening_allowances, read_capacity_routes,
        capacity_priority_statement, propose_capacity_expansion_from_priority_rows, capacity_proposal_payload, capacity_route_still_valid,
        CAPACITY_READ_BATCH_SIZE,
    )
    if capacity_expansion_enabled():
        from .study_mode import study_only_enabled
        if not study_only_enabled(db):
            limits = limit if isinstance(limit, dict) else {
                row[0]: limit for row in db.execute('SELECT id FROM repertoires').fetchall()
            }
            frozen_active = frozen_opening_order.activation(db)[0] is not None
            remaining = remaining_opening_allowances([] if frozen_active else normal_plan, introduced_by_repertoire, limits)
            if any(remaining.values()):
                normal_parent_ids = tuple(row['id'] for row in candidates) if frozen_active else tuple(row['id'] for _, row in normal_plan)
                routes = tuple(route for repertoire_id in sorted(remaining) if remaining[repertoire_id] > 0
                               for route in read_capacity_routes(
                                   lambda statement, parameters: db.execute(statement, parameters).fetchall(),
                                   repertoire_id, normal_parent_ids,
                               ))
                capacity_rows = []
                card_ids = sorted({card_id for card_id, _ in routes})
                for offset in range(0, len(card_ids), CAPACITY_READ_BATCH_SIZE):
                    page = card_ids[offset:offset + CAPACITY_READ_BATCH_SIZE]
                    capacity_rows.extend(db.execute(
                        f"WITH active_miss AS ({outstanding_miss_query(db)}) "
                        + capacity_priority_statement(_PRIORITY_OPENING_CANDIDATE_BODY)
                        + ' AND c.id IN (' + ','.join('?' for _ in page) + ')',
                        (MISS_REASON, day, day, day, day, *page),
                    ).fetchall())
                if frozen_active:
                    from .opening_capacity_admission import plan_frozen_with_capacity
                    admission_plan = plan_frozen_with_capacity(day, candidates, introduced_by_repertoire, limits, routes,
                        frozen_opening_order.annotate(db, capacity_rows), miss_reason=MISS_REASON)
                for proposal in ([] if frozen_active else propose_capacity_expansion_from_priority_rows(day, normal_plan, remaining, routes, capacity_rows,
                                                   miss_reason=MISS_REASON)):
                    admission_plan.append((proposal.candidate.repertoire_id, {
                        **proposal.candidate.priority_row(), **capacity_proposal_payload(proposal),
                    }))
    next_position = maximum
    for repertoire_id, choice in admission_plan:
        if not frozen_opening_order.proposal_valid(db, {**choice, "card_id": choice["id"], "reason": choice["gameplay_priority_reason"]}):
            continue
        if 'capacity_route' in choice:
            if not capacity_route_still_valid(db, choice, day):
                continue
            # Shared cards are charged only to the writer's chosen membership.
            consumed = db.execute(
                """SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=?
                     AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=?""",
                (day, day, repertoire_id),
            ).fetchone()[0]
            current_limit = db.execute(
                'SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
                'FROM repertoires r CROSS JOIN settings s WHERE r.id=? AND s.id=1', (repertoire_id,),
            ).fetchone()
            requested_limit = limit.get(repertoire_id, 0) if isinstance(limit, dict) else limit
            if current_limit is None or consumed >= min(current_limit[0], requested_limit):
                continue
        next_position += 1
        db.execute(
            """INSERT INTO daily_queue(
                   queue_date,card_id,position,gameplay_priority_reason,admission_repertoire_id
               ) VALUES(?,?,?,?,?)""",
            (day, choice["id"], next_position, choice["gameplay_priority_reason"], repertoire_id),
        )
        db.execute(
            "UPDATE cards SET introduced_at=?,state='learning' WHERE id=?",
            (day, choice["id"]),
        )
        db.execute(
            """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
               WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
                 AND json_extract(evidence_json,'$.analysis_based') IS NULL
                 AND status='active'""",
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
             repertoire_id, choice["id"]),
        )
    return next_position


_OPENING_UNLOCK_ELIGIBILITY_SQL = f"""content_type='opening' AND state='locked' AND archived=0
    AND EXISTS(
        SELECT 1 FROM opening_graph_steps step
        JOIN opening_graph_publications publication
          ON publication.repertoire_id=step.repertoire_id
         AND publication.generation=step.generation
        WHERE step.card_id=cards.id
          AND (step.parent_card_id IS NULL OR EXISTS(
              SELECT 1 FROM cards parent
              WHERE parent.id=step.parent_card_id AND {PRACTICED_OPENING_PARENT_SQL}
          ))
    )"""


_OPENING_UNLOCK_CANDIDATES_SQL = f"""WITH root_candidates AS (
    SELECT DISTINCT card.id FROM opening_graph_publications publication
    JOIN opening_graph_steps step ON step.repertoire_id=publication.repertoire_id
        AND step.generation=publication.generation
    JOIN cards card ON card.id=step.card_id
    WHERE step.parent_card_id IS NULL AND card.content_type='opening'
        AND card.state='locked' AND card.archived=0 AND card.id>?
    ORDER BY card.id LIMIT ?
), practiced_parent_candidates AS (
    SELECT DISTINCT card.id FROM cards parent
    JOIN opening_graph_steps step ON step.parent_card_id=parent.id
    JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
        AND publication.generation=step.generation
    JOIN cards card ON card.id=step.card_id
    WHERE {PRACTICED_OPENING_PARENT_SQL} AND card.content_type='opening'
        AND card.state='locked' AND card.archived=0 AND card.id>?
    ORDER BY card.id LIMIT ?
) SELECT id FROM root_candidates UNION SELECT id FROM practiced_parent_candidates
ORDER BY id LIMIT ?"""


_OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL = f"""WITH root_candidates AS (
    SELECT candidate.id FROM opening_graph_publications publication
    CROSS JOIN LATERAL (
        SELECT DISTINCT card.id FROM opening_graph_steps step
        JOIN cards card ON card.id=step.card_id
        WHERE step.repertoire_id=publication.repertoire_id
          AND step.generation=publication.generation AND step.parent_card_id IS NULL
          AND card.content_type='opening' AND card.state='locked' AND card.archived=0
          AND card.id>%s ORDER BY card.id NULLS FIRST LIMIT %s
    ) candidate
), practiced_parent_candidates AS (
    SELECT candidate.id FROM cards parent
    CROSS JOIN LATERAL (
        SELECT DISTINCT card.id FROM opening_graph_steps step
        JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
          AND publication.generation=step.generation
        JOIN cards card ON card.id=step.card_id
        WHERE step.parent_card_id=parent.id AND card.content_type='opening'
          AND card.state='locked' AND card.archived=0 AND card.id>%s
        ORDER BY card.id NULLS FIRST LIMIT %s
    ) candidate WHERE {PRACTICED_OPENING_PARENT_SQL}
) SELECT id FROM root_candidates UNION SELECT id FROM practiced_parent_candidates
ORDER BY id NULLS FIRST LIMIT %s"""


def _unlock_eligible_opening_cards(
    db, day, *, after_card_id: str | None = None, batch_size: int | None = None,
) -> str | None:
    selected_card_ids: list[str] = []
    has_more_candidates = False
    if batch_size is not None:
        if batch_size < 1:
            raise ValueError("Queue unlock batch size must be positive")
        selector = (db.execute_native if hasattr(db, 'execute_native') else db.execute)
        candidates = selector(
            _OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL if hasattr(db, 'execute_native') else _OPENING_UNLOCK_CANDIDATES_SQL,
            (after_card_id or "", batch_size + 1,
             after_card_id or "", batch_size + 1, batch_size + 1),
        ).fetchall()
        selected_card_ids = [row[0] for row in candidates[:batch_size]]
        has_more_candidates = len(candidates) > batch_size
        if not selected_card_ids:
            return None
    # Recheck the same published eligibility when applying the bounded update.
    update_statement = f"UPDATE cards SET state='new' WHERE {_OPENING_UNLOCK_ELIGIBILITY_SQL}"
    if selected_card_ids:
        update_statement += " AND cards.id IN (" + ",".join("?" for _ in selected_card_ids) + ")"
    db.execute(update_statement, selected_card_ids)
    return selected_card_ids[-1] if has_more_candidates else None


def _block_ineligible_opening_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT c.id FROM cards c
               WHERE c.content_type='opening' AND (
                   COALESCE(c.pending_validation,0)=1 OR NOT EXISTS(
                       SELECT 1 FROM repertoires eligible
                       WHERE (eligible.id=c.repertoire_id OR EXISTS(
                           SELECT 1 FROM repertoire_cards linked
                           WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                       )) AND NOT EXISTS(
                           SELECT 1 FROM repertoire_integrity_card_blocks block
                           WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                       )
                   )
               )
           )""",
        (day,),
    )


def _block_unapproved_defense_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='blocked'
           WHERE queue_date=? AND status='queued' AND card_id IN (
               SELECT card.id FROM cards card
               JOIN threat_training_candidates candidate ON candidate.card_id=card.id
               WHERE card.content_type='defense' AND
                 (card.pending_validation=1 OR candidate.approved_at IS NULL
                  OR candidate.validation_state NOT IN ('engine_supported','validated_control'))
           )""",
        (day,),
    )


def _restore_eligible_due_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
               SELECT c.id FROM cards c
           WHERE c.archived=0 AND c.due_date<=? AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='defense' OR EXISTS(
                     SELECT 1 FROM threat_training_candidates candidate
                     WHERE candidate.card_id=c.id AND candidate.approved_at IS NOT NULL
                       AND candidate.validation_state IN ('engine_supported','validated_control')
                       AND candidate.superseded_at IS NULL
                 ))
                 AND EXISTS(
                     SELECT 1 FROM repertoires eligible
                     WHERE (eligible.id=c.repertoire_id OR EXISTS(
                         SELECT 1 FROM repertoire_cards linked
                         WHERE linked.card_id=c.id AND linked.repertoire_id=eligible.id
                     )) AND NOT EXISTS(
                         SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.repertoire_id=eligible.id AND block.card_id=c.id
                     )
                 )
           )""",
        (day, day),
    )


def _restore_published_study_queue_entries(db, day):
    db.execute(
        """UPDATE daily_queue SET status='queued'
           WHERE queue_date=? AND status='blocked' AND card_id IN (
             SELECT card.id FROM cards card
             JOIN study_exercises exercise ON exercise.id=card.study_exercise_id
             JOIN studies study ON study.id=exercise.study_id
             WHERE card.content_type='study_exercise' AND card.archived=0
               AND card.pending_validation=0 AND study.archived=0
               AND exercise.status='published' AND card.due_date<=?
               AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                              WHERE burial.exercise_id=exercise.id AND burial.study_day=?)
           )""",
        (day, day, day),
    )


_QUEUE_ELIGIBILITY_PHASES = (
    ("unlock_opening", _unlock_eligible_opening_cards),
    ("block_opening", _block_ineligible_opening_queue_entries),
    ("block_defense", _block_unapproved_defense_queue_entries),
    ("restore_due", _restore_eligible_due_queue_entries),
    ("restore_study", _restore_published_study_queue_entries),
)


def _reset_unintroduced_opening_cards(database, queue_date: str) -> None:
    database.execute(
        """UPDATE cards SET state='new' WHERE content_type='opening' AND state='learning'
           AND introduced_at IS NULL AND NOT EXISTS(
               SELECT 1 FROM reviews review WHERE review.card_id=cards.id)"""
    )


def _reset_stale_opening_introductions(database, queue_date: str) -> None:
    database.execute(
        """UPDATE cards SET state='new',introduced_at=NULL
           WHERE content_type='opening' AND repertoire_id<>'__retained_cards__' AND state='learning' AND introduced_at<?
             AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=cards.id)
             AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                            WHERE queue.card_id=cards.id AND queue.queue_date=?)""",
        (queue_date, queue_date),
    )


def seed_queue(db, day):
    for _, apply_phase in _QUEUE_ELIGIBILITY_PHASES:
        apply_phase(db, day)
    seed_tactical_introductions(db, day)
    _reset_unintroduced_opening_cards(db, day)
    _reset_stale_opening_introductions(db, day)
    limit = db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[
        0
    ]
    from ..repertoire_settings import effective_opening_limits
    repertoire_limits = effective_opening_limits(db)
    reconcile_unseen_queue(db, day, limit, repertoire_limits)
    maximum = db.execute(
        "SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?", (day,)
    ).fetchone()[0]
    rows = db.execute(
        """SELECT id FROM cards WHERE due_date<=? AND state IN ('learning','mature') AND archived=0 AND COALESCE(pending_validation,0)=0
           AND (cards.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires rr
                      WHERE (rr.id=cards.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=cards.id AND rc.repertoire_id=rr.id))
                        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                       WHERE block.repertoire_id=rr.id AND block.card_id=cards.id)))
           AND (cards.content_type!='study_exercise' OR (
             EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id
                    WHERE exercise.id=cards.study_exercise_id AND exercise.status='published' AND study.archived=0)
             AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                            WHERE burial.exercise_id=cards.study_exercise_id AND burial.study_day=?)))
           AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id""",
        (day, day, day),
    ).fetchall()
    for offset, row in enumerate(rows, 1):
        db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
            (day, row[0], maximum + offset),
        )
    maximum += len(rows)
    admit_prioritized_opening_cards(db, day, repertoire_limits, maximum)
    outstanding_studied_cards = db.execute(outstanding_miss_query(
        db, additional_where="AND card.state IN ('learning','mature') ORDER BY event.card_id",
    )).fetchall()
    for missed_card in outstanding_studied_cards:
        promote_real_game_card(db, missed_card["card_id"], day)
    for stale_priority in db.execute("SELECT DISTINCT card_id FROM daily_queue WHERE queue_date=? AND gameplay_priority_reason=?", (day, MISS_REASON)).fetchall():
        clear_satisfied_miss_priority(db, stale_priority["card_id"])
    study_allowance = db.execute("SELECT study_new_per_day FROM settings WHERE id=1").fetchone()[0]
    admitted_studies = db.execute(
        """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND c.content_type='study_exercise' AND c.state='new'
             AND q.status IN ('queued','complete','buried')""", (day,),
    ).fetchone()[0]
    remaining_studies = max(0, study_allowance - admitted_studies)
    if remaining_studies:
        next_position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
        study_rows = db.execute(
            """SELECT c.id FROM cards c
               JOIN study_exercises e ON e.id=c.study_exercise_id
               JOIN studies s ON s.id=e.study_id
               WHERE c.content_type='study_exercise' AND c.state='new' AND c.archived=0
                 AND c.pending_validation=0 AND c.due_date<=? AND e.status='published' AND s.archived=0
                 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=?)
                 AND NOT EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.queue_date=?)
               ORDER BY e.created_at,e.id LIMIT ?""",
            (day, day, day, remaining_studies),
        ).fetchall()
        for offset, study_row in enumerate(study_rows):
            db.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind) VALUES(?,?,?,'study_exercise','new')",
                       (day, study_row["id"], next_position + offset))


_QUEUE_RANDOMIZATION_ROWS_SQL = """SELECT q.id,q.card_id,q.position,c.content_type,
                  CASE WHEN q.admission_kind='explicit' THEN 'explicit'
                       WHEN EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.invalidated_at IS NULL) THEN 'review'
                       ELSE 'new' END admission_kind,
                  q.gameplay_priority_reason
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued'
           ORDER BY q.id"""


def randomize_daily_queue(db, day: str, *, preserve_through_entry_id: int | None = None) -> None:
    """Create a stable mixed queue whenever that day's membership changes."""
    rows = db.execute(_QUEUE_RANDOMIZATION_ROWS_SQL, (day,)).fetchall()
    if not rows:
        return
    saved = db.execute(
        "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)
    ).fetchone()
    membership_hash = frozen_opening_order.queue_hash(_queue_membership_hash(rows), frozen_opening_order.activation(db))
    if saved and saved["membership_hash"] == membership_hash and frozen_opening_order.activation(db)[0] is None:
        prioritize_queued_misses(db, day)
        return
    seed, _, ordered = _plan_daily_queue_order(
        rows, day, saved, preserve_through_entry_id=preserve_through_entry_id,
    )
    if frozen_opening_order.activation(db)[0] is not None:
        ordered = sorted(rows, key=lambda row: (row["position"], row["id"]))
    frozen_active = frozen_opening_order.activation(db)[0] is not None
    positions = sorted(row["position"] for row in rows)
    ordered = frozen_opening_order.order_queue_slots(db, ordered, preserve_through_entry_id=preserve_through_entry_id)
    if frozen_active:
        frozen_opening_order.record_queue_changes(db,day,[{'id':row['id'],'position':positions[index]} for index,row in enumerate(ordered)])
    for position, row in enumerate(ordered):
        db.execute(
            """UPDATE daily_queue SET position=?,card_bucket=?,admission_kind=? WHERE id=?""",
            (
                positions[position] if frozen_active else position,
                row["content_type"] or "opening",
                row["admission_kind"],
                row["id"],
            ),
        )
    db.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (day, seed, membership_hash, datetime.now(timezone.utc).isoformat()),
    )


def _quarantine_malformed_opening_cards(database, queue_date: str) -> list[dict]:
    candidates = database.execute(
        """SELECT q.id queue_entry_id,c.id,c.start_fen,c.moves_json,c.content_type,
                          COALESCE(c.trained_color,(SELECT l.trained_color FROM repertoire_lines l
                           WHERE l.repertoire_id=c.repertoire_id ORDER BY l.created_at LIMIT 1)) trained_color
                   FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
                     AND (c.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires r_ok
                                WHERE (r_ok.id=c.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc_ok WHERE rc_ok.card_id=c.id AND rc_ok.repertoire_id=r_ok.id))
                                  AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                                 WHERE block.repertoire_id=r_ok.id AND block.card_id=c.id)))""",
        (queue_date,),
    ).fetchall()
    diagnostics: list[dict] = []
    for candidate in candidates:
        if candidate["content_type"] != "opening" or candidate["trained_color"] not in {
            "white",
            "black",
        }:
            continue
        try:
            moves = json.loads(candidate["moves_json"])
        except json.JSONDecodeError:
            moves = []
        if ends_on_trained_move(
            candidate["start_fen"], moves, candidate["trained_color"]
        ):
            continue
        database.execute("UPDATE cards SET state='locked' WHERE id=?", (candidate["id"],))
        database.execute(
            "UPDATE daily_queue SET status='skipped' WHERE id=?",
            (candidate["queue_entry_id"],),
        )
        diagnostics.append(
            {
                "card_id": candidate["id"],
                "message": "Skipped an incomplete opening card. Edit or re-import its line to study it.",
            }
        )
    return diagnostics


def materialize_daily_queue(database, queue_date: str, *, preserve_through_entry_id: int | None = None) -> None:
    """Publish one complete queue generation in a bounded writer transaction."""

    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1)
           ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',refresh_pending=1,
               last_error=NULL""",
        (queue_date,),
    )
    seed_queue(database, queue_date)
    randomize_daily_queue(database, queue_date, preserve_through_entry_id=preserve_through_entry_id)
    diagnostics = _quarantine_malformed_opening_cards(database, queue_date)
    database.execute(
        "DELETE FROM queue_projection_diagnostics WHERE queue_date=?", (queue_date,)
    )
    database.executemany(
        "INSERT INTO queue_projection_diagnostics(queue_date,card_id,message) VALUES(?,?,?)",
        [
            (queue_date, diagnostic["card_id"], diagnostic["message"])
            for diagnostic in diagnostics
        ],
    )
    now = datetime.now(timezone.utc).isoformat()
    blocked_count = database.execute(
        """SELECT COUNT(*) FROM (
               SELECT q.card_id FROM daily_queue q
               WHERE q.queue_date=? AND q.status='blocked'
               UNION
               SELECT c.id FROM cards c
               WHERE c.content_type='opening' AND c.archived=0
                 AND c.state IN ('learning','mature') AND c.due_date<=?
                 AND EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.card_id=c.id
                       AND (block.repertoire_id=c.repertoire_id OR EXISTS(
                           SELECT 1 FROM repertoire_cards rc
                           WHERE rc.card_id=c.id
                             AND rc.repertoire_id=block.repertoire_id
                       ))
                 )
           )""",
        (queue_date, queue_date),
    ).fetchone()[0]
    database.execute(
        """UPDATE queue_projections SET state='ready',generation=generation+1,
               updated_at=?,refresh_pending=0,last_error=NULL,blocked_count=?
           WHERE queue_date=?""",
        (now, blocked_count, queue_date),
    )


def requeue(db, day, cid, after, attempt):
    cycle = db.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM ("
        "SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL "
        "SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
        (day, cid, day, cid),
    ).fetchone()[0]
    if after is None:
        position = db.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
            (day,),
        ).fetchone()[0]
    else:
        row = db.execute(
            _AVAILABLE_ATTEMPT_SQL.replace("SELECT q.id FROM", "SELECT q.position FROM", 1)
            + " ORDER BY q.position,q.id LIMIT 1 OFFSET ?",
            (day, max(0, after)),
        ).fetchone()
        position = (
            row[0]
            if row
            else db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (day,),
            ).fetchone()[0]
        )
        db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",
            (day, position),
        )
    inserted_entry_id = db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) "
        "VALUES(?,?,?,?,?) RETURNING id",
        (day, cid, cycle, position, attempt),
    ).fetchone()[0]
    db.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind='review'
           WHERE id=?""",
        (inserted_entry_id,),
    )
    preserve_daily_queue_order(db, day)
    return inserted_entry_id


_QUEUE_REFRESH_PROJECTION_SQL = """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL"""


_AVAILABLE_ATTEMPT_SQL = """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
            WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
              AND c.pending_validation=0 AND c.superseded_by IS NULL
              AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)
              AND (c.content_type!='opening' OR EXISTS(
                  SELECT 1 FROM repertoires repertoire
                  WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                      SELECT 1 FROM repertoire_cards link
                      WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                    AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                        WHERE blocked.card_id=c.id AND blocked.repertoire_id=repertoire.id)))
              AND (c.content_type!='defense' OR
                   (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
              AND (c.content_type!='study_exercise' OR EXISTS(
                  SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id
                  WHERE exercise.id=c.study_exercise_id AND exercise.status='published' AND study.archived=0
                    AND (q.admission_kind='explicit' OR NOT EXISTS(
                        SELECT 1 FROM study_sibling_burials burial
                        WHERE burial.exercise_id=exercise.id AND burial.study_day=q.queue_date))))"""
_AVAILABLE_ATTEMPT_SQL += frozen_opening_order.stale_queue_predicate()


def request_queue_refresh_in_transaction(database: PostgresConnection,
                                         queue_date: str, *, preserve_through_entry_id: int | None = None) -> dict[str, Any]:
    """Checkpoint a foreground mutation and its follow-up queue refresh together."""

    payload = {"queue_date": queue_date}
    if preserve_through_entry_id is not None:
        payload["preserve_through_entry_id"] = preserve_through_entry_id
    task = enqueue_task_in_transaction(
        database, "daily_queue", "current", payload, priority=10,
    )
    database.execute(
        _QUEUE_REFRESH_PROJECTION_SQL,
        (queue_date,),
    )
    return task

