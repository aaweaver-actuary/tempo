"""Current presentation and scoped conflict guards at Study attempt boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.contracts.review_conflicts import ReviewConflict

if TYPE_CHECKING:
    from tempo_storage.postgres import PostgresConnection


def guard_current_attempt(database: PostgresConnection, queue_entry_id: int | None, card_id: str | None, revision: int | None) -> None:
    from tempo_storage.lifecycle.queue_attempt_abandonment import (
        is_abandoned,
    )

    if is_abandoned(database, queue_entry_id, card_id, revision):
        raise ReviewConflict(
            "queue_attempt_retired",
            "This attempt was abandoned. Refresh Study before continuing.",
        )
