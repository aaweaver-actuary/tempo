"""Fixed B02 inputs, independent of PR177's evaluation corpus."""
from datetime import datetime, timezone

import chess
from fsrs import Card

from app.services.decision_readiness import (
    PresentationIdentity, ReadinessSnapshot, StudyReviewEvidence, decision_readiness,
)
from app.services.opening_card_valuation import _plain, evaluate_opening_card_valuation
from app.services.scheduler import schedule_review
from test_opening_card_valuation import fixture


def fixed_contract():
    reviewed_at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    as_of = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    schedule = schedule_review("correct", fsrs_card_json=Card(card_id=200, due=reviewed_at).to_json(),
                               reviewed_at=reviewed_at, review_day=reviewed_at.date())
    readiness = ReadinessSnapshot(
        PresentationIdentity("fixed-card", 1, "italian", "white"), chess.STARTING_FEN, ("e2e4",),
        fsrs_card_json=schedule.fsrs_card_json, state="learning", due_date=schedule.due_date,
        reinforcement_pending=schedule.reinforcement_pending,
        reviews=(StudyReviewEvidence(200, "fixed-card", reviewed_at, "correct", card_revision=1),),
    )
    return {"valuation": evaluate_opening_card_valuation(fixture()),
            "readiness": _plain(decision_readiness(readiness, as_of)), "schedule": _plain(schedule)}
