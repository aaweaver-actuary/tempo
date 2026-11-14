"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime

from tempo_storage.opening_progression import (
    unlock_legacy_children_after_review,
)
from tempo_storage.postgres import PostgresConnection


def apply_scheduling_review(
    database: PostgresConnection,
    card_id: str,
    outcome: str,
    *,
    guided: bool,
    source_kind: str,
    source_ref: str | None,
    light_first_interval_days: int,
    reviewed_at: datetime,
    review_day: date,
) -> dict:
    from tempo_storage.scheduling_review_store import (
        OwnedConnectionSchedulingReviewStore,
    )

    from .scheduling_review_application import (
        apply_scheduling_review as apply_review_to_store,
    )

    result = apply_review_to_store(
        OwnedConnectionSchedulingReviewStore(database),
        card_id,
        outcome,
        guided=guided,
        source_kind=source_kind,
        source_ref=source_ref,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at,
        review_day=review_day,
    )
    if result["idempotent"]:
        return result
    unlock_legacy_children_after_review(
        database, card_id, review_day.isoformat(), result["state"]
    )
    return result
