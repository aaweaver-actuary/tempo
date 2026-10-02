"""Bounded PostgreSQL queue-refresh slices with durable phase and cursor state."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any

from psycopg.errors import TransactionTimeout

from ..database import background_read_connection, connection
from .. import postgres_store
from ..queue_position_lock import lock_queue_date_for_position
from .cards import card_id
from .activity_gate import activity_gate
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    lock_current_slice,
)
from .pgn import ends_on_trained_move
from .puzzles import validate_puzzle_record
from .tactical_catalog import pack_records
from .tactic_admission import DAILY_TACTIC_COUNT_SQL, count_daily_tactic_introductions, lock_daily_tactic_admission


_ELIGIBILITY_PHASES = (
    "unlock_opening",
    "block_opening",
    "block_defense",
    "restore_due",
    "restore_study",
)
_UNLOCK_BATCH_SIZE = 8
_QUARANTINE_READ_BATCH_SIZE = 32
_OPENING_CANDIDATE_READ_BATCH_SIZE = 16

_PRIORITY_OPENING_PAGE_SQL = """WITH active_miss AS MATERIALIZED (
    SELECT unnest(%s::text[]) AS card_id
), eligible_cards AS MATERIALIZED (
    SELECT * FROM cards WHERE id=ANY(%s::text[])
)
SELECT DISTINCT c.id,linked.id AS repertoire_id,c.moves_json,c.due_date,
       CASE WHEN c.state='locked' AND opportunity.id IS NOT NULL THEN
         'Priority introduction · reached ' ||
         (opportunity.evidence_json::jsonb ->> 'encounter_count') ||
         ' times in games, missed ' ||
         (opportunity.evidence_json::jsonb ->> 'miss_count') || ' times'
         WHEN active_miss.card_id IS NOT NULL THEN %s ELSE priority.reason END
         AS gameplay_priority_reason,
       CASE WHEN active_miss.card_id IS NOT NULL THEN %s ELSE priority.priority_date END
         AS priority_date,
       COALESCE(published.priority_score,legacy.priority_score) AS priority_score,
       COALESCE(published.completed_line_ids_json,legacy.completed_line_ids_json)
         AS completed_line_ids_json,
       COALESCE(published.frontier_decisions_json,legacy.frontier_decisions_json)
         AS frontier_decisions_json
FROM eligible_cards c
JOIN LATERAL (
    SELECT c.repertoire_id AS repertoire_id
    UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=c.id
) linked_ids ON TRUE
JOIN repertoires linked ON linked.id=linked_ids.repertoire_id
LEFT JOIN current_gameplay_card_priorities priority ON priority.card_id=c.id
    AND priority.priority_date<=%s
LEFT JOIN active_miss ON active_miss.card_id=c.id
LEFT JOIN current_repertoire_opportunities opportunity ON opportunity.repertoire_id=linked.id
    AND opportunity.card_id=c.id AND opportunity.kind='weak_known_decision'
    AND opportunity.status='active'
    AND (opportunity.evidence_json::jsonb ->> 'analysis_based') IS NULL
LEFT JOIN repertoire_priority_publications publication ON publication.repertoire_id=linked.id
LEFT JOIN current_repertoire_card_priority_generations published ON published.card_id=c.id
    AND published.repertoire_id=linked.id AND published.generation=publication.generation
LEFT JOIN current_repertoire_card_introduction_priorities legacy ON legacy.card_id=c.id
    AND legacy.repertoire_id=linked.id
