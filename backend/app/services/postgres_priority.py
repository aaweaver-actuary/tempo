"""Prepare priority evidence once, then publish bounded PostgreSQL slices."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from .background_metrics import increment
from typing import Any

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.priority_lifecycle import preparation
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)
from .introduction_priorities import (
    SCORING_VERSION, _load_priority_calculation_input,
    calculate_priority_records, enqueue_priority_refresh_in_transaction,
)


def _batch_size() -> int:
    return max(1, min(64, int(os.getenv("TEMPO_PRIORITY_STAGE_BATCH_SIZE", "16"))))


def _has_current_priority_lease(database, task, repertoire_id, generation):
    lease = preparation.task_lease(database, task["id"])
    if not lease or (lease["generation"] != task["generation"]
                     or lease["lease_token"] != task["lease_token"]
                     or lease["state"] != "leased"):
        return False
    # Source writers take the epoch before the application enqueue, whose
    # lock order is job then task. Preserve that order here.
    if _priority_source_version(database, repertoire_id, lock=True) is None:
        return False
    job = preparation.locked_current_job(database, repertoire_id)
    return bool(job and int(job["generation"]) == generation
                and job["status"] in {"queued", "running"}
                and lock_current_slice(database, task))


def _priority_source_version(database, repertoire_id, *, lock=False):
    return preparation.source_version(database, repertoire_id, lock=lock)


def _load_preparation_manifest(database, repertoire_id, generation):
    return preparation.preparation_manifest(database, repertoire_id, generation)


def _prepare_priority_generation(task, repertoire_id, generation):
    payload = task["payload"]
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        manifest = _load_preparation_manifest(database, repertoire_id, generation)
        if manifest and manifest["status"] == "ready":
            return advance_task_slice_in_transaction(
                database, task, next_phase="staging_priorities",
                next_payload={**payload, "cursor": 0},
            )
        if manifest and int(manifest["scoring_version"]) != SCORING_VERSION:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        calculated_at = (manifest["calculated_at"] if manifest
                         else datetime.now(timezone.utc).isoformat())
        if not manifest:
            preparation.insert_preparation(database, repertoire_id, generation, _priority_source_version(database, repertoire_id), SCORING_VERSION, calculated_at)
        source_version = str(manifest["source_version"]) if manifest else _priority_source_version(database, repertoire_id)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True

    calculation_input = _load_priority_calculation_input(
        repertoire_id, background=True, calculated_at=datetime.fromisoformat(calculated_at),
    )
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
    # Count invocations before compute, including attempts whose result is later stale.
    with connection(background=True) as database:
        increment(database, "repertoire_priority", task["id"], priority_calculator_calls=1)
    records = calculate_priority_records(calculation_input)
    # Ordinals are durable identities across a partially committed retry.
    records.sort(key=lambda record: record.card_id)
    batch_size = _batch_size()
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        with connection(background=True) as database:
            if not _has_current_priority_lease(database, task, repertoire_id, generation):
                return complete_task_slice_in_transaction(database, task)
            if _priority_source_version(database, repertoire_id) != source_version:
                enqueue_priority_refresh_in_transaction(database, repertoire_id)
                return True
            preparation.upsert_prepared_rows(database, [(repertoire_id, generation, start + offset, record.card_id,
                  record.completed_line_ids_json, record.completion_mass,
                  record.frontier_decisions_json, record.frontier_reach,
                  record.priority_score, record.evidence_json)
                 for offset, record in enumerate(batch)])

    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        if _priority_source_version(database, repertoire_id) != source_version:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        count = preparation.prepared_count(database, repertoire_id, generation)
        if count != len(records):
            raise RuntimeError("Priority preparation is incomplete")
        preparation.mark_preparation_ready(database, count, repertoire_id, generation)
        preparation.mark_running(database, datetime.now(timezone.utc).isoformat(), repertoire_id, generation)
        return advance_task_slice_in_transaction(
            database, task, next_phase="staging_priorities",
            next_payload={**payload, "cursor": 0},
        )


def execute_repertoire_priority_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    repertoire_id = str(payload["repertoire_id"])
    generation = int(payload["generation"])
    cursor_value = payload.get("cursor")
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        manifest = _load_preparation_manifest(database, repertoire_id, generation)
        unverifiable_stage = (
            "source_signature" in payload
            or (cursor_value is not None and int(cursor_value) > 0
                and (manifest is None or manifest["status"] != "ready"))
            or (manifest is not None and manifest["ordering_version"] != 1)
        )
        if unverifiable_stage:
            # Advancing the fenced job/task generation leaves the last published
            # generation readable and makes replay of this old lease inert.
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        needs_preparation = (
            cursor_value is None or manifest is None
            or manifest["status"] == "preparing"
        )
    if needs_preparation:
        return _prepare_priority_generation(task, repertoire_id, generation)

    cursor = int(cursor_value)
    now = datetime.now(timezone.utc).isoformat()
    with connection(background=True) as database:
        if not _has_current_priority_lease(database, task, repertoire_id, generation):
            return complete_task_slice_in_transaction(database, task)
        manifest = _load_preparation_manifest(database, repertoire_id, generation)
        if not manifest or manifest["status"] != "ready":
            raise RuntimeError("Priority preparation is not complete")
        if int(manifest["scoring_version"]) != SCORING_VERSION:
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        if _priority_source_version(database, repertoire_id) != str(manifest["source_version"]):
            enqueue_priority_refresh_in_transaction(database, repertoire_id)
            return True
        expected_count = int(manifest["expected_count"])
        rows = list(preparation.prepared_page(database, repertoire_id, generation, cursor, _batch_size()))
        if rows:
            if int(rows[0]["ordinal"]) != cursor:
                raise RuntimeError("Priority preparation has a missing row")
            preparation.upsert_staged_rows(database, [(repertoire_id, generation, row["card_id"], SCORING_VERSION,
                  row["completed_line_ids_json"], row["completion_mass"],
                  row["frontier_decisions_json"], row["frontier_reach"],
                  row["priority_score"], row["evidence_json"], now)
                 for row in rows])
            return advance_task_slice_in_transaction(
                database, task, next_phase="staging_priorities",
                next_payload={**payload, "cursor": cursor + len(rows)},
            )
        prepared_count = preparation.prepared_count(database, repertoire_id, generation)
        staged_count = preparation.staged_count(database, repertoire_id, generation)
        if cursor != expected_count or prepared_count != expected_count or staged_count != expected_count:
            raise RuntimeError("Priority generation is incomplete; publication was withheld")
        preparation.publish_generation(database, repertoire_id, generation, now)
        preparation.complete_job(database, now, repertoire_id, generation)
        enqueue_compact_postgres_task_in_transaction(
            database, "priority_retention", repertoire_id,
            {"repertoire_id": repertoire_id}, priority=200,
        )
        completed = complete_task_slice_in_transaction(database, task)
        if completed:
            from .refresh_requests import record_publication
            record_publication(database, 'repertoire_priority', repertoire_id)
            from .repertoire_opportunities import enqueue_opportunity_refresh_in_transaction
            enqueue_opportunity_refresh_in_transaction(database, repertoire_id)
            increment(database, "repertoire_priority", task["id"], priority_publications=1,
                      priority_published_records=expected_count, useful_completions=1)
        return completed
