"""Current explicit next-card command; accepted Study work survives Games exit.

Construction is inert. Optional delivery is injected only by a verified
feedback-mode assembly; its absence never disables the existing Study owner.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from tempo_legacy_database.connections import connection, read_connection
from tempo_legacy_database.adapters.study.game_feedback import (
    feedback_preparation_admitted, installed, pending_feedback_preparation,
)

from app.legacy_game_observation import capture_game
from app.services.queue_next_card import configure_next_card_work
from app.study.interfaces.next_card import NextCardPreparation
from .feedback_maintenance import advance_local_feedback


def register_current_next_card_priority(*, read=read_connection, write=connection,
        capture=capture_game, clock=None,
        local_handoff: Callable[[], NextCardPreparation] | None = None,
        source_pending: Callable[[object, str], bool] | None = None,
        qualification_prepare=None):
    clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare():
        delivery = local_handoff() if local_handoff else NextCardPreparation()
        with read() as database:
            if not installed(database):
                return delivery
            admitted = feedback_preparation_admitted(database)
        if not admitted:
            return NextCardPreparation(delivery.progressed, delivery.pending, 'paused')
        # No command/head/review writer is open. Each owner unit prepares its
        # complete local facts first and owns its separate bounded transaction.
        progressed = advance_local_feedback(read=read, write=write, capture=capture,
            clock=clock, operation_admitted=admitted,
            qualification_prepare=qualification_prepare)
        with read() as database:
            from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
            day = PostgresGameFeedbackStore(database).study_day(clock()).isoformat()
            pending = pending_feedback_preparation(database, day)
        return NextCardPreparation(delivery.progressed or progressed,
            delivery.pending or pending, delivery.reason or ('study_feedback_preparation_pending' if pending else None))

    configure_next_card_work(prepare=prepare, source_pending=source_pending)
