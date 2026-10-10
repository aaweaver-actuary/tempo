"""Finish a sync only after every window and game task has a durable receipt."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from ..models import GameSyncRequest
from ..postgres_store import PostgresConnection


def finish_game_sync_if_complete(database: PostgresConnection, job_id: str) -> bool:
    job = database.execute(
        "SELECT status,request_json,result_json FROM game_sync_jobs WHERE id=? FOR UPDATE",
        (job_id,),
    ).fetchone()
    if job is None or job["status"] not in {"queued", "running"}:
        return False
    unfinished_window = database.execute(
        """SELECT 1 FROM game_sync_windows WHERE job_id=?
           AND status NOT IN ('complete','split') LIMIT 1""",
        (job_id,),
    ).fetchone()
    unfinished_record = database.execute(
        """SELECT 1 FROM background_tasks WHERE kind='game_sync_record'
           AND deduplication_key LIKE ? AND state!='complete' LIMIT 1""",
        (f"{job_id}:%",),
    ).fetchone()
    if unfinished_window or unfinished_record:
        return False
    request = GameSyncRequest.model_validate_json(job["request_json"])
    current_result = json.loads(job["result_json"]) if job["result_json"] else {}
    current_providers = current_result.get("providers", {})
    window_counts = database.execute(
        """SELECT provider,SUM(fetched_count) fetched,SUM(filtered_count) filtered,
                  SUM(rejected_count) rejected,MIN(window_start_ms) earliest_ms
           FROM game_sync_windows WHERE job_id=? GROUP BY provider""",
        (job_id,),
    ).fetchall()
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
        database.execute(
            """INSERT INTO game_sync_state(provider,username,status,cursor,last_success_at,
                   last_error,retry_after,last_result_json)
               VALUES(?,?,'idle',?,?,NULL,NULL,?)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,
                   status='idle',cursor=excluded.cursor,
                   last_success_at=excluded.last_success_at,last_error=NULL,
                   retry_after=NULL,last_result_json=excluded.last_result_json""",
            (provider, username, since.isoformat(), now,
             json.dumps(provider_result, separators=(",", ":"))),
        )
    result = {
        "imported": sum(item["inserted"] for item in providers.values()),
        "cached": True,
        "incremental": not request.repair,
        "synced_at": now,
        "providers": providers,
    }
    database.execute(
        """UPDATE game_sync_jobs SET status='complete',result_json=?,error=NULL,
           completed_at=?,updated_at=? WHERE id=?""",
        (json.dumps(result, separators=(",", ":")), now, now, job_id),
    )
    if "lichess" in providers:
        from .postgres_next_opponent import request_profile_refresh
        request_profile_refresh(database)
    return True
