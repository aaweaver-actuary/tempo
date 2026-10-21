"""Per-line PostgreSQL opening-graph preparation for durable rebuild slices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from tempo_legacy_database.connections import background_read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.opening_graph import worker as storage
from ..card_deletion import omit_deleted_graph_steps
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice,
)
from .opening_graph import GraphInput, GraphStep, build_graph
from .redis_admission_gate import background_lease


_STEP_BATCH_SIZE = 8
_CLEANUP_BATCH_SIZE = 2
_CLEANUP_CANDIDATE_PAGE_SIZE = 256


def request_graph_rebuild_in_transaction(
    database: postgres_store.PostgresConnection, repertoire_id: str, local_day: str,
) -> dict[str, Any]:
    """Enqueue a new graph generation after all imported and staged generations."""
    storage.lock_graph_task(database, repertoire_id)
    storage.lock_transition_reservations(database)
    storage.lock_graph_rebuild(database, repertoire_id)
    latest_staged = storage.latest_staged_generation(database, repertoire_id)
    latest_published = storage.published_generation(database, repertoire_id)
    minimum_generation = max(
        int(latest_staged[0]) if latest_staged else 0,
        int(latest_published[0]) if latest_published else 0,
    )
    return enqueue_task_in_transaction(
        database, "opening_graph_rebuild", repertoire_id,
        {"repertoire_id": repertoire_id, "local_day": local_day,
         "after_line_id": "", "step_offset": 0, "after_card_id": ""},
        priority=40, minimum_generation=minimum_generation,
    )


@dataclass(frozen=True)
class PreparedGraphLine:
    """One line's graph, calculated after its read transaction has closed."""

    line_id: str
    steps: tuple[GraphStep, ...]


def prepare_next_graph_line(repertoire_id: str, after_line_id: str) -> PreparedGraphLine | None:
    """Read one source line, close PostgreSQL, then traverse its moves."""

    with background_read_connection() as database:
        line = storage.next_source_line(database, repertoire_id, after_line_id)
        if line is None:
            return None
        default_depth = storage.initial_depth(database)
        prefix_overrides = tuple(dict(row) for row in storage.prefix_override_rows(database))
        source_line = dict(line)
    graph_input = GraphInput(repertoire_id, (source_line,), int(default_depth), prefix_overrides)
    return PreparedGraphLine(source_line["id"], build_graph(graph_input))


def stage_graph_steps(database, batch, generation):
    """Shared publisher writer; transition staging intentionally creates no cards."""
    with storage.graph_cursor(database) as cursor:
        storage.insert_steps(cursor, [
                (step.repertoire_id, generation, step.line_id, step.decision_index,
                 step.segment_kind, step.first_decision_index, step.last_decision_index,
                 json.dumps(step.decision_fen_keys), step.card_id, step.parent_card_id,
                 step.decision_fen_key, step.starting_fen, json.dumps(step.moves),
                 step.trained_color)
                for step in batch
            ])


def create_graph_cards(database, batch, study_day, *, strict=False):
    """Use publisher defaults; strict application never adopts an absent-ID race."""
    if not batch:
        return
    card_rows = [
        (step.card_id, step.repertoire_id,
         "prefix" if step.segment_kind == "prefix" else "response",
         step.starting_fen, json.dumps(step.moves),
         "new" if step.parent_card_id is None else "locked", study_day, step.trained_color)
        for step in batch
    ]
    storage.insert_cards(database, card_rows, strict=strict)


