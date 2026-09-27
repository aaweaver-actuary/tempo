"""Bounded PostgreSQL queue-refresh slices with durable phase and cursor state."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any

from psycopg.errors import TransactionTimeout

from ..database import background_read_connection, connection
from .. import postgres_store
from ..postgres_store import postgres_sql
from .cards import card_id
from .activity_gate import activity_gate
from .durable_tasks import advance_task_slice_in_transaction, lock_current_slice
from .puzzles import validate_puzzle_record
from .tactical_catalog import pack_records


_ELIGIBILITY_PHASES = (
    "unlock_opening",
    "block_opening",
    "block_defense",
    "restore_due",
    "restore_study",
)
_UNLOCK_BATCH_SIZE = 8


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
        "SELECT COUNT(*) FROM tactic_introductions WHERE introduction_date=?", (queue_date,),
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
    limit = database.execute_native("SELECT tactics_new_per_day FROM settings WHERE id=1").fetchone()[0]
    reserved = database.execute_native(
        "SELECT COUNT(*) FROM tactic_introductions WHERE introduction_date=%s", (queue_date,),
    ).fetchone()[0]
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
    reviewed_count = _bounded_read(
        """SELECT COUNT(*) FROM cards c
           WHERE c.repertoire_id=%s AND c.content_type='opening' AND c.introduced_at=%s
             AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                            WHERE block.repertoire_id=c.repertoire_id AND block.card_id=c.id)""",
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
    limit = database.execute_native("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[0]
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
    candidate_sql = (
        "WITH active_miss AS (SELECT unnest(%s::text[]) AS card_id) "
        + postgres_sql(main._PRIORITY_OPENING_CANDIDATE_BODY)
    )
    candidates = _bounded_read(
        candidate_sql, (active_misses, main.MISS_REASON, queue_date,
                        queue_date, queue_date, queue_date), native=True,
    )
    counts = _bounded_read(
        """SELECT COALESCE(q.admission_repertoire_id,c.repertoire_id),COUNT(DISTINCT c.id)
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=%s AND c.content_type='opening' AND c.introduced_at=%s
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                            WHERE block.repertoire_id=c.repertoire_id AND block.card_id=c.id)
           GROUP BY COALESCE(q.admission_repertoire_id,c.repertoire_id)""",
        (queue_date, queue_date), native=True,
    )
    daily_limit = int(_bounded_read("SELECT new_cards_per_day FROM settings WHERE id=1")[0][0])
    plan = main._plan_prioritized_opening_admissions(
        candidates, dict(counts), queue_date, daily_limit,
    )
    return [{"repertoire_id": repertoire_id, "card_id": candidate["id"],
             "reason": candidate["gameplay_priority_reason"]}
            for repertoire_id, candidate in plan]


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
    database.execute_native(
        """UPDATE repertoire_opportunities SET status='resolved',resolved_at=%s,updated_at=%s
           WHERE repertoire_id=%s AND card_id=%s AND kind='weak_known_decision'
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
             AND card.state='new' AND queue.status IN ('queued','complete')""",
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
    allowance = database.execute_native(
        "SELECT study_new_per_day FROM settings WHERE id=1",
    ).fetchone()[0]
    admitted = database.execute_native(
        """SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
           WHERE queue.queue_date=%s AND card.content_type='study_exercise'
             AND card.state='new' AND queue.status IN ('queued','complete')""",
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
                     "admit_study"):
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
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
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
