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


from tempo_legacy_database.adapters.queue_refresh.application import eligibility as eligibility_storage
from tempo_legacy_database.adapters.queue_refresh.application import seeding as seeding_storage
from tempo_legacy_database.adapters.queue_refresh.application.eligibility import (
    _OPENING_UNLOCK_ELIGIBILITY_SQL,
    _OPENING_UNLOCK_CANDIDATES_SQL,
    _OPENING_UNLOCK_POSTGRES_CANDIDATES_SQL,
    _block_ineligible_opening_queue_entries,
    _block_unapproved_defense_queue_entries,
    _restore_eligible_due_queue_entries,
    _restore_published_study_queue_entries,
    _reset_unintroduced_opening_cards,
    _reset_stale_opening_introductions,
)


def _unlock_postgres_opening_page(db, *, after_card_id: str, batch_size: int) -> str | None:
    """Examine a finite indexed card page; preserve at most batch_size writes.

    The former parent-first lateral query revisited all practiced parents for
    every eight unlocks. A page can now make durable progress even when none of
    its cards has an eligible current route. SQLite retains its sparse selector.
    """
    read_limit = max(batch_size, 16)
    raw_candidates = eligibility_storage.locked_opening_page(db, after_card_id, read_limit)
    candidate_ids = [row[0] for row in raw_candidates[:read_limit]]
    if not candidate_ids:
        return None
    eligible, unlock_selected = eligibility_storage.ready_unlock_candidates(db, candidate_ids, batch_size)
    selected_ids = [row[0] for row in eligible]
    if selected_ids:
        unlock_selected(selected_ids)
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
        if eligibility_storage.uses_native_execution(db):
            return _unlock_postgres_opening_page(db, after_card_id=after_card_id or "", batch_size=batch_size)
        candidates = eligibility_storage.sparse_unlock_candidates(db, after_card_id, batch_size)
        selected_card_ids = [row[0] for row in candidates[:batch_size]]
        has_more_candidates = len(candidates) > batch_size
        if not selected_card_ids:
            return None
    # Recheck the same published eligibility when applying the bounded update.
    eligibility_storage.unlock_eligible_cards(db, selected_card_ids)
    return selected_card_ids[-1] if has_more_candidates else None


_QUEUE_ELIGIBILITY_PHASES = (
    ("unlock_opening", _unlock_eligible_opening_cards),
    ("block_opening", _block_ineligible_opening_queue_entries),
    ("block_defense", _block_unapproved_defense_queue_entries),
    ("restore_due", _restore_eligible_due_queue_entries),
    ("restore_study", _restore_published_study_queue_entries),
)


def seed_queue(db, day):
    for _, apply_phase in _QUEUE_ELIGIBILITY_PHASES:
        apply_phase(db, day)
    seed_tactical_introductions(db, day)
    _reset_unintroduced_opening_cards(db, day)
    _reset_stale_opening_introductions(db, day)
    limit = seeding_storage.opening_allowance(db)
    from ..repertoire_settings import effective_opening_limits
    repertoire_limits = effective_opening_limits(db)
    reconcile_unseen_queue(db, day, limit, repertoire_limits)
    maximum = seeding_storage.maximum_position(db, day)
    rows = seeding_storage.due_cards(db, day)
    for offset, row in enumerate(rows, 1):
        seeding_storage.append_due_card(db, day, row[0], maximum + offset)
    maximum += len(rows)
    admit_prioritized_opening_cards(db, day, repertoire_limits, maximum)
    outstanding_studied_cards = seeding_storage.outstanding_studied_cards(db)
    for missed_card in outstanding_studied_cards:
        promote_real_game_card(db, missed_card["card_id"], day)
    for stale_priority in seeding_storage.stale_miss_priority(db, day, MISS_REASON):
        clear_satisfied_miss_priority(db, stale_priority["card_id"])
    study_allowance = seeding_storage.study_allowance(db)
    admitted_studies = seeding_storage.admitted_study_count(db, day)
    remaining_studies = max(0, study_allowance - admitted_studies)
    if remaining_studies:
        next_position = seeding_storage.next_position(db, day)
        study_rows = seeding_storage.new_study_cards(db, day, remaining_studies)
        for offset, study_row in enumerate(study_rows):
            seeding_storage.append_study_card(db, day, study_row["id"], next_position + offset)


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


from tempo_legacy_database.adapters.queue_refresh.foreground import _QUEUE_REFRESH_PROJECTION_SQL, mark_queue_refreshing


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
    mark_queue_refreshing(database, queue_date)
    return task

