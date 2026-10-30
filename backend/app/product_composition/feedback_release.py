"""Concrete first-departure release wiring; import performs no runtime I/O.

These capabilities describe source assembly. The managed operator separately
requires exact image, schema, complete scope, credentials and verified boot
intent before enabling new Games handoff. Accepted Study obligations retain
their own preparation path when optional Games is absent.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from app.games.feedback.interfaces.managed_mode import FeedbackReleaseCapabilities
from app.study.interfaces.next_card import NextCardPreparation


@dataclass(frozen=True)
class PreparedCurrentGamesWork:
    work: object
    clock: object
    current_runtime_id: str

    @property
    def priority(self):
        return self.work.priority

    @property
    def is_control(self):
        return self.work.is_control

    def apply(self, database):
        from .provider_priority import apply_prepared_determined_feedback
        result = apply_prepared_determined_feedback(database,self.work,clock=self.clock,
            current_runtime_id=self.current_runtime_id)
        return NextCardPreparation(result.progressed,result.pending,result.held_reason)


def register_feedback_next_card_priority(*, workspace="legacy",read=None,clock=None):
    """Register optional local handoff and independent accepted Study work.

    Construction reads only the optional local boot identity, outside writers.
    It does not require Games configuration, provider, daemon, schema or health.
    Every explicit command prepares one immutable source unit with closed reads;
    only the receipt-fenced handler can apply it in its borrowed transaction.
    """
    from tempo_legacy_database.connections import read_connection
    from tempo_legacy_database.adapters.games.managed_mode import feedback_handoff_admitted
    from .managed_runtime import current_managed_runtime_identity
    from .game_review import SystemClock
    from .next_card_priority import register_current_next_card_priority
    from .provider_priority import (_owner_capture,prepare_determined_feedback,
        pending_determined_feedback_in_transaction)
    read = read or read_connection
    clock = clock or SystemClock()
    # This fixed process boot snapshot is only a read hint. Real NEW acceptance
    # uses a fresh snapshot after expensive preparation and the shared row fence.
    runtime_id = current_managed_runtime_identity()

    @contextmanager
    def connections(*, repertoire_snapshot=False):
        options = ({"repertoire_snapshot": True} if repertoire_snapshot
            and read is read_connection else {})
        with read(**options) as database:
            yield database.raw

    def admitted():
        return feedback_handoff_admitted(workspace,connections,
            runtime_id=current_managed_runtime_identity())

    def prepare():
        try:
            work = prepare_determined_feedback(workspace=workspace,connections=connections,
                clock=clock,admitted=admitted)
        except Exception:
            # Optional delivery failure cannot hide accepted Study preparation.
            return None
        # The last identity read belongs here, before the gateway opens its
        # writer; a persisted/preparation token alone never proves current boot.
        fresh = current_managed_runtime_identity()
        if work is None or fresh is None or fresh != work.mode_runtime_id:
            return None
        return PreparedCurrentGamesWork(work,clock,fresh)

    def source_pending(database,_day,*,before_priority=None):
        return pending_determined_feedback_in_transaction(database,workspace=workspace,
            runtime_id=runtime_id,before_priority=before_priority)

    register_current_next_card_priority(read=read,
        capture=_owner_capture(connections),
        clock=lambda:datetime.fromisoformat(clock.utc_now().replace("Z","+00:00")),
        local_handoff_prepare=prepare,source_pending=source_pending)


def feedback_capabilities() -> FeedbackReleaseCapabilities:
    """Declare actual callable bindings, never mode or packaged proof by itself."""
    from app.games.feedback.interfaces import select_first_boundary
    from app.services.encounter_preparation import prepare_encounter_introduction
    from app.services.queue_admission_adapter import current_owner_encounter_introductions
    from app.services.queue_next_card import prepare_next_card_command,pending_next_card_preparation
    from tempo_legacy_database.adapters.study.issuance import stamp_issued_head
    from app.study.encounter_feedback import EncounterFeedback
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    from .current_first_boundary import prepare_current_first_boundary
    from .provider_priority import prepare_feedback_qualification,apply_prepared_determined_feedback
    from .feedback_maintenance import prepare_feedback_effect,apply_prepared_feedback_effect
    return FeedbackReleaseCapabilities(policy_version="first_departure_v1",receipt_interface_version="0.2.2",
        selector=all(map(callable,(select_first_boundary,prepare_current_first_boundary))),
        current_authority=all(map(callable,(CurrentOwnerDecisions.prepare_qualified,CurrentOwnerDecisions.current_qualified))),
        again=all(map(callable,(EncounterFeedback.refresh_encounter,apply_prepared_feedback_effect))),
        canonical_introduction=all(map(callable,(prepare_encounter_introduction,current_owner_encounter_introductions))),
        retained_qualification=all(map(callable,(prepare_feedback_qualification,prepare_feedback_effect))),
        foreground_handoff=all(map(callable,(register_feedback_next_card_priority,PreparedCurrentGamesWork.apply,
                                            apply_prepared_determined_feedback,prepare_next_card_command))),
        issuance_fence=all(map(callable,(stamp_issued_head,pending_next_card_preparation))))
