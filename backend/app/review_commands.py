"""Idempotent foreground review command for the PostgreSQL worker."""

from __future__ import annotations

from typing import Any

from .command_gateway import register_command
from .models import ReviewRequest
from .postgres_store import PostgresConnection


def submit_review(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    # Import lazily: main owns the scheduling rules and imports the Celery app.
    from .main import _apply_review

    request = ReviewRequest.model_validate(payload["review"])
    return _apply_review(str(payload["card_id"]), request, database=database)


register_command("cards.review", submit_review)
