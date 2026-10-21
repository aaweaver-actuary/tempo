"""Real-game obligation policy over semantic facts; no database operations."""
from app.study.review.feedback_contracts import RealGameFeedbackStore

MISS_REASON = "Priority review · missed in a recent game"


def has_outstanding_real_game_miss(store: RealGameFeedbackStore, card_id: str) -> bool:
    return store.outstanding_card(card_id) is not None


def promote_real_game_card(store: RealGameFeedbackStore, card_id: str, queue_date: str) -> bool:
    """Recheck after the card lock; admission and review remain separate effects."""
    store.lock_card(card_id)
    outstanding = store.outstanding_card(card_id)
    if outstanding is None or store.is_buried(card_id, queue_date):
        return False
    if outstanding.state in ("new", "locked") and outstanding.introduced_at is None:
        store.request_introduction(queue_date)
        return True
    if outstanding.state not in ("learning", "mature"):
        return False
    store.lock_queue_day(queue_date)
    store.enqueue_review(card_id, queue_date, MISS_REASON)
    store.prioritize_queued_misses(queue_date)
    return True


def prioritize_real_game_miss(store: RealGameFeedbackStore, event_id: str, queue_day: str) -> bool:
    card_id = store.event_card(event_id)
    return bool(card_id and promote_real_game_card(store, card_id, queue_day))


def clear_satisfied_miss_priority(store: RealGameFeedbackStore, card_id: str) -> None:
    if not has_outstanding_real_game_miss(store, card_id):
        store.replace_miss_priority(card_id, MISS_REASON, store.accepted_miss_priority(card_id))
