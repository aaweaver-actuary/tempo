"""Explicit local API commands and read-only inspection; no direct HTTP writes."""
from fastapi import APIRouter, Header, HTTPException, Path
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

from . import postgres_store
from .database import read_connection
from .command_dispatch import dispatch_command

router = APIRouter()


def _postgres_owner():
    if not postgres_store.configured():
        raise HTTPException(404, "Legacy evidence requires the PostgreSQL owner")


@router.post("/api/game-evidence/legacy/deliveries")
def receive_legacy_evidence(delivery: EvidenceDelivery,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
    _postgres_owner()
    return dispatch_command("game_evidence.receive_legacy", {"delivery": delivery.model_dump(mode="json")},
                            idempotency_key=idempotency_key, background=True)


@router.post("/api/game-evidence/legacy/cards/{card_id}/queue")
def queue_legacy_evidence_card(card_id: str = Path(min_length=1, max_length=128),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
    _postgres_owner()
    return dispatch_command("game_evidence.admit_legacy", {"card_id": card_id}, idempotency_key=idempotency_key)


@router.get("/api/game-evidence/legacy/cards/{card_id}")
def legacy_card_status(card_id: str = Path(min_length=1, max_length=128)):
    _postgres_owner()
    from .services.legacy_study_remediation import card_status
    with read_connection() as database:
        return card_status(database, card_id)
