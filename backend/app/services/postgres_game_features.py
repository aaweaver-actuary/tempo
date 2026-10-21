"""Publish one game's feature row with a derivation-generation fence."""

from __future__ import annotations

from typing import Any

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.games.derivation import features as storage, jobs
from .durable_tasks import (
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .statistics import refresh_game_features


def execute_game_feature_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    feature_values = refresh_game_features(game_id, background=True, prepare_only=True)
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = jobs.lock_job(database, game_id)
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 5
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        if feature_values is None:
            raise RuntimeError("Game feature input disappeared during derivation")
        storage.write_feature_values(database, feature_values)
        storage.advance_feature_phase(database, game_id, version)
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_priorities", game_id,
            {"game_id": game_id, "derivation_version": version,
             "after_repertoire_id": ""}, priority=126,
        )
        return complete_task_slice_in_transaction(database, task)
