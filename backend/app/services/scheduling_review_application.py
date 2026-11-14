"""Scheduling decisions over application-owned persistence capabilities.

The caller owns the transaction, completion clock, and source identity. This
module neither acquires infrastructure nor performs queue/progression follow-ups.
"""

from __future__ import annotations

from datetime import date, datetime

from app.study.policy import scheduling_review_application as _owned

from .scheduler import schedule_review, unlock_ready

# Explicit compatibility re-exports preserve the original public imports.
from .scheduling_review_contracts import (
    CardState as CardState,
)
from .scheduling_review_contracts import (
    MaturityFacts as MaturityFacts,
)
from .scheduling_review_contracts import (
    OpeningSeedFacts as OpeningSeedFacts,
)
from .scheduling_review_contracts import (
    ReviewRecord as ReviewRecord,
)
from .scheduling_review_contracts import (
    SchedulingReviewResult as SchedulingReviewResult,
)
from .scheduling_review_contracts import (
    SchedulingReviewStore as SchedulingReviewStore,
)
from .scheduling_review_contracts import (
    UpdatedCardState as UpdatedCardState,
)


def apply_scheduling_review(
    store: SchedulingReviewStore,
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
    return _owned.apply_scheduling_review(
        store,
        card_id,
        outcome,
        guided=guided,
        source_kind=source_kind,
        source_ref=source_ref,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at,
        review_day=review_day,
        schedule_review_function=schedule_review,
        unlock_ready_function=unlock_ready,
    )
