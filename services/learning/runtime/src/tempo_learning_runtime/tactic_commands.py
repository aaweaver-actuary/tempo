from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .clock import date
import json, uuid
from typing import Any
from .models import TacticAttemptRequest
from tempo_learning_database.adapters.postgres_store import PostgresConnection
from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
from .review_service import ensure_card_queued_after
from tempo_learning_database.adapters.tactic_admission import lock_daily_tactic_admission
from tempo_learning_database.adapters import tactic_commands as storage
from tempo_chess_core import card_id


def submit_tactic_attempt(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    request = TacticAttemptRequest.model_validate(payload["request"])
    pack_id = str(payload["pack_id"])
    training_fen = str(payload["training_fen"])
    solution = [str(move) for move in payload["solution"]]
    tactic_card_id = card_id(training_fen, solution)
    from .card_deletion import require_card_not_deleted

    require_card_not_deleted(database, tactic_card_id)
    lock_daily_tactic_admission(database, date.today().isoformat())
    storage.lock_puzzle(database, request.puzzle_id)
    if request.attempt_id:
        previous = storage.attempt_result(database, request.attempt_id)
        if previous:
            return json.loads(previous[0])
    now = datetime.now(timezone.utc)
    calendar_day = date.today()
    light_days = int(storage.light_interval_days(database))
    storage.ensure_tactic_repertoire(database, now.isoformat())
    clean_at = now.isoformat() if request.correct and request.clean else None
    storage.record_progress(
        database, request.puzzle_id, pack_id, tactic_card_id, clean_at, now.isoformat(),
        "light" if request.correct and request.clean else "normal",
    )
    storage.ensure_tactic_card(
        database, tactic_card_id, training_fen, json.dumps(solution),
        (
            calendar_day + timedelta(days=light_days)
            if request.correct and request.clean
            else calendar_day
        ).isoformat(),
        "tactic", "light" if request.correct and request.clean else "normal",
        request.puzzle_id, request.source_fen, calendar_day.isoformat(),
    )
    if not request.correct or not request.clean:
        storage.restore_normal_learning(database, calendar_day.isoformat(), tactic_card_id)
    if not request.correct:
        lock_queue_date_for_position(database, calendar_day.isoformat())
        if not storage.queued_attempt(database, calendar_day.isoformat(), tactic_card_id):
            ensure_card_queued_after(database, tactic_card_id, 4, "guided")
    saved_card = storage.saved_schedule(database, tactic_card_id)
    storage.retain_admission_mode(database, saved_card[0], request.puzzle_id)
    result = {
        "card_id": tactic_card_id,
        "mode": saved_card[0],
        "next_due": saved_card[1],
    }
    storage.retain_attempt_result(
        database, request.attempt_id or str(uuid.uuid4()), request.deck_id,
        request.puzzle_id, int(request.correct and request.clean), json.dumps(result),
    )
    return result