WHERE c.content_type='opening'
  AND (c.due_date<=%s OR priority.card_id IS NOT NULL OR active_miss.card_id IS NOT NULL
       OR opportunity.id IS NOT NULL)
  AND (c.state='new' OR (c.state='locked' AND opportunity.id IS NOT NULL))
  AND c.introduced_at IS NULL AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
  AND EXISTS(
      SELECT 1 FROM (
          SELECT c.repertoire_id AS repertoire_id
          UNION SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=c.id
      ) eligible_link JOIN repertoires allowed ON allowed.id=eligible_link.repertoire_id
      WHERE NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                       WHERE block.repertoire_id=allowed.id AND block.card_id=c.id)
  )
  AND c.id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=%s)"""


def _bounded_read(statement: str, parameters: tuple = (), *, native: bool = False) -> list:
    """Retry a read-only section if PostgreSQL closes its 50 ms transaction."""

    for attempt in range(3):
        try:
            with background_read_connection() as database:
                execute = database.execute_native if native else database.execute
                return execute(statement, parameters).fetchall()
        except TransactionTimeout:
            if attempt == 2:
                raise
    raise AssertionError("Unreachable background read retry state")


def _prepare_tactical_introduction(queue_date: str) -> dict[str, Any] | None:
    """Choose one puzzle after closing the bounded PostgreSQL read section."""

    limit = _bounded_read("SELECT tactics_new_per_day FROM settings WHERE id=1")[0][0]
    reserved = _bounded_read(
        DAILY_TACTIC_COUNT_SQL, (queue_date,),
    )[0][0]
    if reserved >= limit:
        return None
    active_packs = sorted(row[0] for row in _bounded_read(
        "SELECT pack_id FROM tactic_pack_activation WHERE active=1",
    ))
    if not active_packs:
        return None
    rotation_cursor = _bounded_read(
        "SELECT last_pack_id FROM tactic_rotation WHERE id=1",
    )[0][0]
    seen_puzzles = {row[0] for row in _bounded_read(
        "SELECT puzzle_id FROM tactic_progress WHERE admitted_at IS NOT NULL",
    )}
    ordered_packs = ([pack for pack in active_packs if pack > rotation_cursor]
                     + [pack for pack in active_packs if pack <= rotation_cursor])
    for pack_id in ordered_packs:
        for record in pack_records(pack_id):
            if record["PuzzleId"] in seen_puzzles:
                continue
            training_fen, solution = validate_puzzle_record(record)
            return {
                "pack_id": pack_id,
                "puzzle_id": record["PuzzleId"],
                "card_id": card_id(training_fen, solution),
                "training_fen": training_fen,
                "solution_json": json.dumps(solution),
                "source_fen": record["FEN"],
                "rotation_cursor": rotation_cursor,
            }
    return None


def _publish_tactical_introduction(database, queue_date: str,
                                   prepared: dict[str, Any] | None) -> bool:
    """Commit one reservation, or report that the phase can advance."""

    if prepared is None:
        return False
    lock_daily_tactic_admission(database, queue_date)
    limit = database.execute_native("SELECT tactics_new_per_day FROM settings WHERE id=1").fetchone()[0]
    reserved = count_daily_tactic_introductions(database, queue_date)
    if reserved >= limit:
        return False
    cursor = database.execute_native(
        "SELECT last_pack_id FROM tactic_rotation WHERE id=1 FOR UPDATE",
    ).fetchone()[0]
    active = database.execute_native(
        "SELECT active FROM tactic_pack_activation WHERE pack_id=%s",
        (prepared["pack_id"],),
    ).fetchone()
    if cursor != prepared["rotation_cursor"] or active is None or not active[0]:
        return True
    progress = database.execute_native(
        "SELECT admitted_at FROM tactic_progress WHERE puzzle_id=%s FOR UPDATE",
        (prepared["puzzle_id"],),
    ).fetchone()
    if progress is not None and progress[0] is not None:
        return True
    database.execute_native(
        "INSERT INTO repertoires(id,name,source_name,created_at) "
        "VALUES('__tactics__','Tactics','Lichess puzzle database',%s) "
        "ON CONFLICT(id) DO NOTHING",
        (queue_date,),
    )
    database.execute_native(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,"
        "content_type,scheduling_mode,source_ref,source_fen,introduced_at) "
        "VALUES(%s,'__tactics__','checkpoint',%s,%s,'learning',%s,'tactic','light',%s,%s,%s) "
        "ON CONFLICT(id) DO NOTHING",
        (prepared["card_id"], prepared["training_fen"], prepared["solution_json"],
         queue_date, prepared["puzzle_id"], prepared["source_fen"], queue_date),
    )
    database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (prepared["card_id"],))
    database.execute_native(
        "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at,admission_mode) "
        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(puzzle_id) DO UPDATE SET "
        "card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at)",
        (prepared["puzzle_id"], prepared["pack_id"], prepared["card_id"], queue_date, "light"),
    )
    database.execute_native(
        "INSERT INTO tactic_introductions(puzzle_id,pack_id,introduction_date) VALUES(%s,%s,%s)",
        (prepared["puzzle_id"], prepared["pack_id"], queue_date),
    )
    queued = database.execute_native(
        "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s",
        (queue_date, prepared["card_id"]),
    ).fetchone()
    if queued is None:
        lock_queue_date_for_position(database, queue_date)
        queued_after_lock = database.execute_native(
            "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s",
            (queue_date, prepared["card_id"]),
        ).fetchone()
        if queued_after_lock is None:
            next_position = database.execute_native(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s",
                (queue_date,),
            ).fetchone()[0]
            database.execute_native(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)",
                (queue_date, prepared["card_id"], next_position),
            )
    database.execute_native(
        "UPDATE tactic_rotation SET last_pack_id=%s WHERE id=1", (prepared["pack_id"],),
    )
    return True


def _prepare_unseen_reconciliation(queue_date: str, processed_ids: list[int],
                                   introduced_counts: dict[str, int]) -> tuple[dict | None, int]:
    """Read one unprocessed queue entry and its repertoire's starting count."""

    rows = _bounded_read(
        """SELECT q.id,q.card_id,COALESCE(q.admission_repertoire_id,c.repertoire_id) repertoire_id
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.status='queued' AND c.content_type='opening'
             AND COALESCE(q.admission_kind,'')!='explicit'
             AND (c.introduced_at IS NULL OR c.introduced_at=%s)
             AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
             AND q.id <> ALL(%s::bigint[])
           ORDER BY q.position,q.id LIMIT 1""",
        (queue_date, queue_date, processed_ids), native=True,
    )
    if not rows:
        return None, 0
    candidate = dict(rows[0])
    repertoire_id = candidate["repertoire_id"]
    if repertoire_id in introduced_counts:
        return candidate, introduced_counts[repertoire_id]
    # Eligibility changes must not refund a reviewed historical introduction.
    reviewed_count = _bounded_read(
        """SELECT COUNT(DISTINCT c.id) FROM cards c
           LEFT JOIN daily_queue q ON q.card_id=c.id AND q.queue_date=c.introduced_at AND q.cycle=0
           WHERE COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s AND c.content_type='opening' AND c.introduced_at=%s
             AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)""",
        (repertoire_id, queue_date), native=True,
    )[0][0]
    return candidate, int(reviewed_count)


