"""Durable validation of one saved line/card per background slice."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import chess

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.canonical_prefix import preview as storage, retention
from tempo_legacy_database.adapters.authored_repertoires.canonical_prefix.retention import (
    _RETENTION_VICTIMS_SQL, _RETENTION_TASK_SQL, _RETIRE_TASK_SQL, _INVALIDATE_PREVIEW_SQL,
    _DELETE_RESULTS_SQL, _DELETE_POSITIONS_SQL, _DELETE_EVENTS_SQL,
    _DELETE_RETIRED_TASK_SQL, _DELETE_EMPTY_PREVIEW_SQL,
)
from tempo_legacy_database.ddl.canonical_prefix_preview_schema import TABLE_DEFINITIONS, initialize_sqlite_schema
from tempo_legacy_database.connections import background_read_connection, connection
from .canonical_prefix import (
    line_origin, position_key, prefix_projection, read_prefix, store_positions,
    validate_scoped_line,
)
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice, warm_completion_sql,
)


RETAINED_PREVIEWS = 8
RETENTION_ROW_BUDGET = 64
RETENTION_VICTIM_BUDGET = 4
_EXPIRED_PREVIEW_ERROR = 'This compatibility preview expired. Check the prefix again before saving.'


def _warm_retention_sql() -> None:
    retention.warm_queries(warm_completion_sql)


def _cleanup_retention_victim(database, victim: dict, remaining_rows: int) -> int:
    """Spend this slice's shared row budget; parent cascades must be empty."""
    preview_id = victim['id']
    old_task = retention.retention_task(database, preview_id)
    if old_task and old_task['state'] != 'superseded':
        # Retirement and certificate invalidation cannot be split across commits.
        if remaining_rows < 2:
            return remaining_rows
        remaining_rows -= retention.retire_task(database, old_task['id'])
    if victim['state'] != 'stale':
        if not remaining_rows:
            return remaining_rows
        remaining_rows -= retention.invalidate_preview(database, _EXPIRED_PREVIEW_ERROR, preview_id)
    for delete_children, parent_id in ((retention.delete_results, preview_id),
                                  (retention.delete_positions, preview_id),
                                  (retention.delete_events, old_task['id'] if old_task else None)):
        if not remaining_rows:
            return remaining_rows
        if parent_id is not None:
            remaining_rows -= delete_children(database, parent_id, remaining_rows)
    if old_task and remaining_rows:
        deleted = retention.delete_retired_task(database, old_task['id'])
        remaining_rows -= deleted
        if not deleted:
            return remaining_rows
    if remaining_rows:
        remaining_rows -= retention.delete_empty_preview(database, preview_id)
    return remaining_rows


def _execute_retention_slice(task: dict) -> bool:
    """Clean at most four victims / 64 rows, checkpoint atomically, then yield."""
    payload = task["payload"]
    repertoire_id = payload["repertoire_id"]
    retention_parameters = (repertoire_id, payload['preview_id'], repertoire_id, RETAINED_PREVIEWS, repertoire_id)
    _warm_retention_sql()
    with background_read_connection(authoritative=True) as database:
        prepared_ids = {row['id'] for row in retention.retention_victims(database, (*retention_parameters, RETENTION_VICTIM_BUDGET))}
    with connection(background=True) as database:
        read_prefix(database, repertoire_id, lock=True)
        lease_current = lock_current_slice(database, task) if storage.uses_native_queries(database) else storage.leased_local_task(database, task['id'], task['generation'], task['lease_token'])
        if not lease_current:
            return False
        remaining_rows = RETENTION_ROW_BUDGET
        # Creation, activation, and source writes use this same repertoire lock.
        # A prepared victim may now be protected; never trust the earlier page.
        victims = retention.retention_victims(database, (*retention_parameters, RETENTION_VICTIM_BUDGET))
        for victim in victims:
            if not remaining_rows:
                break
            if victim['id'] in prepared_ids:
                remaining_rows = _cleanup_retention_victim(database, victim, remaining_rows)
        if not retention.retention_victim(database, (*retention_parameters, 1)):
            return complete_task_slice_in_transaction(database, task)
        return advance_task_slice_in_transaction(database, task, next_phase='retention', next_payload=payload)



def request_preview(database, repertoire_id: str, moves: list[str]) -> dict:
    if repertoire_id.startswith("__"):
        raise ValueError("Canonical prefixes are only available for opening repertoires")
    if not storage.repertoire_presence(database, repertoire_id):
        raise KeyError("Repertoire not found")
    prefix = read_prefix(database, repertoire_id, lock=True)
    reusable = storage.reusable_preview(database, repertoire_id, json.dumps(moves), prefix["revision"], prefix["source_revision"])
    if reusable:
        return {"preview_id": reusable["id"], "state": reusable["state"], **prefix_projection(moves, prefix["revision"])}
    preview_id = str(uuid.uuid4())
    storage.insert_preview_values(database, (preview_id, repertoire_id, json.dumps(moves), prefix["revision"], prefix["source_revision"], datetime.now(timezone.utc).isoformat()))
    # Prefix positions are bounded by the input limit, independent of repertoire size.
    seed = validate_scoped_line(chess.STARTING_FEN, moves, moves, [])
    store_positions(database, preview_id, seed["positions"])
    enqueue_task_in_transaction(
        database, "canonical_prefix_preview", preview_id,
        {"preview_id": preview_id, "repertoire_id": repertoire_id, "phase": "lines",
         "cursor": "", "pass": 0, "progress": False, "suggestion": None}, priority=65,
    )
    return {"preview_id": preview_id, "state": "checking", **prefix_projection(moves, prefix["revision"])}


