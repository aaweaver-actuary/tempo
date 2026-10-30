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
    is_control: bool = False

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
        if receipt is None and self.effect.reason:
            # A retained recoverable debt is still pending even though this
            # command has completed. A terminal owner hold permits other work.
            return NextCardPreparation(False, not self.effect.terminal, self.effect.reason)
        progressed = bool(receipt is not None and receipt.admission_status == 'available')
        return NextCardPreparation(progressed, bool(receipt is not None and not progressed),
            receipt.reason if receipt is not None and not progressed else None)


def _observed_priority(observed):
    from app.study.game_feedback import effect_id
    facts = observed.progress or observed.qualification
    command = (observed.encounter or observed.lapse) if facts is None else None
    # Old accepted commands retain their actual detection/receipt clock and
    # historical class until current qualification establishes stronger facts.
    determined_at = (facts.determined_at if facts else
        command.detected_at if command is not None else observed.received_at.isoformat())
    return dict(urgent_determined_at=determined_at,
        urgent_stream=facts.stream if facts else 'reconciliation',
        urgent_effect_id=effect_id(observed.key))


def register_current_next_card_priority(*, read=read_connection,
        capture=capture_game, clock=None,
        local_handoff_prepare: Callable[..., PreparedNextCardWork | None] | None = None,
        source_pending: Callable[..., bool] | None = None,
        qualification_prepare=None):
    clock = clock or (lambda: datetime.now(timezone.utc))

    def prepare():
        # This callback is deliberately pure. Gateway preparers can run before
        # discovering a complete/conflicting operation or a stale attempt token.
        with read() as database:
            available = installed(database)
            admitted = available and feedback_preparation_admitted(database)
            observed = None
            if admitted:
                day = PostgresGameFeedbackStore(database).study_day(clock()).isoformat()
                observed = next_feedback_preparation(database, day, now=clock())
        priority = _observed_priority(observed) if observed is not None else None
        # The optional owner selects controls or earlier source work by cheap
        # metadata before projecting/capturing it. Only the winning unit pays
        # preparation cost; all reads close before the gateway writer opens.
        delivery = local_handoff_prepare(before_priority=priority) if local_handoff_prepare else None
        if delivery is not None and delivery.is_control:
            return delivery
        if not available:
            return delivery or NoNextCardWork()
        if not admitted:
            return delivery or NoNextCardWork(NextCardPreparation(reason='paused'))
        if observed is None:
            return delivery or NoNextCardWork()
        if (delivery is not None and delivery.priority is not None
                and urgent_priority_key(delivery.priority) < urgent_priority_key(priority)):
            return delivery
        effect = prepare_feedback_effect(observed, read=read, capture=capture,
            clock=clock, qualification_prepare=qualification_prepare)
        return PreparedCurrentStudyWork(effect, admitted, clock, priority)

    configure_next_card_work(prepare=prepare, source_pending=source_pending)
