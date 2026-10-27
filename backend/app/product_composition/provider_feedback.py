"""Provider outbox binding to the qualified retained encounter receiver.

All capture/projection/import work finishes before the borrowed writer opens.
Games authenticates its committed command; Study alone mutates learning state.
"""
from datetime import datetime

from app.product_composition.encounters import receive_admitted_encounter
from app.product_composition.retained_game import retained_canonical_game
from app.repertoire import project_capture
from app.study.encounter_feedback import EncounterFeedback,EncounterIntroductionPolicy
from tempo_legacy_database.adapters.games.product_review import fence_imported_effect
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback


class _NoCurrentDecision:
    def current(self, command):
        return ()

    def current_encounter(self, command):
        return ()


def capture_provider_encounter(command):
    from app.legacy_game_observation import capture_game
    from app.contracts.product_v02.learning import GameEncounter
    repertoire = (command.decision.repertoire_id if isinstance(command,GameEncounter)
                  else command.finding.repertoire_id)
    return capture_game(repertoire,command.key.game_id,
                        (command.key.occurrence_ply-1,))


def project_provider_capture(capture):
    return project_capture(capture,comparison_game=retained_canonical_game(capture))


class CurrentProviderFeedback:
    def __init__(self, *, read, write, operation_stopped, clock,
                 capture=capture_provider_encounter,project=project_provider_capture,
                 fence=fence_imported_effect,receive=receive_admitted_encounter):
        self.read,self.write=read,write
        self.operation_stopped,self.clock=operation_stopped,clock
        self.capture,self.project,self.fence,self.receive=capture,project,fence,receive
        self.policy=EncounterIntroductionPolicy("encounter_exception")

    def _now(self):
        return datetime.fromisoformat(self.clock.utc_now().replace("Z","+00:00"))

    def _require_admitted(self):
        from app.services.redis_admission_gate import BackgroundAdmissionDeferred
        if self.operation_stopped() is not False:
            raise BackgroundAdmissionDeferred("Provider feedback operation is stopped or unavailable")

    def accept_encounter(self,command):
        self._require_admitted()
        retained=self.capture(command)
        snapshot=self.project(retained)
        self._require_admitted()
        with self.write() as database:
            self.fence(database,command,retained)
            receipt=self.receive(database,capture=retained,snapshot=snapshot,command=command,
                operation_stopped=self.operation_stopped,introduction_policy=self.policy,clock=self._now)
        return receipt

    def accept_lapse(self,command):
        """Keep an already-proved ordinary lapse on its existing owner path."""
        self._require_admitted()
        retained=self.capture(command)
        snapshot=self.project(retained)
        self._require_admitted()
        with self.write() as database:
            self.fence(database,command,retained)
            self._require_admitted()
            from app.services.activity_gate import activity_gate
            activity_gate.check_background_admission()
            owner=current_owner_game_feedback(database,retained_owner_capture=retained,
                snapshot=snapshot,clock=self._now)
            receipt=owner.accept_lapse(command)
        return receipt

    def _status_owner(self,database):
        return EncounterFeedback(PostgresGameFeedbackStore(database),_NoCurrentDecision(),
            clock=self._now,introductions=None,introduction_policy=None)

    def encounter_status(self,key):
        with self.read() as database:
            return self._status_owner(database).encounter_status(key)

    def effect_status(self,key):
        with self.read() as database:
            owner=self._status_owner(database)
            return owner.encounter_status(key) or owner.effect_status(key)

    def control_effect(self,command):
        self._require_admitted()
        with self.write() as database:
            return self._status_owner(database).control_effect(command)
