"""Scheduling SQL adapter borrowing an existing caller-owned connection."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .scheduling_review_application import (
    CardState, MaturityFacts, OpeningSeedFacts, ReviewRecord, UpdatedCardState,
)

if TYPE_CHECKING:
    import sqlite3
    from ..postgres_store import PostgresConnection


class OwnedConnectionSchedulingReviewStore:
    def __init__(self, database: sqlite3.Connection | PostgresConnection):
        self._database = database

    def source_review_exists(self, source_kind: str, source_ref: str) -> bool:
        return self._database.execute(
            'SELECT 1 FROM reviews WHERE source_kind=? AND source_ref=?',
            (source_kind, source_ref),
        ).fetchone() is not None

    def load_card_state(self, card_id: str, *, require_active: bool) -> CardState | None:
        card = self._database.execute(
            '''SELECT due_date,interval_days,fsrs_card_json,first_correct_at,
                      reinforcement_pending,stability,guided_review,state,
                      scheduling_mode,hard_correct_streak,recent_attempts_json
               FROM cards WHERE id=?''' + (' AND archived=0' if require_active else ''),
            (card_id,),
        ).fetchone()
        if card is None:
            return None
        return CardState(
            card['due_date'], card['interval_days'], card['fsrs_card_json'],
            card['first_correct_at'], bool(card['reinforcement_pending']),
            card['stability'], bool(card['guided_review']), card['state'],
            card['scheduling_mode'], card['hard_correct_streak'], card['recent_attempts_json'],
        )

    def append_review(self, review: ReviewRecord) -> None:
        self._database.execute(
            '''INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,
                   previous_interval,next_interval,source_kind,source_ref)
               VALUES(?,?,?,?,?,?,?,?,?)''',
            (review.card_id, review.rating, review.internal_rating, int(review.guided),
             review.reviewed_at.isoformat(), review.previous_interval, review.next_interval,
             review.source_kind, review.source_ref),
        )

    def load_maturity_facts(self, card_id: str) -> MaturityFacts:
        successful_days = self._database.execute(
            "SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct' AND invalidated_at IS NULL",
            (card_id,),
        ).fetchone()[0]
        seed = self._database.execute(
            '''SELECT baseline_successful_days,baseline_recent_clean
               FROM opening_card_schedule_seeds WHERE card_id=?''', (card_id,),
        ).fetchone()
        recent_outcomes = tuple(row[0] for row in self._database.execute(
            '''SELECT rating FROM reviews WHERE card_id=? AND invalidated_at IS NULL
               ORDER BY reviewed_at DESC,id DESC LIMIT 2''', (card_id,),
        ))
        return MaturityFacts(successful_days, recent_outcomes,
                             OpeningSeedFacts(int(seed['baseline_successful_days']),
                                              int(seed['baseline_recent_clean'])) if seed else None)

    def save_card_state(self, card_id: str, state: UpdatedCardState) -> None:
        self._database.execute(
            '''UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,
                   reinforcement_pending=?,stability=?,guided_review=?,state=?,scheduling_mode=?,
                   hard_correct_streak=?,recent_attempts_json=? WHERE id=?''',
            (state.due_date, state.interval_days, state.fsrs_card_json, state.first_correct_at,
             int(state.reinforcement_pending), state.stability, int(state.guided_review),
             state.state, state.scheduling_mode, state.hard_correct_streak,
             json.dumps(state.recent_attempts), card_id),
        )
