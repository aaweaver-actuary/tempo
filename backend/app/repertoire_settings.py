"""Opening-specific daily limits, shared by both persistence stacks."""
from fastapi import HTTPException

from app.contracts.transport.authored_repertoire import RepertoireSettingsResponse
from tempo_legacy_database.adapters.controls import settings as storage

from .card_deletion import SYSTEM_REPERTOIRE_IDS as SYSTEM_REPERTOIRES


def effective_opening_limits(database) -> dict[str, int]:
    return dict(storage.effective_opening_limit_rows(database))


def repertoire_settings_response(database, repertoire_id: str) -> dict:
    if repertoire_id in SYSTEM_REPERTOIRES:
        raise HTTPException(404, 'Repertoire not found')
    row = storage.repertoire_limit_row(database, repertoire_id)
    if row is None:
        raise HTTPException(404, 'Repertoire not found')
    return RepertoireSettingsResponse(
        repertoire_id=repertoire_id, new_cards_per_day=row[0],
        effective_new_cards_per_day=row[1],
    ).model_dump(mode='json')
