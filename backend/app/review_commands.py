"""Idempotent foreground review command for the PostgreSQL worker."""

from __future__ import annotations

from datetime import date
from typing import Any

from .command_gateway import register_command
from .models import ReviewRequest
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position


def submit_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    # Import lazily: main owns the scheduling rules and imports the Celery app.
    from .main import _apply_review

    request = ReviewRequest.model_validate(payload["review"])
    card_id = str(payload["card_id"])
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
    lock_queue_date_for_position(database, date.today().isoformat())
    return _apply_review(card_id, request, database=database)


register_command("cards.review", submit_review)


def reconcile_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from .main import _reconcile_review

    request = ReviewRequest.model_validate(payload["review"])
    card_id = str(payload["card_id"])
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))
    lock_queue_date_for_position(database, date.today().isoformat())
    return _reconcile_review(card_id, request, database=database)


register_command("cards.review.reconcile", reconcile_review)
