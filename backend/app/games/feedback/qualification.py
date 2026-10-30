"""Qualify original outbox commands using the public first-boundary selector.

No storage, provider or Study implementation is imported. Composition supplies
complete current owner snapshots; a missing scope remains a hold.
"""
from app.contracts.product_v02.learning import (EncounterReceipt, FirstDepartureWitness,
    GameEncounter, LearningReceipt)
from app.contracts.product_v02.repertoire import IntroductionGameScope


def held_delivery(delivery, reason, *, retry_at, retryable=True):
    return delivery.model_copy(update={"qualification_status": "held",
        "qualification_reason": reason, "qualification_retryable": retryable,
        "error": reason, "next_attempt_at": retry_at})


def selection_supports_original(selected,command):
    """Check frozen occurrence facts for reanalysis without minting a witness.

    Physical presentation/revision can be resolved by Study; changed authored
    response, position, source or play is a different factual boundary.
    """
    if isinstance(command,GameEncounter):
        if command.observation != "missed":
            return False
        source,decision,expected,played = command.source,command.decision,(command.expected_uci,),command.played_uci
    else:
        decision = command.finding
        source = IntroductionGameScope(provider=decision.provider,game_id=decision.game_id,
            source_revision=decision.source_revision,source_digest=decision.source_digest)
        expected,played = decision.expected_uci,decision.played_uci
    key = command.key
    return bool(selected.status == "own_actionable" and selected.decision is not None
        and selected.source == source and selected.workspace_id == key.workspace_id
        and selected.ply == key.occurrence_ply and selected.decision.decision_id == key.decision_id == decision.decision_id
        and selected.position_key == decision.position_key and selected.expected_uci == expected
        and selected.played_uci == played)


def acknowledge_qualified_delivery(delivery, receipt):
    """Validate the real owner receipt without changing the frozen command."""
    command, qualification = delivery.command, delivery.qualification
    source = command.source if isinstance(command, GameEncounter) else command.finding
    if (not isinstance(receipt, EncounterReceipt) or receipt.key != command.key
            or receipt.source_revision != source.source_revision or not receipt.owner_receipt_id
            or receipt.source_digest != source.source_digest or receipt.observation != "missed"):
        raise ValueError("receiver_qualified_receipt_provenance_mismatch")
    historical_final = receipt.interface_version == "0.2.1" and receipt.progress is None and (
        receipt.status == "settled" or (receipt.status == "held"
            and receipt.reason in {"source_hold","source_withdraw"}
            and receipt.outcome in {"historical_preserved","withdrawn","duplicate"}))
    if not historical_final and (qualification is None or receipt.interface_version != "0.2.2" or receipt.progress is None
            or receipt.progress.determined_at != qualification.determined_at
            or receipt.progress.stream != qualification.stream):
        raise ValueError("receiver_first_departure_policy_receipt_required")
    state = "failed" if receipt.outcome in {"conflict", "stale_revision"} else "acknowledged"
    return delivery.model_copy(update={"state": state, "receipt": receipt,
        "attempts": delivery.attempts + 1, "error": receipt.reason if state == "failed" else None})


def acknowledge_control_delivery(delivery, receipt):
    command = delivery.control
    if (command is None or not isinstance(receipt,LearningReceipt) or receipt.key != command.key
            or receipt.source_revision != command.source_revision or not receipt.owner_receipt_id):
        raise ValueError("receiver_control_receipt_identity_mismatch")
    state = "failed" if receipt.outcome in {"conflict","stale_revision"} else "acknowledged"
    return delivery.model_copy(update={"state":state,"receipt":receipt,
        "attempts":delivery.attempts+1,"error":receipt.reason if state == "failed" else None})


def qualify_delivery(delivery, *, source, configuration, snapshots, unavailable=(),
                     determined_at, retry_at, select_first_boundary):
    """One explicit disposition; never rewrite command, counters or occurrence.

    A retained command whose original acquisition stream was not stored is
    qualified by the reconciliation class. This does not pretend it came from
    the current forward stream or use an arbitrary game-age cutoff.
    """
    if delivery.control is not None:
        return delivery
    command = delivery.command
    if source is None or source.normalized is None or source.quarantined:
        return held_delivery(delivery, "first_departure_source_unavailable", retry_at=retry_at)
    if configuration is None:
        return held_delivery(delivery, "first_departure_configuration_unavailable", retry_at=retry_at)
    game = source.normalized
    if (configuration.workspace_id, configuration.provider) != (command.key.workspace_id, command.key.provider):
        return held_delivery(delivery, "first_departure_configuration_mismatch", retry_at=retry_at, retryable=False)
    original = (command.source if isinstance(command, GameEncounter) else IntroductionGameScope(
        provider=command.finding.provider, game_id=command.finding.game_id,
        source_revision=command.finding.source_revision, source_digest=command.finding.source_digest))
    current_source = IntroductionGameScope(provider=configuration.provider, game_id=game.game_id,
        source_revision=game.source_revision, source_digest=game.source_digest)
    if current_source != original:
        return held_delivery(delivery, "first_departure_source_changed", retry_at=retry_at, retryable=False)
    selection = select_first_boundary(game, snapshots=tuple(snapshots), source=current_source,
        selected_repertoire_ids=configuration.repertoire_ids, unavailable_repertoire_ids=tuple(unavailable))
    if selection.status in {"unresolved", "own_unmapped"}:
        return held_delivery(delivery, selection.diagnostic or "first_departure_target_unavailable", retry_at=retry_at)
    if selection.status != "own_actionable":
        return delivery.model_copy(update={"state": "failed", "qualification_status": "ineligible",
            "qualification_reason": "first_departure_" + selection.status,
            "error": "first_departure_" + selection.status, "next_attempt_at": 0})
    witness = FirstDepartureWitness(selection=selection,
        configuration_revision=configuration.revision,
        determined_at=delivery.determined_at or determined_at,
        stream=delivery.stream or "reconciliation")
    if not witness.matches(command):
        return delivery.model_copy(update={"state": "failed", "qualification_status": "ineligible",
            "qualification_reason": "not_selected_first_departure",
            "error": "not_selected_first_departure", "next_attempt_at": 0})
    if command.occurred_at is None or game.played_at is None:
        return held_delivery(delivery, "first_departure_source_time_unavailable", retry_at=retry_at)
    return delivery.model_copy(update={"qualification_status": "ready", "qualification_reason": None,
        "qualification_retryable": True,
        "qualification": witness, "determined_at": witness.determined_at,
        "stream": witness.stream, "error": None, "next_attempt_at": 0})
