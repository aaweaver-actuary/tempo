"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations
from typing import TYPE_CHECKING
from tempo_storage.postgres import PostgresConnection

from datetime import date, datetime
import json
from tempo_storage.study.review import storage

from tempo_storage.opening_progression import (
    unlock_legacy_children_after_review,
)
from .scheduler import schedule_review, unlock_ready
from app.study.queue_order import capture_daily_queue_order, preserve_daily_queue_order
from app.study.queue_placement import ensure_card_queued_after

if TYPE_CHECKING:
    from .scheduling_review_contracts import SchedulingReviewResult


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
) -> SchedulingReviewResult:
    from .scheduling_review_application import (
        apply_scheduling_review as apply_review_to_store,
    )
    from tempo_storage.scheduling_review_store import (
        OwnedConnectionSchedulingReviewStore,
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
