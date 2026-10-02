"""Opening-specific daily limits, shared by both persistence stacks."""
from fastapi import HTTPException

from .models import RepertoireSettingsResponse

SYSTEM_REPERTOIRES = ('__tactics__', '__endgames__', '__game_mistakes__', '__game_tactics__', '__captured_tactics__')


def effective_opening_limits(database) -> dict[str, int]:
    return dict(database.execute(
        'SELECT r.id,COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
        'FROM repertoires r CROSS JOIN settings s WHERE s.id=1'
    ).fetchall())


def repertoire_settings_response(database, repertoire_id: str) -> dict:
    if repertoire_id in SYSTEM_REPERTOIRES:
        raise HTTPException(404, 'Repertoire not found')
    row = database.execute(
        'SELECT r.new_cards_per_day,COALESCE(r.new_cards_per_day,s.new_cards_per_day) '
        'FROM repertoires r CROSS JOIN settings s WHERE r.id=? AND s.id=1',
        (repertoire_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, 'Repertoire not found')
    return RepertoireSettingsResponse(
        repertoire_id=repertoire_id, new_cards_per_day=row[0],
        effective_new_cards_per_day=row[1],
    ).model_dump(mode='json')
