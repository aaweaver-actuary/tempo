"""Foreground admission of one validated discovery continuation."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .database import read_connection
from .postgres_store import PostgresConnection
from .services.discovery_acceptance_application import (
    DiscoveryAcceptanceError, accept_discovery as apply_discovery_acceptance,
)
from .services.discovery_acceptance_contracts import DiscoveryChoice
from .services.discovery_acceptance_store import PostgresDiscoveryAcceptanceStore
from .services.discovery_admission import (
    admission_intent_id, recommend_missing_continuations,
)


def prepare_discovery_acceptance(
    opportunity_id: str, selected_move_uci: str, evidence_fingerprint: str,
) -> dict[str, Any]:
    """Do the potentially costly recommendation work before Celery opens a write."""
    with read_connection() as database:
        exists = PostgresDiscoveryAcceptanceStore(database).has_matching_intent(
            DiscoveryChoice(opportunity_id, selected_move_uci, evidence_fingerprint),
        )
    if exists:
        return {"opportunity_id": opportunity_id, "selected_move_uci": selected_move_uci,
                "evidence_fingerprint": evidence_fingerprint}
    recommendation = recommend_missing_continuations(opportunity_id)
    if (recommendation["state"] != "ready"
            or recommendation["evidence_fingerprint"] != evidence_fingerprint):
        raise ValueError("Discovery evidence changed; refresh the preview")
    selected = next((candidate for candidate in recommendation["candidates"]
                     if candidate["move_uci"] == selected_move_uci), None)
    if selected is None:
        raise ValueError("The chosen move is not a validated recommendation")
    return {"opportunity_id": opportunity_id, "selected_move_uci": selected_move_uci,
            "evidence_fingerprint": evidence_fingerprint,
            "repertoire_id": recommendation["repertoire_id"],
            "starting_fen": recommendation["starting_fen"],
            "preview_moves_uci": selected["preview_moves_uci"],
            "recommendation": selected}


def accept_discovery(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    try:
        return apply_discovery_acceptance(
            PostgresDiscoveryAcceptanceStore(database), payload,
            intent_id=admission_intent_id(payload["opportunity_id"], payload["selected_move_uci"],
                                          payload["evidence_fingerprint"]),
        )
    except DiscoveryAcceptanceError as error:
        raise HTTPException(error.status_code, error.detail) from error


register_command("discovery.accept", accept_discovery)