def stage_graph_line_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    prepared: PreparedGraphLine | None,
) -> bool:
    """Stage at most eight steps and atomically advance the durable cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if prepared is None:
        return advance_task_slice_in_transaction(
            database, task, next_phase="link", next_payload={**payload, "step_offset": 0},
        )
    offset = int(payload.get("step_offset", 0))
    if offset < 0 or offset >= max(1, len(prepared.steps)):
        raise ValueError("Opening graph slice cursor is outside its source line")
    original_batch = prepared.steps[offset:offset + _STEP_BATCH_SIZE]
    # Synchronize with permanent deletion before checking identity exclusions.
    # Parents must be fenced too: a child cannot retain a removed prerequisite.
    route_ids = sorted({step.card_id for step in prepared.steps})
    for identifier in route_ids:
        storage.lock_card_edit(database, identifier)
    deleted_ids = {row[0] for row in storage.deleted_card_rows(database, route_ids)}
    retained_steps = {step.decision_index: step for step in omit_deleted_graph_steps(prepared.steps, deleted_ids)}
    batch = tuple(retained_steps[step.decision_index] for step in original_batch if step.decision_index in retained_steps)
    generation = int(task["generation"])
    study_day = str(payload["local_day"])
    stage_graph_steps(database, batch, generation)
    create_graph_cards(database, batch, study_day)
    next_offset = offset + len(original_batch)
    next_payload = {
        **payload,
        "after_line_id": prepared.line_id if next_offset == len(prepared.steps)
        else payload.get("after_line_id", ""),
        "step_offset": 0 if next_offset == len(prepared.steps) else next_offset,
    }
    return advance_task_slice_in_transaction(
        database, task, next_phase="stage", next_payload=next_payload,
    )


def execute_graph_stage_slice(task: dict[str, Any]) -> bool:
    """Prepare one bounded line batch without a connection, then checkpoint it."""

    payload = task["payload"]
    prepared = prepare_next_graph_line(
        str(payload["repertoire_id"]), str(payload.get("after_line_id", "")),
    )
    with background_lease():
        with storage.writer_connection() as database:
            return stage_graph_line_in_transaction(database, task, prepared)


def prepare_next_graph_cards(
    repertoire_id: str, generation: int, after_card_id: str, *, limit: int = 8,
) -> tuple[str, ...]:
    """Read a bounded set of distinct staged cards under the admission gate."""

    with background_read_connection() as database:
        cards = storage.staged_card_ids(database, repertoire_id, generation, after_card_id, limit)
    return tuple(str(card[0]) for card in cards)


def link_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Link at most eight staged cards, then checkpoint the sorted cursor."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="publish", next_payload=payload,
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    with storage.graph_cursor(database) as cursor:
        storage.insert_links(cursor, [(repertoire_id, card_id, repertoire_id, generation, card_id)
             for card_id in card_ids])
    return advance_task_slice_in_transaction(
        database, task, next_phase="link",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_link_slice(task: dict[str, Any]) -> bool:
    """Link eight staged cards without delaying foreground transactions."""

    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with storage.writer_connection() as database:
            return link_graph_cards_in_transaction(database, task, card_ids)


def publish_graph_generation(database, repertoire_id, generation):
    """Shared atomic visibility boundary after all generated identities are linked."""
    missing_link = storage.missing_link(database, repertoire_id, generation)
    if missing_link:
        raise RuntimeError("Opening graph generation has unlinked cards")
    storage.publish_generation(database, (repertoire_id, generation, datetime.now(timezone.utc).isoformat()))


def publish_graph_in_transaction(
    database: postgres_store.PostgresConnection, task: dict[str, Any],
) -> bool:
    """Change the visible generation only after every staged card is linked."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    publish_graph_generation(database, repertoire_id, generation)
    return advance_task_slice_in_transaction(
        database, task, next_phase="classify",
        next_payload={**payload, "after_card_id": ""},
    )


def execute_graph_publish_slice(task: dict[str, Any]) -> bool:
    with background_lease():
        with storage.writer_connection() as database:
            return publish_graph_in_transaction(database, task)


