"""Optional-worker composition of the public, durable Study priority intake.

Capture closes before the borrowed writer opens. Receiving acceptance records
the owner's obligation only; independent Study refresh owns queue availability.
"""
from datetime import datetime
import logging
import re

from app.repertoire import project_capture
from .encounters import receive_admitted_encounter
from .game_review import CurrentStudyFeedback, CurrentStudyReceiptReads
from .retained_game import retained_canonical_game

_LOGGER = logging.getLogger("tempo.games.priority_intake")
_CAPTURE_REASONS = frozenset({
    "legacy_game_already_accepted_use_retained_preview",
    "one_explicit_existing_game_required",
    "explicit_decision_scope_exceeds_bound_or_is_invalid",
    "legacy_game_changed_or_accepted_during_export",
    "capture_time_requires_timezone",
    "legacy_completed_game_required",
    "legacy_game_time_unknown_or_changed",
    "canonical_imported_source_changed",
    "retained_owner_capture_required",
    "retained_owner_capture_changed",
    "retained_owner_projection_mismatch",
    "retained_owner_scope_mismatch",
    "retained_owner_scope_exceeds_bound",
    "borrowed_owner_writer_transaction_required",
})


def _failure_code(error):
    # Exception messages/tracebacks may contain SQL parameters or private PGN.
    # Only these existing fixed capture reasons and a five-character SQLSTATE
    # are safe to include. The original error still propagates unchanged.
    reason = getattr(error, "reason", None)
    if not isinstance(reason, str) or reason not in _CAPTURE_REASONS:
        reason = str(error) if type(error) is ValueError and str(error) in _CAPTURE_REASONS else None
    sqlstate = getattr(error, "sqlstate", None)
    return reason or (sqlstate if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else "unclassified")


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
        stage = "controls"
        try:
            _require_current_sync_controls()
            stage = "capture"
            captured = self.capture(command.decision.repertoire_id, command.key.game_id,
                                    (command.key.occurrence_ply - 1,))
            stage = "projection"
            snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
            stage = "writer"
            with self.connections.writer() as database:
                stage = "intake"
                receipt = receive_admitted_encounter(database, capture=captured, snapshot=snapshot,
                    command=command, operation_stopped=self.operation_stopped,
                    clock=lambda: datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00")))
                stage = "commit"
        except Exception as error:
            _LOGGER.warning("Games priority intake deferred stage=%s error=%s code=%s",
                            stage, type(error).__name__, _failure_code(error))
            raise
        return receipt  # The delivery acknowledgment follows this commit.

    def control_effect(self, command):
        return self.controls.control_effect(command)

    def encounter_status(self, key):
        return self.reads.encounter_status(key)

    def effect_status(self, key):
        return self.reads.effect_status(key)

    def accept_lapse(self, command):
        raise ValueError("encounter_worker_does_not_create_legacy_lapses")
