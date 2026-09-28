"""Durable one-game slices after the repertoire graph changes."""

from __future__ import annotations

from datetime import datetime, timezone

from ..database import connection
from .. import postgres_store
from .activity_gate import activity_gate
from .durable_tasks import (
    enqueue_compact_postgres_task_in_transaction,
    enqueue_task,
    enqueue_task_in_transaction,
    lock_current_slice,
)


def enqueue_repertoire_game_refresh(*, background: bool) -> None:
    enqueue_task("repertoire_game_refresh", "all", {"after_game_id": ""},
                 priority=90, foreground=not background)


def execute_repertoire_game_refresh_slice(task: dict) -> bool:
    """Queue one derivation, then persist a cursor for the following slice."""
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return False
        else:
            current = database.execute(
                "SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
                (task["id"], task["generation"], task["lease_token"]),
            ).fetchone()
            if not current:
                return False
        game = database.execute(
            "SELECT id FROM imported_games WHERE id>? ORDER BY id LIMIT 1",
            (task["payload"].get("after_game_id", ""),),
        ).fetchone()
        if not game:
            return False
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES(?,'queued',?)
               ON CONFLICT(game_id) DO UPDATE SET status='queued',last_error=NULL,
                 derivation_version=game_derivation_jobs.derivation_version+1,
                 completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
            (game["id"], now),
        )
        if postgres_store.configured():
            derivation_job = database.execute(
                "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?",
                (game["id"],),
            ).fetchone()
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_positions", game["id"],
                {"game_id": game["id"],
                 "derivation_version": derivation_job["derivation_version"],
                 "cursor": 0}, priority=125,
            )
            enqueue_compact_postgres_task_in_transaction(
                database, "repertoire_game_refresh", "all",
                {"after_game_id": game["id"]}, priority=90,
            )
        else:
            enqueue_task_in_transaction(
                database, "repertoire_game_refresh", "all",
                {"after_game_id": game["id"]}, priority=90,
            )
        return True
