"""Current presentation and scoped conflict guards at Study attempt boundaries."""

from app.contracts.review_conflicts import ReviewConflict


def guard_current_attempt(database, queue_entry_id, card_id, revision):
    from tempo_storage.lifecycle.queue_attempt_abandonment import (
        is_abandoned,
    )

    if is_abandoned(database, queue_entry_id, card_id, revision):
        raise ReviewConflict(
            "queue_attempt_retired",
            "This attempt was abandoned. Refresh Study before continuing.",
        )
