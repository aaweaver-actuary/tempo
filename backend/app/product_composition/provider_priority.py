"""Optional-worker composition of the public, durable Study priority intake.

Capture closes before the borrowed writer opens. Receiving acceptance records
the owner's obligation only; independent Study refresh owns queue availability.
"""
from datetime import datetime

from app.repertoire import project_capture
from .encounters import receive_admitted_encounter
from .game_review import CurrentStudyFeedback, CurrentStudyReceiptReads
from .retained_game import retained_canonical_game


class CurrentProviderPriorityIntake:
    def __init__(self, *, connections, clock, operation_stopped, capture=None):
        if not callable(operation_stopped):
            raise ValueError("explicit_provider_operation_control_required")
        if capture is None:
            from app.legacy_game_observation import capture_game
            capture = capture_game
        self.connections, self.clock = connections, clock
        self.operation_stopped, self.capture = operation_stopped, capture
        self.reads = CurrentStudyReceiptReads(connections, clock)
        # Controls share the existing owner's ledger; they never promote a queue.
        self.controls = CurrentStudyFeedback(transactions=connections.writer,
            capture_for=None, fence_game=None, clock=clock)

    def accept_encounter(self, command):
        from .game_sync_current import _require_current_sync_controls
        _require_current_sync_controls()
        captured = self.capture(command.decision.repertoire_id, command.key.game_id,
                                (command.key.occurrence_ply - 1,))
        snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
        with self.connections.writer() as database:
            receipt = receive_admitted_encounter(database, capture=captured, snapshot=snapshot,
                command=command, operation_stopped=self.operation_stopped,
                clock=lambda: datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00")))
        return receipt  # The delivery acknowledgment follows this commit.

    def control_effect(self, command):
        return self.controls.control_effect(command)

    def encounter_status(self, key):
        return self.reads.encounter_status(key)

    def effect_status(self, key):
        return self.reads.effect_status(key)

    def accept_lapse(self, command):
        raise ValueError("encounter_worker_does_not_create_legacy_lapses")
