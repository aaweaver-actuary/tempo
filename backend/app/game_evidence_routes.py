"""Explicit local API commands and read-only inspection; no direct HTTP writes."""
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException, Path, Query
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.connections import read_connection
from .command_dispatch import dispatch_command

router = APIRouter()


from .product_composition.feedback_commands import ImportedFeedbackSelection


@router.post("/api/game-evidence/product/feedback")
def receive_product_feedback(selection: ImportedFeedbackSelection,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
    _postgres_owner()
    return dispatch_command("game_evidence.product_accept", selection.model_dump(mode="json"),
                            idempotency_key=idempotency_key, background=True)


@router.get("/api/game-evidence/product/effect")
def product_effect_status(provider: str, game_id: str = Query(min_length=1, max_length=200),
    decision_id: str = Query(min_length=1, max_length=200), occurrence_ply: int = Query(ge=1, le=1000)):
    _postgres_owner()
    from .contracts.product_v02.learning import GameEffectKey
    from .services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID
    from .study.game_feedback import GameFeedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    try:
        key = GameEffectKey(workspace_id=LEGACY_WORKSPACE_ID, provider=provider,
            game_id=game_id, decision_id=decision_id, occurrence_ply=occurrence_ply)
    except ValueError as error:
        raise HTTPException(422, "Invalid game effect identity") from error
    with read_connection() as database:
        receipt = GameFeedback(PostgresGameFeedbackStore(database), None,
            clock=lambda: datetime.now(timezone.utc)).effect_status(key)
    if receipt is None:
        raise HTTPException(404, "No retained Study receipt for this occurrence")
    return receipt.model_dump(mode="json")


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
