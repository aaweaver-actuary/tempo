"""Foreground defensive exercise commands with atomic PostgreSQL receipts."""

from __future__ import annotations

from typing import Any

from .command_gateway import register_command
from .models import DefenseAttemptRequest, DefenseRecognitionRequest
from .postgres_store import PostgresConnection
from .services.threat_training import submit_defense_attempt, submit_defense_recognition


def submit_recognition(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    request = DefenseRecognitionRequest.model_validate(payload["request"])
    return submit_defense_recognition(
        str(payload["candidate_id"]), request, write_database=database,
    )


def submit_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    request = DefenseAttemptRequest.model_validate(payload["request"])
    return submit_defense_attempt(
        str(payload["candidate_id"]), attempt_id=request.attempt_id,
        exercise_revision=request.exercise_revision, queue_entry_id=request.queue_entry_id,
        move_uci=request.move_uci, recognition_attempt_id=request.recognition_attempt_id,
        light_first_interval_days=int(payload["light_first_interval_days"]),
        write_database=database,
    )


register_command("defense.recognition.submit", submit_recognition)
register_command("defense.attempt.submit", submit_attempt)