def preview_projection(database, repertoire_id: str, preview_id: str, after: str = "") -> dict:
    preview = storage.preview_task_projection(database, preview_id, repertoire_id)
    if preview is None:
        raise KeyError("Prefix preview not found")
    current = read_prefix(database, repertoire_id)
    state = preview["state"]
    error = preview["last_error"]
    if preview["task_state"] == "failed":
        state, error = "failed", preview["task_error"]
    elif current["revision"] != preview["expected_revision"] or current["source_revision"] != preview["source_revision"]:
        state, error = "stale", "The repertoire changed. Check the prefix again before saving."
    conflicts = storage.conflict_rows(database, preview_id, after)
    count = storage.conflict_count(database, preview_id)
    return {"preview_id": preview_id, "state": state, "error": error,
            **prefix_projection(json.loads(preview["moves_json"]), int(preview["expected_revision"])),
            "suggestion": prefix_projection(json.loads(preview["suggestion_json"])),
            "conflicts": conflicts[:20], "conflict_count": count,
            "next_cursor": conflicts[19]["item_id"] if len(conflicts) > 20 else None}


def _next_item(database, preview: dict, payload: dict) -> dict | None:
    phase = payload["phase"]
    prefix_id = "card:" if phase == "cards" else "line:"
    cursor = payload.get("cursor", "")
    if phase == "cards":
        row = storage.next_card(database, preview["id"], preview["repertoire_id"], cursor)
    else:
        row = storage.next_line(database, preview["id"], preview["repertoire_id"], cursor)
    return {**dict(row), "item_id": prefix_id + row["id"]} if row else None


def execute_prefix_preview_slice(task: dict) -> bool:
    payload = task["payload"]
    if payload['phase'] == 'retention':
        return _execute_retention_slice(task)
    with background_read_connection() as database:
        stored_preview = storage.stored_preview(database, payload["preview_id"])
        if stored_preview is None:
            return False
        preview = dict(stored_preview)
        item = _next_item(database, preview, payload)
        prefix = read_prefix(database, preview["repertoire_id"])
        origin = None
        if item:
            origin = line_origin(database, preview["id"], item["start_fen"])
            # Saved histories survive removal/shortening of a prefix without rewriting cards.
            if origin is None and prefix["preview_id"]:
                prior = storage.prior_origin(database, prefix["preview_id"], item["item_id"], prefix["source_revision"])
                if prior and prior["origin_json"] is not None:
                    origin = json.loads(prior["origin_json"])
                else:
                    origin = line_origin(database, prefix["preview_id"], item["start_fen"])
    # The read connection is closed before chess traversal.
    result = None
    suggestion = payload.get("suggestion")
    if item:
        moves = json.loads(item["moves_json"])
        try:
            result = validate_scoped_line(item["start_fen"], moves, json.loads(preview["moves_json"]), origin)
        except ValueError as error:
            result = {"status": "conflict", "reason": str(error), "disagreement_ply": None}
        if payload["phase"] == "lines" and payload.get("pass", 0) == 0 and position_key(item["start_fen"]) == position_key(chess.STARTING_FEN):
            if suggestion is None:
                suggestion = moves[:160]
            else:
                common_length = next((index for index, pair in enumerate(zip(suggestion, moves)) if pair[0] != pair[1]), min(len(suggestion), len(moves)))
                suggestion = suggestion[:common_length]
    with connection(background=True) as database:
        current = read_prefix(database, preview["repertoire_id"], lock=True)
        lease_current = lock_current_slice(database, task) if storage.uses_native_queries(database) else storage.leased_local_task(database, task["id"], task["generation"], task["lease_token"])
        if not lease_current:
            return False
        if current["source_revision"] != preview["source_revision"] or current["revision"] != preview["expected_revision"]:
            storage.mark_stale(database, "The repertoire changed. Check the prefix again.", preview["id"])
            storage.prioritize_retention(database, task["id"])
            return advance_task_slice_in_transaction(database, task, next_phase='retention',
                                                     next_payload={**payload, 'phase': 'retention'})
        if item and result:
            storage.upsert_result_values(database, (preview["id"], item["item_id"], item["name"], result["status"], result.get("reason"), result.get("disagreement_ply"),
                 json.dumps(result["origin"]) if result.get("origin") is not None else None, result.get("scope_start_ply")))
            if result["status"] == "valid":
                store_positions(database, preview["id"], result["positions"])
            return advance_task_slice_in_transaction(database, task, next_phase=payload["phase"], next_payload={
                **payload, "cursor": item["id"], "suggestion": suggestion,
                "progress": payload.get("progress", False) or result["status"] == "valid",
            })
        if payload["phase"] == "lines":
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload={**payload, "phase": "cards", "cursor": ""})
        # Disconnected items are finalized one at a time, retaining bounded writes.
        pending = storage.next_pending_result(database, preview["id"])
        if pending and payload.get("progress"):
            return advance_task_slice_in_transaction(database, task, next_phase="lines", next_payload={
                **payload, "phase": "lines", "cursor": "", "pass": payload.get("pass", 0) + 1, "progress": False,
            })
        if pending:
            storage.mark_pending_conflict(database, preview["id"], pending["item_id"])
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload=payload)
        conflict = storage.conflicting_result(database, preview["id"])
        storage.finish_preview(database, "conflicts" if conflict else "ready", json.dumps(suggestion or []), preview["id"])
        if not conflict and current["moves"] and current["moves"] == json.loads(preview["moves_json"]):
            # Recertification changes no opening assumption or study state.
            storage.set_empty_prefix_preview(database, preview["id"], preview["repertoire_id"])
        storage.prioritize_retention(database, task["id"])
        return advance_task_slice_in_transaction(database, task, next_phase='retention',
                                                 next_payload={**payload, 'phase': 'retention'})
