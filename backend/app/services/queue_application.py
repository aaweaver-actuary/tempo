"""Queue eligibility and projection effects on the caller-owned connection.

Transaction, lock, issuance and durable-slice ownership stay with existing callers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .durable_tasks import enqueue_task_in_transaction
from . import frozen_opening_order
from tempo_legacy_database.adapters.opening_progression import PRACTICED_OPENING_PARENT_SQL
from .pgn import ends_on_trained_move
from .queue_ordering import _queue_membership_hash, _plan_daily_queue_order
from .real_game_feedback import MISS_REASON
from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query, outstanding_miss_sql
from .real_game_feedback_adapter import has_outstanding_real_game_miss, promote_real_game_card, clear_satisfied_miss_priority, prioritize_queued_misses
from .review_service import preserve_daily_queue_order
from .tactical_catalog import seed_tactical_introductions


# The compatibility surface remains here; introduction decisions and persistence
# now have explicit, separately testable owners.
from .queue_admission import AdmittedOpeningProposal, OpeningCapacityRoutePayload
from .queue_admission_adapter import QueueAdmissionAdapter, _PRIORITY_OPENING_CANDIDATE_BODY
from . import queue_admission


def reconcile_unseen_queue(db, day, limit, repertoire_limits=None):
    return queue_admission.reconcile_unseen_queue(QueueAdmissionAdapter(db), day, limit, repertoire_limits)


from tempo_legacy_database.adapters.queue_refresh.reads import _ACTIVE_OPENING_MISS_SQL


def admit_prioritized_opening_cards(db, day: str, limit: int | dict[str, int], maximum: int) -> int:
    return queue_admission.admit_prioritized_opening_cards(QueueAdmissionAdapter(db), day, limit, maximum)


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


def _unlock_postgres_opening_page(db, *, after_card_id: str, batch_size: int) -> str | None:
    """Examine a finite indexed card page; preserve at most batch_size writes.

    The former parent-first lateral query revisited all practiced parents for
    every eight unlocks. A page can now make durable progress even when none of
    its cards has an eligible current route. SQLite retains its sparse selector.
    """
    read_limit = max(batch_size, 16)
    raw_candidates = db.execute_native(
        "SELECT id FROM cards WHERE content_type='opening' AND state='locked' "
        "AND archived=0 AND id>%s ORDER BY id LIMIT %s",
        (after_card_id, read_limit + 1),
    ).fetchall()
    candidate_ids = [row[0] for row in raw_candidates[:read_limit]]
    if not candidate_ids:
        return None
    eligibility_sql = _OPENING_UNLOCK_ELIGIBILITY_SQL.replace(
        'WHERE step.card_id=cards.id', "WHERE publication.state='ready' AND step.card_id=cards.id")
    eligible = db.execute_native(
        "WITH bounded_cards AS MATERIALIZED (SELECT * FROM cards WHERE id=ANY(%s)) "
        "SELECT id FROM bounded_cards cards WHERE " + eligibility_sql +
        " ORDER BY id LIMIT %s", (candidate_ids, batch_size),
    ).fetchall()
    selected_ids = [row[0] for row in eligible]
    if selected_ids:
        db.execute("UPDATE cards SET state='new' WHERE " + eligibility_sql +
                   " AND cards.id IN (" + ",".join("?" for _ in selected_ids) + ")", selected_ids)
    cursor = selected_ids[-1] if len(selected_ids) == batch_size else candidate_ids[-1]
    return cursor if cursor < candidate_ids[-1] or len(raw_candidates) > read_limit else None


def _unlock_eligible_opening_cards(
    db, day, *, after_card_id: str | None = None, batch_size: int | None = None,
) -> str | None:
    selected_card_ids: list[str] = []
    has_more_candidates = False
    if batch_size is not None:
        if batch_size < 1:
            raise ValueError("Queue unlock batch size must be positive")
        if hasattr(db, 'execute_native'):
            return _unlock_postgres_opening_page(db, after_card_id=after_card_id or "", batch_size=batch_size)
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





from tempo_legacy_database.adapters.queue_refresh import presentation as presentation_storage
from tempo_legacy_database.adapters.queue_refresh.presentation import _QUEUE_RANDOMIZATION_ROWS_SQL

def presentation_rows(database, day, *, read=None, after_entry_id=None, limit=None):
    """Capture eligibility and attempt protection without changing membership."""
    rows, reviewed, eligible = presentation_storage.presentation_facts(
        database, day, MISS_REASON, read=read, after_entry_id=after_entry_id, limit=limit,
    )
    if not rows:
        return rows
    for row in rows:
        from .legacy_game_evidence_scope import BRIDGE_MISS_REASON
        if presentation_storage.is_postgres_connection(database) and row['gameplay_priority_reason'] == BRIDGE_MISS_REASON:
            from .legacy_study_remediation import has_current_miss
            if not has_current_miss(database, row['card_id']): row['gameplay_priority_reason'] = None
        row['admission_kind'] = ('explicit' if row['admission_kind']=='explicit'
                                 else 'review' if row.get('cycle',0)>0 or row['card_id'] in reviewed else 'new')
        row['eligible'] = row['id'] in eligible
    return rows


def randomize_daily_queue(db, day: str, *, preserve_through_entry_id: int | None = None) -> None:
    """Create a stable mixed queue whenever that day's membership changes."""
    rows = presentation_rows(db, day)
    if not rows:
        return
    saved = db.execute(
        "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)
    ).fetchone()
    token = frozen_opening_order.activation(db)
    membership_hash = frozen_opening_order.queue_hash(_queue_membership_hash(rows), token)
    if saved and saved["membership_hash"] == membership_hash:
        prioritize_queued_misses(db, day)
        return
    if token[0] is not None:
        rows = frozen_opening_order.annotate(db, rows)
    seed, _, ordered = _plan_daily_queue_order(
        rows, day, saved, preserve_through_entry_id=preserve_through_entry_id,
    )
    frozen_active = token[0] is not None
    positions = sorted(row["position"] for row in rows)
    if frozen_active:
        frozen_opening_order.record_queue_changes(db,day,[{'id':row['id'],'position':positions[index]} for index,row in enumerate(ordered)])
    for position, row in enumerate(ordered):
        db.execute(
            """UPDATE daily_queue SET position=?,card_bucket=?,admission_kind=? WHERE id=?""",
            (
                positions[position],
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
    from .review_service import capture_daily_queue_order
    previous_order = capture_daily_queue_order(db, day)
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
            available_attempt_sql().replace("SELECT q.id FROM", "SELECT q.position FROM", 1)
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
    preserve_daily_queue_order(db, day, previous=previous_order)
    return inserted_entry_id


_QUEUE_REFRESH_PROJECTION_SQL = """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL"""


from tempo_legacy_database.adapters.http_composition import availability as queue_availability_storage

_AVAILABLE_ATTEMPT_SQL = queue_availability_storage.base_available_attempt_sql(MISS_REASON)


def available_attempt_sql() -> str:
    return queue_availability_storage.available_attempt_sql(MISS_REASON)


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

