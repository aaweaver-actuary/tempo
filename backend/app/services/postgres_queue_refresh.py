"""Bounded PostgreSQL queue-refresh slices with durable phase and cursor state."""

from __future__ import annotations

from datetime import date
import json
from typing import Any

from psycopg.errors import TransactionTimeout

from ..database import background_read_connection, connection
from .. import postgres_store
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


def _bounded_read(statement: str, parameters: tuple = ()) -> list:
    """Retry a read-only section if PostgreSQL closes its 50 ms transaction."""

    for attempt in range(3):
        try:
            with background_read_connection() as database:
                return database.execute(statement, parameters).fetchall()
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
                     "reset_opening_new", "reset_opening_stale"):
        raise RuntimeError(f"Queue refresh phase is not yet ported: {phase}")
    prepared_tactic = None
    if phase == "tactical_introductions":
        activity_gate.wait_for_foreground()
        prepared_tactic = _prepare_tactical_introduction(queue_date)
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
