"""Materialize one day's statistics outside the leased database section."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from ..database import background_read_connection, connection
from .durable_tasks import complete_task_slice_in_transaction, lock_current_slice


def _prepare_daily_statistics(local_day: str) -> dict[str, Any]:
    next_day = (datetime.fromisoformat(local_day).date() + timedelta(days=1)).isoformat()
    with background_read_connection() as database:
        providers = [row["provider"] for row in database.execute(
            "SELECT provider FROM game_accounts WHERE trim(username)!=''",
        )]
        missing_watermarks = []
        for provider in providers:
            state = database.execute(
                "SELECT last_success_at FROM game_sync_state WHERE provider=?", (provider,),
            ).fetchone()
            if not state or not state["last_success_at"] or state["last_success_at"][:10] < next_day:
                missing_watermarks.append(provider)
        eligible_games = database.execute(
            """SELECT g.id,g.analysis_state,g.adaptive_excluded,f.game_id AS feature_game_id
                 FROM imported_games g LEFT JOIN game_feature_rows f ON f.game_id=g.id
                WHERE substr(g.played_at,1,10)=?""",
            (local_day,),
        ).fetchall()
        blocked_games = [
            row["id"] for row in eligible_games
            if not row["adaptive_excluded"] and row["analysis_state"] != "failed"
            and row["feature_game_id"] is None
        ]
        if missing_watermarks or blocked_games:
            return {
                "local_day": local_day,
                "status": "waiting-sync" if missing_watermarks else "waiting-analysis",
                "missing_provider_watermarks": missing_watermarks,
                "blocked_game_ids": blocked_games,
            }
        features = database.execute(
            "SELECT outcome_score,tactical_opportunities,tactical_found "
            "FROM game_feature_rows WHERE local_day=?", (local_day,),
        ).fetchall()
    games = len(features)
    opportunities = sum(row["tactical_opportunities"] for row in features)
    return {
        "local_day": local_day, "status": "complete", "games": games,
        "score": sum(row["outcome_score"] for row in features) / games if games else None,
        "tactical_found": sum(row["tactical_found"] for row in features),
        "tactical_opportunities": opportunities,
    }


def execute_postgres_daily_statistics_slice(task: dict[str, Any]) -> bool:
    local_day = str(task["payload"]["local_day"])
    snapshot = _prepare_daily_statistics(local_day)
    now = datetime.now(timezone.utc).isoformat()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        database.execute(
            """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
               VALUES(?,1,?,?,?) ON CONFLICT(local_day) DO UPDATE SET
               metrics_json=excluded.metrics_json,status=excluded.status,
               updated_at=excluded.updated_at""",
            (local_day, json.dumps(snapshot), snapshot["status"], now),
        )
        if snapshot["status"] == "complete":
            insight_kinds = []
            if snapshot["games"] >= 20:
                insight_kinds.append("outcome trend")
            missed = snapshot["tactical_opportunities"] - snapshot["tactical_found"]
            if snapshot["tactical_opportunities"] >= 10 and missed >= 3:
                insight_kinds.append("tactical focus")
            for kind in insight_kinds:
                identifier = hashlib.sha256(
                    f"{local_day}\0{kind.replace(' ', '-')}\01".encode()
                ).hexdigest()
                database.execute(
                    """INSERT OR IGNORE INTO daily_chess_insights(
                         id,local_day,kind,evidence_json,status,created_at,updated_at)
                       VALUES(?,?,?,?,'pending',?,?)""",
                    (identifier, local_day, kind, json.dumps(snapshot), now, now),
                )
        database.execute(
            "UPDATE daily_statistics_jobs SET status='complete',last_error=NULL,updated_at=? "
            "WHERE local_day=?", (now, local_day),
        )
        return complete_task_slice_in_transaction(database, task)
