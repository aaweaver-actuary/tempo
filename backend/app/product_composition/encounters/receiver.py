"""Retained-history receiving boundary, borrowing the existing caller writer.

The workflow supplies the shared command; this module creates no alternate
delivery ledger, ingestion path, endpoint, policy or canonical introduction.
"""
from app.contracts.product_v02.learning import GameEncounter, EncounterReceipt
from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games.authority import acceptance as source_owner
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    CurrentDecisionUnavailable, current_owner_encounter_feedback,
)


def receive_captured_encounter(database, *, capture, snapshot, command: GameEncounter,
                               introductions, introduction_policy, clock) -> EncounterReceipt:
    """Fence retained canonical bytes, then delegate every effect to Study.

    Explicit missing policy/capability stays pending in Study. Composition never
    chooses a live cap, reserve or pause exception. Receipt is provisional until
    the workflow's borrowed command transaction commits.
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
