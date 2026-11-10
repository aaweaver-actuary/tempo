"""Scheduling SQL adapter borrowing an existing caller-owned connection."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.study.policy.scheduling_review_contracts import CardState, MaturityFacts, OpeningSeedFacts, ReviewRecord, UpdatedCardState

if TYPE_CHECKING:
    from tempo_storage.postgres import PostgresConnection


class OwnedConnectionSchedulingReviewStore:
    def __init__(self, database: PostgresConnection):
        self._database = database

    def source_review_exists(self, source_kind: str, source_ref: str) -> bool:
        return (
            self._database.execute(
                'SELECT 1 FROM reviews WHERE source_kind=%s AND source_ref=%s',
                (source_kind, source_ref),
            ).fetchone()
            is not None
        )

    def load_card_state(
        self, card_id: str, *, require_active: bool
    ) -> CardState | None:
        card = self._database.execute(
            'SELECT due_date,interval_days,fsrs_card_json,first_correct_at,\n                      reinforcement_pending,stability,guided_review,state,\n                      scheduling_mode,hard_correct_streak,recent_attempts_json\n               FROM cards WHERE id=%s'
            + (" AND archived=0" if require_active else ""),
            (card_id,),
        ).fetchone()
        if card is None:
            return None
        return CardState(
            card["due_date"],
            card["interval_days"],
            card["fsrs_card_json"],
            card["first_correct_at"],
            bool(card["reinforcement_pending"]),
            card["stability"],
            bool(card["guided_review"]),
            card["state"],
            card["scheduling_mode"],
            card["hard_correct_streak"],
            card["recent_attempts_json"],
        )

    def append_review(self, review: ReviewRecord) -> None:
        self._database.execute(
            'INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,\n                   previous_interval,next_interval,source_kind,source_ref)\n               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (
                review.card_id,
                review.rating,
                review.internal_rating,
                int(review.guided),
                review.reviewed_at.isoformat(),
                review.previous_interval,
                review.next_interval,
                review.source_kind,
                review.source_ref,
            ),
        )

    def load_maturity_facts(self, card_id: str) -> MaturityFacts:
        successful_days = self._database.execute(
            "SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=%s AND rating='correct' AND invalidated_at IS NULL",
            (card_id,),
        ).fetchone()[0]
        seed = self._database.execute(
            'SELECT baseline_successful_days,baseline_recent_clean\n               FROM opening_card_schedule_seeds WHERE card_id=%s',
            (card_id,),
        ).fetchone()
        recent_outcomes = tuple(
            row[0]
            for row in self._database.execute(
                'SELECT rating FROM reviews WHERE card_id=%s AND invalidated_at IS NULL\n               ORDER BY reviewed_at DESC,id DESC LIMIT 2',
                (card_id,),
            )
        )
        return MaturityFacts(
            successful_days,
            recent_outcomes,
            OpeningSeedFacts(
                int(seed["baseline_successful_days"]),
                int(seed["baseline_recent_clean"]),
            )
            if seed
            else None,
        )

    def save_card_state(self, card_id: str, state: UpdatedCardState) -> None:
        self._database.execute(
            'UPDATE cards SET due_date=%s,interval_days=%s,fsrs_card_json=%s,first_correct_at=%s,\n                   reinforcement_pending=%s,stability=%s,guided_review=%s,state=%s,scheduling_mode=%s,\n                   hard_correct_streak=%s,recent_attempts_json=%s WHERE id=%s',
            (
                state.due_date,
                state.interval_days,
                state.fsrs_card_json,
                state.first_correct_at,
                int(state.reinforcement_pending),
                state.stability,
                int(state.guided_review),
                state.state,
                state.scheduling_mode,
                state.hard_correct_streak,
                json.dumps(state.recent_attempts),
                card_id,
            ),
        )
