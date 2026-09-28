"""Publish one game's feature row with a derivation-generation fence."""

from __future__ import annotations

from typing import Any

from ..database import connection
from .durable_tasks import (
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .statistics import GAME_FEATURE_UPSERT_SQL, refresh_game_features


def execute_game_feature_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    feature_values = refresh_game_features(game_id, background=True, prepare_only=True)
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 5
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if feature_values is None:
            raise RuntimeError("Game feature input disappeared during derivation")
        database.execute(GAME_FEATURE_UPSERT_SQL, feature_values)
        database.execute(
            "UPDATE game_derivation_jobs SET completed_phases=6,"
            "phase='enqueueing_priorities' "
            "WHERE game_id=? AND derivation_version=?", (game_id, version),
        )
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_priorities", game_id,
            {"game_id": game_id, "derivation_version": version,
             "after_repertoire_id": ""}, priority=126,
        )
        return complete_task_slice_in_transaction(database, task)