def _reconcile_one_unseen_entry(database, queue_date: str, candidate: dict,
                                introduced_count: int) -> bool:
    """Apply the original per-repertoire admission rule to one locked entry."""

    current = database.execute_native(
        """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.id=%s AND q.queue_date=%s AND q.status='queued'
             AND c.content_type='opening' AND COALESCE(q.admission_kind,'')!='explicit'
             AND (c.introduced_at IS NULL OR c.introduced_at=%s)
             AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
           FOR UPDATE OF q,c""",
        (candidate["id"], queue_date, queue_date),
    ).fetchone()
    if current is None:
        return False
    limit = _current_opening_limit(database, candidate["repertoire_id"])
    if limit is None:
        return False
    if introduced_count < limit:
        database.execute_native(
            "UPDATE cards SET introduced_at=%s,state='learning' WHERE id=%s",
            (queue_date, candidate["card_id"]),
        )
        return True
    database.execute_native("DELETE FROM daily_queue WHERE id=%s", (candidate["id"],))
    database.execute_native(
        "UPDATE cards SET introduced_at=NULL,state='new' WHERE id=%s",
        (candidate["card_id"],),
    )
    return False


_DUE_CARD_ELIGIBILITY = """c.due_date<=%s AND c.state IN ('learning','mature')
    AND c.archived=0 AND COALESCE(c.pending_validation,0)=0
    AND (c.content_type!='opening' OR EXISTS(
        SELECT 1 FROM repertoires repertoire
        WHERE (repertoire.id=c.repertoire_id OR EXISTS(
            SELECT 1 FROM repertoire_cards link
            WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                         WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id)))
    AND (c.content_type!='study_exercise' OR (
        EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study
               ON study.id=exercise.study_id
               WHERE exercise.id=c.study_exercise_id AND exercise.status='published'
                 AND study.archived=0)
        AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                       WHERE burial.exercise_id=c.study_exercise_id AND burial.study_day=%s)))
    AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                   WHERE queue.card_id=c.id AND queue.queue_date=%s)"""


def _prepare_due_card(queue_date: str) -> str | None:
    rows = _bounded_read(
        "SELECT c.id FROM cards c WHERE " + _DUE_CARD_ELIGIBILITY +
        " ORDER BY c.due_date,c.id LIMIT 1",
        (queue_date, queue_date, queue_date), native=True,
    )
    return str(rows[0][0]) if rows else None


def _admit_one_due_card(database, queue_date: str, card_id: str) -> bool:
    if database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,),
    ).fetchone() is None:
        return False
    lock_queue_date_for_position(database, queue_date)
    eligible = database.execute_native(
        "SELECT 1 FROM cards c WHERE c.id=%s AND " + _DUE_CARD_ELIGIBILITY,
        (card_id, queue_date, queue_date, queue_date),
    ).fetchone()
    if eligible is None:
        return False
    next_position = database.execute_native(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s",
        (queue_date,),
    ).fetchone()[0]
    database.execute_native(
        "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)",
        (queue_date, card_id, next_position),
    )
    return True


