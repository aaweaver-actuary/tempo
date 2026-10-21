"""Stage gameplay events one row at a time and publish a complete generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.games.derivation import events as storage, jobs
from .durable_tasks import (
    advance_task_slice_in_transaction,
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    lock_current_slice,
)
from .gameplay_events import refresh_gameplay_events


def execute_game_event_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    cursor = int(payload.get("cursor", 0))
    events = refresh_gameplay_events(game_id, background=True, prepare_only=True) or []
    signature = hashlib.sha256(json.dumps(
        [event[:-2] for event in events], sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = jobs.lock_job(database, game_id)
        if (job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 4
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if expected_signature is not None and expected_signature != signature:
            next_version = version + 1
            storage.restart_changed_event_source(database, game_id, version, next_version)
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_positions", game_id,
                {"game_id": game_id, "derivation_version": next_version, "cursor": 0},
                priority=125,
            )
            return complete_task_slice_in_transaction(database, task)
        if cursor < len(events):
            storage.stage_event(database, events[cursor], version)
            return advance_task_slice_in_transaction(
                database, task, next_phase="refreshing_events",
                next_payload={**payload, "cursor": cursor + 1,
                              "source_signature": signature},
            )
        staged_count = storage.staged_event_count(database, game_id, version)
        if staged_count != len(events):
            raise RuntimeError("Gameplay event stage is incomplete; publication was withheld")
        storage.publish_event_generation(database, game_id, version)
        enqueue_compact_postgres_task_in_transaction(
            database, "game_derivation_features", game_id,
            {"game_id": game_id, "derivation_version": version}, priority=126,
        )
        return complete_task_slice_in_transaction(database, task)
