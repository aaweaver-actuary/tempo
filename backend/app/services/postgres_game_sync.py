"""One short, idempotent PostgreSQL publication of a provider game."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from ..postgres_store import PostgresConnection
from .game_record import GameRecord


_GAME_COLUMNS = (
    "provider_game_id", "content_hash", "username", "played_at", "speed",
    "rated", "color", "result", "start_fen", "moves_json", "game_url",
    "opening_name", "player_rating", "opponent_rating", "rating_change",
    "time_control",
)


def persist_game_record_in_transaction(
    database: PostgresConnection, record: GameRecord,
) -> tuple[str, str]:
    """Publish one normalized game with its follow-up intents atomically."""

    identity = f"{record.provider}:{record.provider_game_id}"
    database.raw.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:imported-game:{identity}",),
    )
    existing = database.execute(
        "SELECT * FROM imported_games WHERE provider=? AND provider_game_id=? FOR UPDATE",
        (record.provider, record.provider_game_id),
    ).fetchone()
    if existing is None:
        existing = database.execute(
            """SELECT * FROM imported_games WHERE provider=?
               AND lower(username)=lower(?) AND content_hash=?
               AND provider_game_id IS NULL FOR UPDATE""",
            (record.provider, record.username, record.content_hash),
        ).fetchone()
    values = (
        record.provider_game_id, record.content_hash, record.username,
        record.played_at, record.speed, int(record.rated), record.color,
        record.result, record.start_fen, json.dumps(record.uci_moves),
        record.game_url, record.opening_name, record.player_rating,
        record.opponent_rating, record.rating_change, record.time_control,
    )
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        game_id = existing["id"]
        changed = any(existing[column_name] != value for column_name, value in zip(
            _GAME_COLUMNS, values,
        ))
        if changed:
            database.execute(
                "UPDATE imported_games SET "
                + ",".join(f"{column_name}=?" for column_name in _GAME_COLUMNS)
                + " WHERE id=?",
                (*values, game_id),
            )
        outcome = "updated" if changed else "duplicate"
    else:
        game_id = identity
        database.execute(
            "INSERT INTO imported_games(id,provider," + ",".join(_GAME_COLUMNS)
            + ") VALUES(" + ",".join("?" for _ in range(len(_GAME_COLUMNS) + 2)) + ")",
            (game_id, record.provider, *values),
        )
        outcome = "inserted"
    database.execute(
        "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?) "
        "ON CONFLICT(game_id) DO NOTHING",
        (game_id, now),
    )
    if outcome != "duplicate":
        database.execute(
            """INSERT INTO game_derivation_jobs(game_id,status,updated_at)
               VALUES(?,'queued',?) ON CONFLICT(game_id) DO UPDATE SET
               status='queued',last_error=NULL,
               derivation_version=game_derivation_jobs.derivation_version+1,
               completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
            (game_id, now),
        )
        database.execute(
            "DELETE FROM daily_chess_snapshots WHERE local_day=?",
            (record.played_at[:10],),
        )
        database.execute(
            "DELETE FROM daily_chess_insights WHERE local_day=?",
            (record.played_at[:10],),
        )
    return outcome, game_id
