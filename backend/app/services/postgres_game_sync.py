"""One short, idempotent PostgreSQL publication of a provider game."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from ..postgres_store import PostgresConnection, connection
from .durable_tasks import (
    complete_task_slice_in_transaction, enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .game_record import GameRecord
from .postgres_game_sync_completion import finish_game_sync_if_complete
from .redis_admission_gate import background_lease


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
        derivation = database.execute(
            "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?",
            (game_id,),
        ).fetchone()
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_positions", game_id,
            {"game_id": game_id, "derivation_version": derivation["derivation_version"],
             "cursor": 0},
            priority=125,
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


def execute_game_sync_record_slice(claimed_task: dict[str, Any]) -> bool:
    """Commit one fetched game only if its durable task lease remains current."""

    record = GameRecord(**claimed_task["payload"]["record"])
    job_id = str(claimed_task["payload"]["job_id"])
    with background_lease():
        with connection(read_only=False, background=True) as database:
            if not lock_current_slice(database, claimed_task):
                return False
            job = database.execute(
                "SELECT status,result_json FROM game_sync_jobs WHERE id=? FOR UPDATE",
                (job_id,),
            ).fetchone()
            if job is None or job["status"] not in {"queued", "running"}:
                complete_task_slice_in_transaction(database, claimed_task)
                return False
            outcome, _ = persist_game_record_in_transaction(database, record)
            result = json.loads(job["result_json"]) if job["result_json"] else {}
            counts = result.setdefault("providers", {}).setdefault(record.provider, {
                "inserted": 0, "updated": 0, "duplicates": 0,
            })
            count_name = "duplicates" if outcome == "duplicate" else outcome
            counts[count_name] = int(counts.get(count_name, 0)) + 1
            result["imported"] = sum(
                int(provider_counts.get("inserted", 0))
                for provider_counts in result["providers"].values()
            )
            database.execute(
                "UPDATE game_sync_jobs SET status='running',result_json=?,updated_at=? WHERE id=?",
                (json.dumps(result, separators=(",", ":")),
                 datetime.now(timezone.utc).isoformat(), job_id),
            )
            if not complete_task_slice_in_transaction(database, claimed_task):
                raise RuntimeError("Game sync lease changed before publication")
            finish_game_sync_if_complete(database, job_id)
    return True