def _prepare_prioritized_openings(queue_date: str) -> list[dict[str, Any]]:
    """Plan openings using separate bounded reads and no open write transaction."""

    from .. import main

    active_misses = [row[0] for row in _bounded_read(main._ACTIVE_OPENING_MISS_SQL)]
    candidates = []
    after_card_id = ""
    while True:
        card_ids = [row[0] for row in _bounded_read(
            "SELECT id FROM cards WHERE content_type='opening' "
            "AND state IN ('new','locked') AND introduced_at IS NULL "
            "AND archived=0 AND COALESCE(pending_validation,0)=0 "
            "AND id>%s ORDER BY id LIMIT %s",
            (after_card_id, _OPENING_CANDIDATE_READ_BATCH_SIZE), native=True,
        )]
        if not card_ids:
            break
        candidates.extend(_bounded_read(
            _PRIORITY_OPENING_PAGE_SQL,
            (active_misses, card_ids, main.MISS_REASON, queue_date,
             queue_date, queue_date, queue_date), native=True,
        ))
        after_card_id = card_ids[-1]
    # Count consumption independently from current candidate eligibility.
    counts = _bounded_read(
        """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s
           GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
        (queue_date, queue_date), native=True,
    )
    daily_limit = dict(_bounded_read(
        "SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) "
        "FROM repertoires r CROSS JOIN settings s WHERE s.id=1",
    ))
    plan = main._plan_prioritized_opening_admissions(
        candidates, dict(counts), queue_date, daily_limit,
    )
    return [{"repertoire_id": repertoire_id, "card_id": candidate["id"],
             "reason": candidate["gameplay_priority_reason"]}
            for repertoire_id, candidate in plan]


def _current_opening_limit(database, repertoire_id: str) -> int | None:
    # The slice holds the task lease row. Settings changes enqueue a fresh
    # generation in their write transaction, invalidating any old checkpoint.
    # Do not lock settings rows here: foreground writes take them before the
    # task row, and reversing that order would deadlock the two workers.
    row = database.execute_native(
        "SELECT COALESCE(r.new_cards_per_day,s.new_cards_per_day) "
        "FROM repertoires r CROSS JOIN settings s WHERE r.id=%s AND s.id=1",
        (repertoire_id,),
    ).fetchone()
    return int(row[0]) if row else None


def _admit_one_prioritized_opening(database, queue_date: str, planned: dict) -> None:
    """Publish one still eligible card with the task checkpoint in one transaction."""

    card_id = planned["card_id"]
    card = database.execute_native(
        "SELECT state,introduced_at,archived,pending_validation FROM cards WHERE id=%s FOR UPDATE",
        (card_id,),
    ).fetchone()
    if (card is None or card[1] is not None or card[2] or card[3]
            or card[0] not in ("new", "locked")):
        return
    queued = database.execute_native(
        "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s",
        (queue_date, card_id),
    ).fetchone()
    if queued is not None:
        return
    lock_queue_date_for_position(database, queue_date)
    if database.execute_native(
        "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s",
        (queue_date, card_id),
    ).fetchone():
        return
    # Recheck inside publication: a foreground settings write may have changed
    # the limit after planning, and another slice may have used the allowance.
    daily_limit = _current_opening_limit(database, planned["repertoire_id"])
    admitted_count = database.execute_native(
        """SELECT COUNT(DISTINCT c.id) FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.cycle=0 AND c.content_type='opening' AND c.introduced_at=%s
             AND COALESCE(q.admission_repertoire_id,c.repertoire_id)=%s""",
        (queue_date, queue_date, planned["repertoire_id"]),
    ).fetchone()[0]
    if daily_limit is None or admitted_count >= daily_limit:
        return
    position = database.execute_native(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s",
        (queue_date,),
    ).fetchone()[0]
    database.execute_native(
        """INSERT INTO daily_queue(queue_date,card_id,position,gameplay_priority_reason,
                                   admission_repertoire_id) VALUES(%s,%s,%s,%s,%s)""",
        (queue_date, card_id, position, planned["reason"], planned["repertoire_id"]),
    )
    database.execute_native(
        "UPDATE cards SET introduced_at=%s,state='learning' WHERE id=%s",
        (queue_date, card_id),
    )
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
           WHERE repertoire_id=? AND card_id=? AND kind='weak_known_decision'
             AND json_extract(evidence_json,'$.analysis_based') IS NULL AND status='active'""",
        (now, now, planned["repertoire_id"], card_id),
    )


