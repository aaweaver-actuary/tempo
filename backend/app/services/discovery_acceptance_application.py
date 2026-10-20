"""Validate a prepared Discovery choice and decide replay/readmission without SQL."""

from datetime import datetime, timezone
import hashlib
from typing import Any

from tempo_chess_core import card_id

from .discovery_acceptance_contracts import (
    DiscoveryAcceptanceStore, DiscoveryChoice, LockedDiscoveryOpportunity,
    NewDiscoveryIntent,
)


class DiscoveryAcceptanceError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def opportunity_is_current(locked: LockedDiscoveryOpportunity) -> bool:
    observed = locked.opportunity.scope
    current = locked.current_scope
    if locked.restricted_to_prefix and current.canonical_scope_preview_id is None:
        return False
    return (
        observed.canonical_prefix_revision == current.canonical_prefix_revision
        and observed.game_scope_generation == current.game_scope_generation
        and (not locked.restricted_to_prefix or (
            observed.canonical_scope_source_revision == current.canonical_scope_source_revision
            and observed.canonical_scope_preview_id == current.canonical_scope_preview_id
        ))
    )


def accept_discovery(
    store: DiscoveryAcceptanceStore, payload: dict[str, Any], *, intent_id: str,
) -> dict[str, str]:
    choice = DiscoveryChoice(payload["opportunity_id"], payload["selected_move_uci"],
                             payload["evidence_fingerprint"])
    prior = store.lock_matching_intent(choice)
    if prior is not None:
        if prior.state != "queued":
            task_state = store.task_state(prior.id)
            if task_state is None or task_state == "failed" or prior.state == "failed":
                store.requeue_intent(prior.id)
        return {"status": "preparing", "intent_id": prior.id}

    locked = store.lock_opportunity_scope(choice.opportunity_id)
    if (locked is None or locked.opportunity.status != "active"
            or not opportunity_is_current(locked)):
        raise DiscoveryAcceptanceError(404, "Active discovery not found")
    opportunity = locked.opportunity
    if opportunity.card_id:
        raise DiscoveryAcceptanceError(409, "This discovery already has a saved decision card")
    if (opportunity.evidence_fingerprint != choice.evidence_fingerprint
            or opportunity.repertoire_id != payload.get("repertoire_id")):
        raise DiscoveryAcceptanceError(409, "Discovery evidence changed; refresh the preview")
    # Different operation IDs can prepare one choice before either persists it.
    # Scope and parent opportunity locks precede the duplicate recheck.
    prior = store.lock_matching_intent(choice)
    if prior is not None:
        return {"status": "preparing", "intent_id": prior.id}
    preview_moves = payload["preview_moves_uci"]
    starting_fen = payload["starting_fen"]
    if (not preview_moves or preview_moves[0] != choice.selected_move_uci
            or payload["recommendation"]["move_uci"] != choice.selected_move_uci):
        raise DiscoveryAcceptanceError(409, "The chosen move is not a validated recommendation")
    line_id = hashlib.sha256(
        f"{opportunity.repertoire_id}\0{card_id(starting_fen, preview_moves)}".encode()
    ).hexdigest()
    store.persist_intent_and_task(NewDiscoveryIntent(
        intent_id, choice, opportunity.repertoire_id, starting_fen, tuple(preview_moves),
        payload["recommendation"], line_id, datetime.now(timezone.utc).isoformat(),
    ))
    return {"status": "preparing", "intent_id": intent_id}
