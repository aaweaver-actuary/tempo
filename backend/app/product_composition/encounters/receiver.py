"""Retained-history receiving boundary, borrowing the existing caller writer.

The workflow supplies the shared command; this module creates no alternate
delivery ledger, ingestion path, endpoint, policy or canonical introduction.
"""
from app.contracts.product_v02.learning import GameEncounter, EncounterReceipt
from app.services.queue_admission_adapter import current_owner_encounter_introductions
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games.authority import acceptance as source_owner
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    CurrentDecisionUnavailable, current_owner_encounter_feedback,
)


def receive_admitted_encounter(database, *, capture, snapshot, command: GameEncounter,
                               operation_stopped, introduction_policy, clock) -> EncounterReceipt:
    """Use the existing operation stop and foreground gates before any effect.

    The runtime binds its actual stop/control decision; there is no permissive
    default or inferred pause from saved new-card caps or Study-only. Its normal
    background writer must already own database/maintenance admission. Unknown
    control evidence leaves the durable sender pending, without an owner write.
    """
    from app.services.activity_gate import activity_gate
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred

    paused = operation_stopped()
    if type(paused) is not bool:
        raise BackgroundAdmissionDeferred("Encounter operation control is unavailable")
    if paused:
        raise BackgroundAdmissionDeferred("Encounter operation is stopped")
    activity_gate.check_background_admission()
    introductions = current_owner_encounter_introductions(database,
        allowance="encounter_exception", paused=paused)
    return receive_captured_encounter(database, capture=capture, snapshot=snapshot,
        command=command, introductions=introductions,
        introduction_policy=introduction_policy, clock=clock)


def receive_captured_encounter(database, *, capture, snapshot, command: GameEncounter,
                               introductions, introduction_policy, clock) -> EncounterReceipt:
    """Fence retained canonical bytes, then delegate every effect to Study.

    Explicit missing policy/capability stays pending in Study. The admitted
    wrapper selects the approved targeted exception; this borrowed boundary does
    not choose policy. Receipt is provisional until its command transaction commits.
    """
    raw = capture["game"]
    source_owner.lock_imported_source(database, raw["provider"], raw["provider_game_id"])
    current = source_owner.imported_game(database, raw["id"])
    if (current is None or dict(current) != raw or command.source.source_digest != fingerprint(raw)
            or command.key.game_id != raw["id"]):
        raise CurrentDecisionUnavailable("canonical_imported_source_changed")
    receiver = current_owner_encounter_feedback(database, retained_owner_capture=capture,
        snapshot=snapshot, clock=clock, introductions=introductions,
        introduction_policy=introduction_policy)
    return receiver.accept_encounter(command)