_STUDY_NEW_ELIGIBILITY = """c.content_type='study_exercise' AND c.state='new'
    AND c.archived=0 AND COALESCE(c.pending_validation,0)=0 AND c.due_date<=%s
    AND exercise.status='published' AND study.archived=0
    AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                   WHERE burial.exercise_id=exercise.id AND burial.study_day=%s)
    AND NOT EXISTS(SELECT 1 FROM daily_queue queue
                   WHERE queue.card_id=c.id AND queue.queue_date=%s)"""


def _prepare_study_admission(queue_date: str) -> str | None:
    """Read the allowance and oldest eligible exercise in bounded sections."""

    allowance = int(_bounded_read("SELECT study_new_per_day FROM settings WHERE id=1")[0][0])
    admitted = int(_bounded_read(
        """SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
           WHERE queue.queue_date=%s AND card.content_type='study_exercise'
             AND card.state='new' AND queue.status IN ('queued','complete','buried')""",
        (queue_date,), native=True,
    )[0][0])
    if admitted >= allowance:
        return None
    rows = _bounded_read(
        "SELECT c.id FROM cards c JOIN study_exercises exercise ON exercise.id=c.study_exercise_id "
        "JOIN studies study ON study.id=exercise.study_id WHERE " + _STUDY_NEW_ELIGIBILITY +
        " ORDER BY exercise.created_at,exercise.id LIMIT 1",
        (queue_date, queue_date, queue_date), native=True,
    )
    return str(rows[0][0]) if rows else None


def _admit_one_study_card(database, queue_date: str, study_card_id: str) -> bool:
    """Recheck the quota and candidate while the task and card are locked."""

    if database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (study_card_id,),
    ).fetchone() is None:
        return False
    lock_queue_date_for_position(database, queue_date)
    allowance = database.execute_native(
        "SELECT study_new_per_day FROM settings WHERE id=1",
    ).fetchone()[0]
    admitted = database.execute_native(
        """SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
           WHERE queue.queue_date=%s AND card.content_type='study_exercise'
             AND card.state='new' AND queue.status IN ('queued','complete','buried')""",
        (queue_date,),
    ).fetchone()[0]
    if admitted >= allowance:
        return False
    eligible = database.execute_native(
        "SELECT 1 FROM cards c JOIN study_exercises exercise ON exercise.id=c.study_exercise_id "
        "JOIN studies study ON study.id=exercise.study_id WHERE c.id=%s AND "
        + _STUDY_NEW_ELIGIBILITY,
        (study_card_id, queue_date, queue_date, queue_date),
    ).fetchone()
    if eligible is None:
        return False
    position = database.execute_native(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s",
        (queue_date,),
    ).fetchone()[0]
    database.execute_native(
        """INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind)
           VALUES(%s,%s,%s,'study_exercise','new')""",
        (queue_date, study_card_id, position),
    )
    return True


def _prepare_queue_randomization(queue_date: str) -> dict[str, Any]:
    """Plan a stable queue order without retaining a database transaction."""

    from .. import main

    queue_rows = _bounded_read(
        """SELECT q.id,q.card_id,c.content_type,q.admission_kind,q.gameplay_priority_reason
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.id""",
        (queue_date,), native=True,
    )
    if not queue_rows:
        return {"membership_hash": main._queue_membership_hash([]), "entries": None}
    reviewed_card_ids = {row[0] for row in _bounded_read(
        """SELECT DISTINCT card_id FROM reviews
           WHERE card_id=ANY(%s::text[]) AND invalidated_at IS NULL""",
        ([row["card_id"] for row in queue_rows],), native=True,
    )}
    rows = [
        {**dict(row), "admission_kind": (
            "explicit" if row["admission_kind"] == "explicit"
            else "review" if row["card_id"] in reviewed_card_ids else "new"
        )}
        for row in queue_rows
    ]
    saved_rows = _bounded_read(
        "SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?",
        (queue_date,),
    )
    saved = saved_rows[0] if saved_rows else None
    membership_hash = main._queue_membership_hash(rows)
    if saved and saved["membership_hash"] == membership_hash:
        return {"membership_hash": membership_hash, "entries": None}
    seed, membership_hash, ordered = main._plan_daily_queue_order(rows, queue_date, saved)
    return {
        "seed": seed,
        "membership_hash": membership_hash,
        "entries": [
            {"id": row["id"], "position": position,
             "bucket": row["content_type"] or "opening", "kind": row["admission_kind"]}
            for position, row in enumerate(ordered)
        ],
    }