def classify_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Refresh at most eight cards against the newly published graph."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="integrity",
            next_payload={**payload, "after_card_id": ""},
        )
    study_day = str(payload["local_day"])
    for card_id in card_ids:
        classification = storage.classification(database, card_id)
        if classification is None or classification[0] is None:
            raise RuntimeError("Published graph card disappeared before classification")
        kind = "prefix" if classification[0] else "response"
        next_state = "new" if classification[1] else "locked"
        if 'maintenance_scope' in task and storage.protected_card(database, card_id, kind):
            raise ValueError('Selected rebuild would reclassify a protected card; stop for diagnosis')
        storage.classify_card(database, kind, next_state, study_day, card_id)
    return advance_task_slice_in_transaction(
        database, task, next_phase="classify",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_classify_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with storage.writer_connection() as database:
            return classify_graph_cards_in_transaction(database, task, card_ids)


def refresh_graph_integrity_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    card_ids: tuple[str, ...],
) -> bool:
    """Rebuild integrity blocks for two published cards and update validation."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if not card_ids:
        return advance_task_slice_in_transaction(
            database, task, next_phase="cleanup",
            next_payload={**payload, "after_card_id": ""},
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    now = datetime.now(timezone.utc).isoformat()
    for card_id in card_ids:
        storage.delete_card_blocks(database, repertoire_id, card_id)
        storage.insert_card_blocks(database, (repertoire_id, card_id, f"graph:{generation}", now,
             repertoire_id, generation, card_id))
        storage.refresh_validation(database, card_id)
    return advance_task_slice_in_transaction(
        database, task, next_phase="integrity",
        next_payload={**payload, "after_card_id": card_ids[-1]},
    )


def execute_graph_integrity_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    card_ids = prepare_next_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")), limit=2,
    )
    with background_lease():
        with storage.writer_connection() as database:
            return refresh_graph_integrity_in_transaction(database, task, card_ids)


@dataclass(frozen=True)
class PreparedGraphCleanupSlice:
    """At most two obsolete cards and the resolved prefix of one source page."""

    obsolete_card_ids: tuple[str, ...]
    checkpoint_card_id: str | None


def prepare_obsolete_graph_cards(
    repertoire_id: str, generation: int, after_card_id: str,
) -> PreparedGraphCleanupSlice:
    """Inspect a bounded raw-key page before deciding which old links to remove."""

    with background_read_connection() as database:
        rows = storage.cleanup_candidates(database, repertoire_id, after_card_id, _CLEANUP_CANDIDATE_PAGE_SIZE, generation)
    obsolete_card_ids: list[str] = []
    checkpoint_card_id = None
    for row in rows:
        checkpoint_card_id = str(row[0])
        if row[1]:
            obsolete_card_ids.append(checkpoint_card_id)
            if len(obsolete_card_ids) == _CLEANUP_BATCH_SIZE:
                # The suffix may contain more obsolete links; replay it next slice.
                break
    return PreparedGraphCleanupSlice(tuple(obsolete_card_ids), checkpoint_card_id)


def remove_obsolete_graph_memberships(database, repertoire_id, generation, card_ids):
    """Clean an explicitly bounded identity set with constant client SQL work.

    Lock the eligible links/cards together before mutations. Keep queue retirement,
    archival and owner reassignment before deleting links: those links suppress
    implicit authored-owner fallback until the retained owner is established.
    Ordinary graph cleanup still supplies at most two identities per durable slice.
    """
    if not card_ids:
        return
    eligible_card_ids = [row[0] for row in storage.eligible_cleanup_cards(database, repertoire_id, card_ids, generation)]
    if not eligible_card_ids:
        return
    storage.retire_orphaned_queue(database, eligible_card_ids, repertoire_id)
    # Also retire a globally authored card whose only effective scope is this
    # generated owner association; retain its card row and all real history.
    storage.archive_generated_owner(database, eligible_card_ids, repertoire_id)
    storage.reassign_retained_owner(database, repertoire_id, eligible_card_ids)
    storage.delete_generated_links(database, repertoire_id, eligible_card_ids)
    storage.delete_obsolete_blocks(database, repertoire_id, eligible_card_ids)
    storage.archive_unlinked_generated_cards(database, eligible_card_ids)


def cleanup_graph_cards_in_transaction(
    database: postgres_store.PostgresConnection,
    task: dict[str, Any],
    prepared: PreparedGraphCleanupSlice,
) -> bool:
    """Remove at most two old links, preserving cards shared by other repertoires."""

    if not lock_current_slice(database, task):
        return False
    payload = dict(task["payload"])
    if prepared.checkpoint_card_id is None:
        return advance_task_slice_in_transaction(
            database, task, next_phase="finalize", next_payload=payload,
        )
    repertoire_id = str(payload["repertoire_id"])
    generation = int(task["generation"])
    if 'maintenance_scope' in task and prepared.obsolete_card_ids:
        protected = storage.protected_queue_entry(database, (list(prepared.obsolete_card_ids), payload['local_day']))
        if protected:
            raise ValueError('Selected rebuild would retire a protected queue entry; stop for diagnosis')
    remove_obsolete_graph_memberships(database, repertoire_id, generation, prepared.obsolete_card_ids)
    return advance_task_slice_in_transaction(
        database, task, next_phase="cleanup",
        next_payload={**payload, "after_card_id": prepared.checkpoint_card_id},
    )


def execute_graph_cleanup_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    prepared = prepare_obsolete_graph_cards(
        str(payload["repertoire_id"]), int(task["generation"]),
        str(payload.get("after_card_id", "")),
    )
    with background_lease():
        with storage.writer_connection() as database:
            return cleanup_graph_cards_in_transaction(database, task, prepared)


def finalize_graph_in_transaction(
    database: postgres_store.PostgresConnection, task: dict[str, Any],
) -> bool:
    """Queue the foreground-visible refresh and complete this graph generation."""

    if not lock_current_slice(database, task):
        return False
    from .postgres_integrity import request_integrity_scan_in_transaction

    request_integrity_scan_in_transaction(
        database, str(task["payload"]["repertoire_id"]),
        int(task["generation"]), str(task["payload"]["local_day"]),
    )
    if not complete_task_slice_in_transaction(database, task):
        raise RuntimeError("Opening graph lease changed before finalization")
    return False


def execute_graph_finalize_slice(task: dict[str, Any]) -> bool:
    from .durable_tasks import warm_completion_sql

    warm_completion_sql()
    with background_lease():
        with storage.writer_connection() as database:
            return finalize_graph_in_transaction(database, task)


def execute_postgres_opening_graph_slice(task: dict[str, Any]) -> bool:
    """Run exactly one durable graph phase, yielding to foreground afterward."""

    phase = task.get("phase", "queued")
    handlers = {
        "queued": execute_graph_stage_slice,
        "stage": execute_graph_stage_slice,
        "link": execute_graph_link_slice,
        "publish": execute_graph_publish_slice,
        "classify": execute_graph_classify_slice,
        "integrity": execute_graph_integrity_slice,
        "cleanup": execute_graph_cleanup_slice,
        "finalize": execute_graph_finalize_slice,
    }
    handler = handlers.get(phase)
    if handler is None:
        raise ValueError(f"Unknown PostgreSQL opening graph phase: {phase}")
    return handler(task)
