"""Leased game-analysis claims executed by the background Celery worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from .command_gateway import register_command
from .postgres_store import PostgresConnection


def claim_game_analysis(database: PostgresConnection, _payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    job = database.execute_native(
        "SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,"
        "g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,"
        "c.divergence_ply "
        "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "LEFT JOIN repertoire_comparisons c ON c.game_id=g.id "
        "LEFT JOIN background_activity control ON control.source='game_analysis' "
        "AND control.work_id=j.game_id "
        "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
        "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
        "AND COALESCE(control.paused,0)=0 "
        "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id "
        "LIMIT 1 FOR UPDATE OF j SKIP LOCKED",
        (now_text,),
    ).fetchone()
    if job is None:
        return {"job": None}
    lease_id = str(uuid.uuid4())
    expires_at = (now + timedelta(minutes=5)).isoformat()
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='leased',lease_id=%s,lease_expires_at=%s,"
        "attempts=attempts+1,updated_at=%s WHERE game_id=%s",
        (lease_id, expires_at, now_text, job["game_id"]),
    )
    database.execute_native(
        "UPDATE imported_games SET analysis_state='analyzing' WHERE id=%s",
        (job["game_id"],),
    )
    return {"job": {**dict(job), "moves": json.loads(job["moves_json"]),
                    "lease_id": lease_id, "lease_expires_at": expires_at}}


register_command("games.analysis.claim", claim_game_analysis)