def _publish_queue_randomization(database, queue_date: str, plan: dict) -> bool:
    """Atomically reorder the current membership, or request a new plan."""

    from .. import main

    lock_queue_date_for_position(database, queue_date)
    current_membership = database.execute_native(
        "SELECT id,card_id FROM daily_queue WHERE queue_date=%s AND status='queued' ORDER BY id",
        (queue_date,),
    ).fetchall()
    if main._queue_membership_hash(current_membership) != plan["membership_hash"]:
        return False
    if plan["entries"] is None:
        return True
    saved = database.execute_native(
        "SELECT membership_hash FROM daily_queue_days WHERE queue_date=%s FOR UPDATE",
        (queue_date,),
    ).fetchone()
    if saved and saved[0] == plan["membership_hash"]:
        return True
    entries = plan["entries"]
    updated_count = database.execute_native(
        """UPDATE daily_queue AS queue
           SET position=planned.position,card_bucket=planned.bucket,
               admission_kind=planned.kind
           FROM unnest(%s::bigint[],%s::bigint[],%s::text[],%s::text[])
                AS planned(id,position,bucket,kind)
           WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'""",
        ([entry["id"] for entry in entries], [entry["position"] for entry in entries],
         [entry["bucket"] for entry in entries], [entry["kind"] for entry in entries],
         queue_date),
    ).rowcount
    if updated_count != len(entries):
        raise RuntimeError("Queue membership changed during atomic randomization")
    database.execute_native(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(%s,%s,%s,%s) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, plan["seed"], plan["membership_hash"],
         datetime.now(timezone.utc).isoformat()),
    )
    return True


_QUARANTINE_OPENING_ELIGIBILITY = """q.queue_date=%s AND q.status='queued'
    AND c.archived=0 AND c.content_type='opening'
    AND EXISTS(SELECT 1 FROM repertoires repertoire
               WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                   SELECT 1 FROM repertoire_cards link
                   WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                WHERE block.repertoire_id=repertoire.id AND block.card_id=c.id))"""


def _prepare_opening_quarantine(queue_date: str, after_entry_id: int) -> dict[str, Any]:
    """Validate a bounded set of opening cards with no database connection open."""

    rows = _bounded_read(
        """SELECT q.id queue_entry_id,c.id card_id,c.start_fen,c.moves_json,
                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line
                           WHERE line.repertoire_id=c.repertoire_id
                           ORDER BY line.created_at LIMIT 1)) trained_color
           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE """
        + _QUARANTINE_OPENING_ELIGIBILITY +
        " AND q.id>%s ORDER BY q.id LIMIT %s",
        (queue_date, after_entry_id, _QUARANTINE_READ_BATCH_SIZE + 1), native=True,
    )
    inspected = rows[:_QUARANTINE_READ_BATCH_SIZE]
    for candidate_index, candidate in enumerate(inspected):
        if candidate["trained_color"] not in {"white", "black"}:
            continue
        try:
            moves = json.loads(candidate["moves_json"])
        except json.JSONDecodeError:
            moves = []
        if not ends_on_trained_move(candidate["start_fen"], moves,
                                    candidate["trained_color"]):
            return {
                "after_entry_id": int(candidate["queue_entry_id"]),
                "has_more": candidate_index < len(rows) - 1,
                "invalid": dict(candidate),
            }
    return {
        "after_entry_id": int(inspected[-1]["queue_entry_id"]) if inspected else after_entry_id,
        "has_more": len(rows) > _QUARANTINE_READ_BATCH_SIZE,
        "invalid": None,
    }


def _quarantine_one_opening(database, queue_date: str, candidate: dict) -> bool:
    """Recheck the prepared card under row locks before skipping it."""

    current = database.execute_native(
        """SELECT c.start_fen,c.moves_json,
                  COALESCE(c.trained_color,(SELECT line.trained_color FROM repertoire_lines line
                           WHERE line.repertoire_id=c.repertoire_id
                           ORDER BY line.created_at LIMIT 1)) trained_color
           FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE """
        + _QUARANTINE_OPENING_ELIGIBILITY +
        " AND q.id=%s AND c.id=%s FOR UPDATE OF q,c",
        (queue_date, candidate["queue_entry_id"], candidate["card_id"]),
    ).fetchone()
    if current is None or any(
        current[column] != candidate[column]
        for column in ("start_fen", "moves_json", "trained_color")
    ):
        return False
    database.execute_native(
        "UPDATE cards SET state='locked' WHERE id=%s", (candidate["card_id"],),
    )
    database.execute_native(
        "UPDATE daily_queue SET status='skipped' WHERE id=%s",
        (candidate["queue_entry_id"],),
    )
    database.execute_native(
        """INSERT INTO queue_projection_diagnostics(queue_date,card_id,message)
           VALUES(%s,%s,%s) ON CONFLICT(queue_date,card_id) DO UPDATE SET
           message=excluded.message""",
        (queue_date, candidate["card_id"],
         "Skipped an incomplete opening card. Edit or re-import its line to study it."),
    )
    return True


