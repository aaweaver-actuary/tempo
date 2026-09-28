"""Foreground PostgreSQL command for packaged and historical tactic attempts."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import TacticAttemptRequest
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .queue_commands import request_queue_refresh_in_transaction
from .services.cards import card_id
from .services.review_service import ensure_card_queued_after
from .services.tactical_catalog import activate


def submit_tactic_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = TacticAttemptRequest.model_validate(payload["request"])
    pack_id = str(payload["pack_id"])
    training_fen = str(payload["training_fen"])
    solution = [str(move) for move in payload["solution"]]
    tactic_card_id = card_id(training_fen, solution)
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:tactic-puzzle:{request.puzzle_id}",),
    )
    if request.attempt_id:
        previous = database.execute(
            "SELECT result_json FROM tactic_discovery_attempts WHERE id=?",
            (request.attempt_id,),
        ).fetchone()
        if previous:
            return json.loads(previous[0])
    now = datetime.now(timezone.utc)
    calendar_day = date.today()
    light_days = int(database.execute(
        "SELECT light_first_interval_days FROM settings WHERE id=1",
    ).fetchone()[0])
    database.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) "
        "VALUES('__tactics__','Tactics','Lichess puzzle database',?)",
        (now.isoformat(),),
    )
    clean_at = now.isoformat() if request.correct and request.clean else None
    database.execute(
        "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET "
        "clean_pass_at=COALESCE(tactic_progress.clean_pass_at,excluded.clean_pass_at),"
        "card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at),"
        "admission_mode=excluded.admission_mode",
        (request.puzzle_id, pack_id, tactic_card_id, clean_at, now.isoformat(),
         "light" if request.correct and request.clean else "normal"),
    )
    database.execute(
        "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,"
        "content_type,scheduling_mode,source_ref,source_fen,state) "
        "VALUES(?, '__tactics__','checkpoint',?,?,?,?,?,?,?,'learning')",
        (tactic_card_id, training_fen, json.dumps(solution),
         (calendar_day + timedelta(days=light_days)
          if request.correct and request.clean else calendar_day).isoformat(),
         "tactic", "light" if request.correct and request.clean else "normal",
         request.puzzle_id, request.source_fen),
    )
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (tactic_card_id,))
    if not request.correct or not request.clean:
        database.execute(
            "UPDATE cards SET state='learning',"
            "scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END,"
            "due_date=? WHERE id=?",
            (calendar_day.isoformat(), tactic_card_id),
        )
    if not request.correct:
        lock_queue_date_for_position(database, calendar_day.isoformat())
        if not database.execute(
            "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued'",
            (calendar_day.isoformat(), tactic_card_id),
        ).fetchone():
            ensure_card_queued_after(database, tactic_card_id, 4, "guided")
    saved_card = database.execute(
        "SELECT scheduling_mode,due_date FROM cards WHERE id=?", (tactic_card_id,),
    ).fetchone()
    database.execute(
        "UPDATE tactic_progress SET admission_mode=? WHERE puzzle_id=?",
        (saved_card[0], request.puzzle_id),
    )
    result = {"card_id": tactic_card_id, "mode": saved_card[0], "next_due": saved_card[1]}
    database.execute(
        "INSERT INTO tactic_discovery_attempts VALUES(?,?,?,?,?)",
        (request.attempt_id or str(uuid.uuid4()), request.deck_id, request.puzzle_id,
         int(request.correct and request.clean), json.dumps(result)),
    )
    return result


register_command("tactics.attempt.submit", submit_tactic_attempt)


def set_tactic_activation(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    try:
        activate(database, [str(pack_id) for pack_id in payload["pack_ids"]], bool(payload["active"]))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"updated": True}


register_command("tactics.activation.set", set_tactic_activation)
