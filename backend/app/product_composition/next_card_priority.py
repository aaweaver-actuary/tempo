"""Current explicit next-card command; accepted Study work survives Games exit.

Construction is inert. Optional delivery is injected only by a verified
feedback-mode assembly; its absence never disables the existing Study owner.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from tempo_legacy_database.connections import read_connection
from tempo_legacy_database.adapters.study.game_feedback import (
    feedback_preparation_admitted, installed, next_feedback_preparation,
    PostgresGameFeedbackStore,
)

from app.legacy_game_observation import capture_game
from app.services.queue_next_card import configure_next_card_work
from app.study.interfaces.next_card import NextCardPreparation, PreparedNextCardWork, NoNextCardWork
from app.services.queue_ordering import urgent_priority_key
from .feedback_maintenance import prepare_feedback_effect, apply_prepared_feedback_effect


@dataclass(frozen=True)
class PreparedCurrentStudyWork:
    effect: object
    admitted: bool
    clock: Callable
    priority: dict[str, str]

    def apply(self, database):
        if not self.admitted or not feedback_preparation_admitted(database,lock=True):
            return NextCardPreparation(reason='paused')
        # Due-queue task ownership precedes every effect/card/day lock. A
        # deferred introduction never acquires the task after those locks.
        introduction = self.effect.prepared_introduction
        if introduction is not None and not introduction.reason:
            from tempo_legacy_database.adapters.queue_refresh.application.admission import has_missing_due_card
            day = PostgresGameFeedbackStore(database).study_day(self.clock()).isoformat()
            if has_missing_due_card(database, day):
                from app.services.queue_application import ensure_due_refresh_in_transaction
                ensure_due_refresh_in_transaction(database, day)
                return NextCardPreparation(False, True, 'due_queue_refresh_pending')
        receipt = apply_prepared_feedback_effect(database, self.effect, clock=self.clock,
                                                 operation_admitted=self.admitted)
        progressed = bool(receipt is not None and receipt.admission_status == 'available')
        return NextCardPreparation(progressed, bool(receipt is not None and not progressed),
            receipt.reason if receipt is not None and not progressed else None)


def register_current_next_card_priority(*, read=read_connection,
        capture=capture_game, clock=None,
        local_handoff_prepare: Callable[[], PreparedNextCardWork | None] | None = None,
        source_pending: Callable[..., bool] | None = None,
        qualification_prepare=None):
    clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare():
        # This callback is deliberately pure. Gateway preparers can run before
        # discovering a complete/conflicting operation or a stale attempt token.
        delivery = local_handoff_prepare() if local_handoff_prepare else None
        with read() as database:
            if not installed(database):
                return delivery or NoNextCardWork()
            admitted = feedback_preparation_admitted(database)
            day = PostgresGameFeedbackStore(database).study_day(clock()).isoformat()
            observed = next_feedback_preparation(database, day, now=clock()) if admitted else None
        if not admitted:
            return delivery or NoNextCardWork(NextCardPreparation(reason='paused'))
        if observed is None:
            return delivery or NoNextCardWork()
        progress, qualification = observed.progress, observed.qualification
        from app.study.game_feedback import effect_id
        priority = dict(urgent_determined_at=progress.determined_at if progress else qualification.determined_at,
            urgent_stream=progress.stream if progress else qualification.stream, urgent_effect_id=effect_id(observed.key))
        if (delivery is not None and delivery.priority is not None
                and urgent_priority_key(delivery.priority) < urgent_priority_key(priority)):
            return delivery
        effect = prepare_feedback_effect(observed, read=read, capture=capture,
            clock=clock, qualification_prepare=qualification_prepare)
        return PreparedCurrentStudyWork(effect, admitted, clock, priority)

    configure_next_card_work(prepare=prepare, source_pending=source_pending)
