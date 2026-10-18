"""Legacy composition of Learning's narrow queue-selection adapter."""
import tempo_learning_review as learning
from tempo_learning_runtime.retry_queue import QueueSelectionAdapter
from . import queue_application, review_service


def selection_store(database):
    from ..queue_commands import _lock_attempt_card
    from ..queue_position_lock import lock_queue_date_for_position
    from .real_game_feedback import MISS_REASON, has_outstanding_real_game_miss
    return QueueSelectionAdapter(database,queue_application=queue_application,
        review_service=review_service,
        lock_card=lambda database,identity: _lock_attempt_card(database,identity.entry_id,
            retained_identity=identity if identity.issued else None),
        lock_day=lock_queue_date_for_position,
        priority_for_retry=lambda database,card_id: MISS_REASON if has_outstanding_real_game_miss(database,card_id) else None)


def select_queue_head(database, *, observed_at, local_date):
    return learning.select_queue_head(selection_store(database),learning.QueueContext(observed_at,local_date))
