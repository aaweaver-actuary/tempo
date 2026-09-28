"""Foreground defensive exercise commands with atomic PostgreSQL receipts."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import DefenseAttemptRequest, DefenseRecognitionRequest
from .postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction
from .services.threat_training import (
    approve_defense_candidate, dismiss_defense_candidate, pause_defense_candidate,
    submit_defense_attempt, submit_defense_recognition, train_defense_candidate_now,
)


def submit_recognition(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    request = DefenseRecognitionRequest.model_validate(payload["request"])
    try:
        return submit_defense_recognition(
            str(payload["candidate_id"]), request, write_database=database,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


def submit_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    request = DefenseAttemptRequest.model_validate(payload["request"])
    try:
        return submit_defense_attempt(
            str(payload["candidate_id"]), attempt_id=request.attempt_id,
            exercise_revision=request.exercise_revision, queue_entry_id=request.queue_entry_id,
            move_uci=request.move_uci, recognition_attempt_id=request.recognition_attempt_id,
            light_first_interval_days=int(payload["light_first_interval_days"]),
            write_database=database,
        )
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


register_command("defense.recognition.submit", submit_recognition)
register_command("defense.attempt.submit", submit_attempt)


def change_candidate_state(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    candidate_id = str(payload["candidate_id"])
    action = str(payload["action"])
    try:
        if action == "dismiss":
            dismiss_defense_candidate(candidate_id, write_database=database)
            return {"status": "dismissed"}
        if action == "approve":
            card_id = approve_defense_candidate(candidate_id, write_database=database)
            request_queue_refresh_in_transaction(database, date.today().isoformat())
            return {"status": "approved", "card_id": card_id}
        if action == "train-now":
            card_id = train_defense_candidate_now(candidate_id, write_database=database)
            return {"status": "queued", "card_id": card_id}
        if action in {"pause", "resume"}:
            pause_defense_candidate(candidate_id, action == "pause", write_database=database)
            if action == "resume":
                today = date.today().isoformat()
                enqueue_compact_postgres_task_in_transaction(
                    database, "defensive_admission", today,
                    {"queue_date": today, "phase": "threat", "cursor": "",
                     "control_exhausted": False}, priority=150,
                )
            return {"status": "paused" if action == "pause" else "eligible"}
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    raise HTTPException(422, "Unknown defensive candidate action")


register_command("defense.candidate.change", change_candidate_state)
