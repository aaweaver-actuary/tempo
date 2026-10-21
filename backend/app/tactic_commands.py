"""Foreground PostgreSQL command for packaged and historical tactic attempts."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from app.contracts.transport.tactics import TacticAttemptRequest, TacticCaptureRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .queue_commands import request_queue_refresh_in_transaction
from app.chess.cards import card_id
from .services.review_service import ensure_card_queued_after
from .services.tactical_catalog import activate
from .services.tactic_capture import capture_tactic
from tempo_legacy_database.adapters.tactic_admission import lock_daily_tactic_admission
from tempo_legacy_database.adapters.tactics import attempts as storage


def create_tactic_capture(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    result = capture_tactic(database, TacticCaptureRequest.model_validate(payload["request"]))
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return result


register_command("tactics.capture.create", create_tactic_capture)


def submit_tactic_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = TacticAttemptRequest.model_validate(payload["request"])
    pack_id = str(payload["pack_id"])
    training_fen = str(payload["training_fen"])
    solution = [str(move) for move in payload["solution"]]
    tactic_card_id = card_id(training_fen, solution)
    from .card_deletion import require_card_not_deleted
    require_card_not_deleted(database, tactic_card_id)
    lock_daily_tactic_admission(database, date.today().isoformat())
    storage.lock_puzzle_attempt(database, request.puzzle_id)
    if request.attempt_id:
        previous = storage.attempt_receipt(database, request.attempt_id)
        if previous:
            return json.loads(previous[0])
    now = datetime.now(timezone.utc)
    calendar_day = date.today()
    light_days = int(storage.first_light_interval(database))
    storage.ensure_tactic_repertoire(database, now.isoformat())
    clean_at = now.isoformat() if request.correct and request.clean else None
    storage.save_progress(database, request.puzzle_id, pack_id, tactic_card_id, clean_at, now.isoformat(),
                          "light" if request.correct and request.clean else "normal")
    storage.create_attempt_card(database, tactic_card_id, training_fen, json.dumps(solution),
         (calendar_day + timedelta(days=light_days)
          if request.correct and request.clean else calendar_day).isoformat(),
         "tactic", "light" if request.correct and request.clean else "normal",
         request.puzzle_id, request.source_fen, calendar_day.isoformat())
    storage.lock_attempt_card(database, tactic_card_id)
    if not request.correct or not request.clean:
        storage.reopen_attempt_card(database, calendar_day.isoformat(), tactic_card_id)
    if not request.correct:
        lock_queue_date_for_position(database, calendar_day.isoformat())
        if not storage.queued_attempt_card(database, calendar_day.isoformat(), tactic_card_id):
            ensure_card_queued_after(database, tactic_card_id, 4, "guided")
    saved_card = storage.attempt_card_schedule(database, tactic_card_id)
    storage.save_admission_mode(database, saved_card[0], request.puzzle_id)
    result = {"card_id": tactic_card_id, "mode": saved_card[0], "next_due": saved_card[1]}
    storage.save_attempt_receipt(database, request.attempt_id or str(uuid.uuid4()), request.deck_id,
                                 request.puzzle_id, int(request.correct and request.clean), json.dumps(result))
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
