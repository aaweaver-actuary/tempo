"""Existing one-game refresh storage and database capability facts."""

from .. import postgres_store
from ..games.coordinator.storage import derivation_version


def uses_native_queries(database):
    return hasattr(database, "execute_native")


def postgres_is_configured():
    return postgres_store.configured()


def leased_local_task(database, task_id, generation, lease_token):
    return database.execute(
        "SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
        (task_id, generation, lease_token),
    ).fetchone()


def next_game(database, after_game_id):
    return database.execute(
        "SELECT id FROM imported_games WHERE id>? ORDER BY id LIMIT 1",
        (after_game_id,),
    ).fetchone()


def queue_derivation(database, game_id, now):
    database.execute(
        """INSERT INTO game_derivation_jobs(game_id,status,updated_at) VALUES(?,'queued',?)
               ON CONFLICT(game_id) DO UPDATE SET status='queued',last_error=NULL,
                 derivation_version=game_derivation_jobs.derivation_version+1,
                 completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
        (game_id, now),
    )
