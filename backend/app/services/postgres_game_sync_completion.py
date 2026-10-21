"""Finish a sync only after every window and game task has a durable receipt."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from ..models import GameSyncRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games.sync import completion as storage


def finish_game_sync_if_complete(database: PostgresConnection, job_id: str) -> bool:
    job = storage.lock_sync_job(database, job_id)
    if job is None or job["status"] not in {"queued", "running"}:
        return False
    unfinished_window = storage.unfinished_window(database, job_id)
    unfinished_record = storage.unfinished_record(database, job_id)
    if unfinished_window or unfinished_record:
        return False
    request = GameSyncRequest.model_validate_json(job["request_json"])
    current_result = json.loads(job["result_json"]) if job["result_json"] else {}
    current_providers = current_result.get("providers", {})
    window_counts = storage.provider_window_counts(database, job_id)
    now = datetime.now(timezone.utc).isoformat()
    providers = {}
    for window_count in window_counts:
        provider = window_count["provider"]
        username = (request.lichess_username if provider == "lichess"
                    else request.chesscom_username).strip()
        saved_counts = current_providers.get(provider, {})
        provider_result = {
            "provider": provider,
            "username": username,
            "status": "idle",
            "fetched": int(window_count["fetched"] or 0),
            "inserted": int(saved_counts.get("inserted", 0)),
            "updated": int(saved_counts.get("updated", 0)),
            "duplicates": int(saved_counts.get("duplicates", 0)),
            "filtered": int(window_count["filtered"] or 0),
            "rejected": int(window_count["rejected"] or 0),
            "failed": 0,
            "error": None,
            "retry_after": None,
        }
        providers[provider] = provider_result
        since = datetime.fromtimestamp(window_count["earliest_ms"] / 1000, timezone.utc)
        storage.save_provider_result(
            database, provider, username, since.isoformat(), now,
            json.dumps(provider_result, separators=(",", ":")),
        )
    result = {
        "imported": sum(item["inserted"] for item in providers.values()),
        "cached": True,
        "incremental": not request.repair,
        "synced_at": now,
        "providers": providers,
    }
    storage.complete_sync_job(
        database, job_id, json.dumps(result, separators=(",", ":")), now,
    )
    if "lichess" in providers:
        from .postgres_next_opponent import request_profile_refresh
        request_profile_refresh(database)
    return True
