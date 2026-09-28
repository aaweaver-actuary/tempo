"""Foreground game mutations committed with their derived-work intents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_task_in_transaction


def set_game_exclusion(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    game_id = str(payload["game_id"])
    excluded = bool(payload["excluded"])
    game = database.execute(
        "SELECT id FROM imported_games WHERE id=? FOR UPDATE", (game_id,),
    ).fetchone()
    if game is None:
        raise HTTPException(404, "Game not found")
    database.execute(
        "UPDATE imported_games SET adaptive_excluded=? WHERE id=?",
        (int(excluded), game_id),
    )
    now = datetime.now(timezone.utc).isoformat()
    if excluded:
        database.execute(
            "UPDATE game_findings SET status='excluded',updated_at=? "
            "WHERE game_id=? AND status='pending'", (now, game_id),
        )
    else:
        database.execute(
            "UPDATE game_findings SET status='pending',updated_at=? "
            "WHERE game_id=? AND status='excluded'", (now, game_id),
        )
    database.execute(
        """INSERT INTO game_derivation_jobs(game_id,status,updated_at)
           VALUES(?,'queued',?) ON CONFLICT(game_id) DO UPDATE SET
           status='queued',last_error=NULL,
           derivation_version=game_derivation_jobs.derivation_version+1,
           completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
        (game_id, now),
    )
    derivation = database.execute(
        "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()
    enqueue_task_in_transaction(
        database, "game_derivation_positions", game_id,
        {"game_id": game_id, "derivation_version": derivation["derivation_version"],
         "cursor": 0},
        priority=125,
    )
    affected_repertoire_ids = [row[0] for row in database.execute(
        "SELECT repertoire_id FROM game_repertoire_matches WHERE game_id=?",
        (game_id,),
    )]
    for repertoire_id in sorted(set(affected_repertoire_ids)):
        enqueue_task_in_transaction(
            database, "repertoire_opportunity", repertoire_id,
            {"repertoire_id": repertoire_id, "phase": "summaries", "cursor": ""},
            priority=130,
        )
    return {"game_id": game_id, "excluded": excluded}


def request_defensive_threat_scan(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, Any]:
    game_id = str(payload["game_id"])
    game = database.execute(
        "SELECT analysis_version FROM imported_games WHERE id=? AND analysis_state='ready'",
        (game_id,),
    ).fetchone()
    if game is None:
        raise HTTPException(404, "Analyzed game not found")
    analysis_version = int(game["analysis_version"])
    enqueue_task_in_transaction(
        database, "defensive_threat_scan", game_id,
        {"game_id": game_id, "analysis_version": analysis_version, "cursor": 0},
        priority=145,
    )
    return {"status": "queued", "analysis_version": analysis_version}


register_command("games.exclusion.set", set_game_exclusion)
register_command("games.defensive_threats.refresh", request_defensive_threat_scan)
