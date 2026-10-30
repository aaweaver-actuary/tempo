"""Qualify original outbox commands using the public first-boundary selector.

No storage, provider or Study implementation is imported. Composition supplies
complete current owner snapshots; a missing scope remains a hold.
"""
from app.contracts.product_v02.learning import FirstDepartureWitness, GameEncounter
from app.contracts.product_v02.repertoire import IntroductionGameScope


def held_delivery(delivery, reason, *, retry_at, retryable=True):
    return delivery.model_copy(update={"qualification_status": "held",
        "qualification_reason": reason, "qualification_retryable": retryable,
        "error": reason, "next_attempt_at": retry_at})


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