_PROJECTION_BLOCKED_COUNT_SQL = """SELECT COUNT(*) FROM (
    SELECT queue.card_id FROM daily_queue queue
    WHERE queue.queue_date=%s AND queue.status='blocked'
    UNION
    SELECT card.id FROM cards card
    WHERE card.content_type='opening' AND card.archived=0
      AND card.state IN ('learning','mature') AND card.due_date<=%s
      AND EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                 WHERE block.card_id=card.id
                   AND (block.repertoire_id=card.repertoire_id OR EXISTS(
                       SELECT 1 FROM repertoire_cards link
                       WHERE link.card_id=card.id
                         AND link.repertoire_id=block.repertoire_id)))
) blocked"""


def _prepare_projection_blocked_count(queue_date: str) -> int:
    return int(_bounded_read(
        _PROJECTION_BLOCKED_COUNT_SQL, (queue_date, queue_date), native=True,
    )[0][0])


def execute_postgres_queue_refresh_slice(task: dict[str, Any]) -> bool:
    """Apply one eligibility phase; later queue phases remain explicitly gated."""

    if not postgres_store.configured():
        raise RuntimeError("PostgreSQL queue refresh requires the PostgreSQL store")
    # Import lazily because main owns the SQLite-compatible eligibility SQL.
    from .. import main

    payload = task["payload"]
    queue_date = str(payload.get("queue_date") or date.today().isoformat())
    phase = str(payload.get("_queue_phase") or _ELIGIBILITY_PHASES[0])
    if phase not in (*_ELIGIBILITY_PHASES, "tactical_introductions",
                     "reset_opening_new", "reset_opening_stale", "reconcile_unseen",
                     "admit_due", "prioritized_openings", "prioritized_opening_item",
                     "admit_study", "randomize_queue", "quarantine",
                     "publish_projection"):
        raise RuntimeError(f"Queue refresh phase is not yet ported: {phase}")
    prepared_tactic = None
    if phase == "tactical_introductions":
        activity_gate.wait_for_foreground()
        prepared_tactic = _prepare_tactical_introduction(queue_date)
    processed_ids = [int(identifier) for identifier in payload.get("processed_ids", [])]
    introduced_counts = {str(identifier): int(count) for identifier, count in
                         payload.get("introduced_counts", {}).items()}
    reconciliation_candidate = None
    starting_count = 0
    if phase == "reconcile_unseen":
        activity_gate.wait_for_foreground()
        reconciliation_candidate, starting_count = _prepare_unseen_reconciliation(
            queue_date, processed_ids, introduced_counts,
        )
    due_card_id = None
    if phase == "admit_due":
        activity_gate.wait_for_foreground()
        due_card_id = _prepare_due_card(queue_date)
    prioritized_plan = None
    if phase == "prioritized_openings":
        activity_gate.wait_for_foreground()
        prioritized_plan = _prepare_prioritized_openings(queue_date)
    study_card_id = None
    if phase == "admit_study":
        activity_gate.wait_for_foreground()
        study_card_id = _prepare_study_admission(queue_date)
    randomization_plan = None
    if phase == "randomize_queue":
        activity_gate.wait_for_foreground()
        randomization_plan = _prepare_queue_randomization(queue_date)
    quarantine_batch = None
    if phase == "quarantine":
        activity_gate.wait_for_foreground()
        quarantine_batch = _prepare_opening_quarantine(
            queue_date, int(payload.get("after_entry_id") or 0),
        )
    blocked_count = None
    if phase == "publish_projection":
        activity_gate.wait_for_foreground()
        blocked_count = _prepare_projection_blocked_count(queue_date)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        if phase == "publish_projection":
            database.execute_native(
                """INSERT INTO queue_projections(queue_date,state,generation,
                                                   refresh_pending,last_error,
                                                   blocked_count,updated_at)
                   VALUES(%s,'ready',1,0,NULL,%s,%s)
                   ON CONFLICT(queue_date) DO UPDATE SET state='ready',
                   generation=queue_projections.generation+1,
                   refresh_pending=0,last_error=NULL,
                   blocked_count=excluded.blocked_count,
                   updated_at=excluded.updated_at""",
                (queue_date, blocked_count, datetime.now(timezone.utc).isoformat()),
            )
            if not complete_task_slice_in_transaction(database, task):
                raise RuntimeError("Queue projection publication lost its task lease")
            return False
        if phase == "unlock_opening":
            next_cursor = main._unlock_eligible_opening_cards(
                database, queue_date,
                after_card_id=str(payload.get("after_card_id") or ""),
                batch_size=_UNLOCK_BATCH_SIZE,
            )
            if next_cursor is not None:
                next_phase = phase
                next_payload = {"queue_date": queue_date, "_queue_phase": phase,
                                "after_card_id": next_cursor}
            else:
                next_phase = _ELIGIBILITY_PHASES[1]
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "tactical_introductions":
            more_tactics = _publish_tactical_introduction(
                database, queue_date, prepared_tactic,
            )
            next_phase = phase if more_tactics else "reset_opening_new"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reset_opening_new":
            main._reset_unintroduced_opening_cards(database, queue_date)
            next_phase = "reset_opening_stale"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reset_opening_stale":
            main._reset_stale_opening_introductions(database, queue_date)
            next_phase = "reconcile_unseen"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "reconcile_unseen":
            if reconciliation_candidate is None:
                next_phase = "admit_due"
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
            else:
                kept = _reconcile_one_unseen_entry(
                    database, queue_date, reconciliation_candidate, starting_count,
                )
                processed_ids.append(int(reconciliation_candidate["id"]))
                repertoire_id = str(reconciliation_candidate["repertoire_id"])
                introduced_counts[repertoire_id] = starting_count + int(kept)
                next_phase = phase
                next_payload = {"queue_date": queue_date, "_queue_phase": phase,
                                "processed_ids": processed_ids,
                                "introduced_counts": introduced_counts}
        elif phase == "admit_due":
            if due_card_id is None:
                next_phase = "prioritized_openings"
            else:
                _admit_one_due_card(database, queue_date, due_card_id)
                next_phase = phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "prioritized_openings":
            next_phase = "prioritized_opening_item" if prioritized_plan else "admit_study"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase,
                            "opening_plan": prioritized_plan or [], "opening_index": 0}
        elif phase == "prioritized_opening_item":
            opening_plan = payload["opening_plan"]
            opening_index = int(payload["opening_index"])
            if opening_index < len(opening_plan):
                _admit_one_prioritized_opening(database, queue_date,
                                               opening_plan[opening_index])
            opening_index += 1
            next_phase = phase if opening_index < len(opening_plan) else "admit_study"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase,
                            "opening_plan": opening_plan, "opening_index": opening_index}
        elif phase == "admit_study":
            if study_card_id is None:
                next_phase = "randomize_queue"
            else:
                _admit_one_study_card(database, queue_date, study_card_id)
                next_phase = phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "randomize_queue":
            published = _publish_queue_randomization(
                database, queue_date, randomization_plan,
            )
            next_phase = "quarantine" if published else phase
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        elif phase == "quarantine":
            if not payload.get("quarantine_started"):
                database.execute_native(
                    "DELETE FROM queue_projection_diagnostics WHERE queue_date=%s",
                    (queue_date,),
                )
            if quarantine_batch["invalid"] is not None:
                _quarantine_one_opening(
                    database, queue_date, quarantine_batch["invalid"],
                )
            next_phase = phase if quarantine_batch["has_more"] else "publish_projection"
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase,
                            "quarantine_started": True,
                            "after_entry_id": quarantine_batch["after_entry_id"]}
        else:
            phase_handlers = {
                "block_opening": main._block_ineligible_opening_queue_entries,
                "block_defense": main._block_unapproved_defense_queue_entries,
                "restore_due": main._restore_eligible_due_queue_entries,
                "restore_study": main._restore_published_study_queue_entries,
            }
            phase_handlers[phase](database, queue_date)
            phase_index = _ELIGIBILITY_PHASES.index(phase)
            next_phase = (_ELIGIBILITY_PHASES[phase_index + 1]
                          if phase_index + 1 < len(_ELIGIBILITY_PHASES)
                          else "tactical_introductions")
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        return advance_task_slice_in_transaction(
            database, task, next_phase=next_phase, next_payload=next_payload,
        )
