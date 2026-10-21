"""Durable one-game slices after authoritative repertoire scope changes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.repertoire_scope import game_refresh as storage
from .activity_gate import activity_gate
from .durable_tasks import (
    enqueue_compact_postgres_task_in_transaction,
    enqueue_task_in_transaction,
    lock_current_slice,
)


@contextmanager
def refreshing_game_scope(database):
    """Admit replacement work in the mutation transaction only when scope advances.

    Reset the existing full sweep's cursor: an in-flight sweep may already have
    passed games invalidated by this mutation. Task generations fence its lease.
    """
    from .canonical_scope_freshness import game_scope_generation
    previous_generation = game_scope_generation(database)
    yield
    if game_scope_generation(database) != previous_generation:
        if storage.uses_native_queries(database):
            enqueue_compact_postgres_task_in_transaction(
                database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90,
            )
        else:
            enqueue_task_in_transaction(
                database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90,
            )


def refresh_game_publications_after_mutation(handler):
    """Wrap a product write using its existing short transaction; never compute here."""
    @wraps(handler)
    def mutate(database, *args, **kwargs):
        with refreshing_game_scope(database):
            return handler(database, *args, **kwargs)
    return mutate


def execute_repertoire_game_refresh_slice(task: dict) -> bool:
    """Queue one derivation, then persist a cursor for the following slice."""
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if storage.postgres_is_configured():
            if not lock_current_slice(database, task):
                return False
        else:
            current = storage.leased_local_task(database, task["id"], task["generation"], task["lease_token"])
            if not current:
                return False
        game = storage.next_game(database, task["payload"].get("after_game_id", ""))
        if not game:
            return False
        now = datetime.now(timezone.utc).isoformat()
        storage.queue_derivation(database, game["id"], now)
        if storage.postgres_is_configured():
            derivation_job = storage.derivation_version(database, game["id"])
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
