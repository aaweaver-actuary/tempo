"""Legacy call-site composition; the caller retains its command transaction."""

from datetime import date

from app.services.real_game_feedback_store import BorrowedFeedbackStore
from . import real_game_feedback as policy
from .real_game_feedback import MISS_REASON


def has_outstanding_real_game_miss(database, card_id):
    return policy.has_outstanding_real_game_miss(
        BorrowedFeedbackStore(database), card_id
    )


def promote_real_game_card(database, card_id, queue_date):
    return policy.promote_real_game_card(
        BorrowedFeedbackStore(database), card_id, queue_date
    )


def prioritize_real_game_miss(database, event_id):
    return policy.prioritize_real_game_miss(
        BorrowedFeedbackStore(database), event_id, date.today().isoformat()
    )


def clear_satisfied_miss_priority(database, card_id):
    policy.clear_satisfied_miss_priority(BorrowedFeedbackStore(database), card_id)


def prioritize_queued_misses(database, queue_date):
    BorrowedFeedbackStore(database).prioritize_queued_misses(queue_date)
