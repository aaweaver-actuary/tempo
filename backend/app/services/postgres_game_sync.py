"""One short, idempotent PostgreSQL publication of a provider game."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from tempo_legacy_database.adapters.postgres_store import PostgresConnection, connection
from tempo_legacy_database.adapters.games.mutations import restart_derivation
from tempo_legacy_database.adapters.games.sync import records as storage
from .durable_tasks import (
    complete_task_slice_in_transaction, enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .game_record import GameRecord
from .postgres_game_sync_completion import finish_game_sync_if_complete
from .redis_admission_gate import background_lease


def persist_game_record_in_transaction(
    database: PostgresConnection, record: GameRecord,
) -> tuple[str, str]:
    """Publish one normalized game with its follow-up intents atomically."""

    identity = f"{record.provider}:{record.provider_game_id}"
    storage.lock_import_identity(database, identity)
    existing = storage.imported_provider_game(database, record.provider, record.provider_game_id)
    if existing is None:
        existing = storage.imported_legacy_game(
            database, record.provider, record.username, record.content_hash,
        )
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
            storage.GAME_COLUMNS, values,
        ))
        if changed:
            storage.update_imported_game(database, game_id, values)
        outcome = "updated" if changed else "duplicate"
    else:
        game_id = identity
        storage.insert_imported_game(database, game_id, record.provider, values)
        outcome = "inserted"
    storage.ensure_analysis_job(database, game_id, now)
    if outcome != "duplicate":
        derivation = restart_derivation(database, game_id, now)
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_positions", game_id,
            {"game_id": game_id, "derivation_version": derivation["derivation_version"],
             "cursor": 0},
            priority=125,
        )
        storage.invalidate_day_snapshot(database, record.played_at[:10])
        storage.invalidate_day_insights(database, record.played_at[:10])
    return outcome, game_id


def execute_game_sync_record_slice(claimed_task: dict[str, Any]) -> bool:
    """Commit one fetched game only if its durable task lease remains current."""

    record = GameRecord(**claimed_task["payload"]["record"])
    job_id = str(claimed_task["payload"]["job_id"])
    with background_lease():
        with connection(read_only=False, background=True) as database:
            if not lock_current_slice(database, claimed_task):
                return False
            job = storage.lock_sync_progress(database, job_id)
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
            storage.save_sync_progress(
                database, job_id, json.dumps(result, separators=(",", ":")),
                datetime.now(timezone.utc).isoformat(),
            )
            if not complete_task_slice_in_transaction(database, claimed_task):
                raise RuntimeError("Game sync lease changed before publication")
            finish_game_sync_if_complete(database, job_id)
    return True
