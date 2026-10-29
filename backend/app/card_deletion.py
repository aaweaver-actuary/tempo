"""Explicit content deletion, shared ownership, and durable recreation exclusions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import defaultdict
from bisect import bisect_left
from datetime import date, datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from tempo_legacy_database.adapters.authored_repertoires.card_deletion import storage
from tempo_legacy_database.adapters.authored_repertoires.card_deletion.storage import (
    is_card_deleted, _delete_matching,
)

RETAINED_REPERTOIRE_ID = "__retained_cards__"
SYSTEM_REPERTOIRE_IDS = ("__tactics__", "__game_mistakes__", "__game_tactics__", "__captured_tactics__", "__defense__", RETAINED_REPERTOIRE_ID)


@dataclass(frozen=True)
class _StoredGraphDependency:
    repertoire_id: str
    line_id: str
    decision_index: int
    card_id: str
    parent_card_id: str | None


def require_card_not_deleted(database, identifier: str) -> None:
    if is_card_deleted(database, identifier):
        raise HTTPException(409, "This card was permanently deleted and cannot be recreated")


def cancel_repertoire_tasks(database, repertoire_id: str) -> None:
    """Decode only candidate payloads; PostgreSQL JSONB rejects valid escaped nulls."""
    now, candidates = storage.cancellation_candidates(database, repertoire_id, lambda: datetime.now(timezone.utc).isoformat())
    for task in candidates:
        payload = json.loads(task["payload_json"])
        if task["deduplication_key"] == repertoire_id or isinstance(payload, dict) and payload.get("repertoire_id") == repertoire_id:
            native_recheck, current = storage.locked_cancellation_candidate(database, task)
            if native_recheck:
                # Lock exact matches in ID order, then recheck after a worker's
                # checkpoint. Clearing its lease fences every later slice.
                if current is None:
                    continue
                current_payload = json.loads(current["payload_json"])
                if current["deduplication_key"] != repertoire_id and (not isinstance(current_payload, dict) or current_payload.get("repertoire_id") != repertoire_id):
                    continue
            storage.cancel_task(database, now, task["id"])


def purge_card_data(database, identifiers: list[str]) -> None:
    """Delete training records; keep immutable receipts/admission proof for replay."""
    if not identifiers:
        return
    queue_ids, puzzle_ids = storage.purge_sources(database, identifiers)
    storage.purge_tactic_attempts(database, puzzle_ids)
    if storage.native_cleanup_supported(database):
        _purge_opening_evidence(database, identifiers, queue_ids)
    storage.clear_source_references(database, identifiers, lambda: datetime.now(timezone.utc).isoformat())
    storage.purge_training_records(database, identifiers, queue_ids)
    # A deleted source remains available for browsing, but not for card admission.
    now = datetime.now(timezone.utc).isoformat()
    storage.record_tombstones(database, identifiers, now)
    storage.purge_queue_and_cards(database, identifiers)


def _purge_opening_evidence(database, identifiers, queue_ids):
    affected_decisions = storage.affected_evidence_decisions(database, identifiers)
    storage.purge_evidence_records(database, identifiers, queue_ids, affected_decisions)
    remaining = defaultdict(lambda: {"counts": [0] * 6, "days": set()})
    for decision_id, serialized in storage.remaining_evidence_observations(database, affected_decisions):
        observation = json.loads(serialized)
        responded = observation.get("first_response_uci") is not None
        counts = (int(responded), int(observation.get("clean", False)), int(observation.get("first_response_correct") is False), int(responded and bool(observation.get("assistance_before_response"))), int(observation.get("manual_failure", False)), int(observation.get("corrected", False)))
        summary = remaining[decision_id]
        summary["counts"] = [left + right for left, right in zip(summary["counts"], counts)]
        if observation.get("clean"):
            summary["days"].add(observation["study_day"])
    for decision_id, summary in remaining.items():
        storage.record_evidence_summary(database, decision_id, summary)
        storage.record_evidence_clean_days(database, decision_id, summary["days"])


def delete_repertoire_data(database, repertoire_id: str, learned_cards: str) -> dict[str, Any]:
    if repertoire_id in SYSTEM_REPERTOIRE_IDS:
        raise HTTPException(400, "This system repertoire cannot be deleted")
    if learned_cards not in {"keep", "delete"}:
        raise HTTPException(422, "Choose whether to keep or delete learned cards")
    storage.lock_main_repertoire(database)
    cancel_repertoire_tasks(database, repertoire_id)
    repertoire = storage.locked_repertoire(database, repertoire_id)
    if repertoire is None:
        raise HTTPException(404, "Repertoire not found")
    storage.lock_affected_cards(database, repertoire_id)
    owned = storage.owned_cards(database, repertoire_id)
    deleted_ids = []
    for card in owned:
        replacement = card["replacement"]
        if replacement is None and learned_cards == "keep" and (card["introduced_at"] or card["first_correct_at"] or card["has_reviews"]):
            replacement = RETAINED_REPERTOIRE_ID
            storage.create_retained_repertoire(database, replacement, lambda: datetime.now(timezone.utc).isoformat())
            storage.retain_membership(database, replacement, card["id"])
        if replacement is None:
            deleted_ids.append(card["id"])
        else:
            if replacement == RETAINED_REPERTOIRE_ID:
                retained_color = card["effective_trained_color"]
                if retained_color is None:
                    move_count = len(json.loads(card["moves_json"]))
                    starts_white = card["start_fen"].split()[1] == "w"
                    retained_color = ("white" if starts_white == bool(move_count % 2) else "black") if move_count else None
                storage.set_retained_color(database, retained_color, card["id"])
            storage.reassign_card(database, replacement, RETAINED_REPERTOIRE_ID, card["id"])
    # The same identity-only exclusions protect both permanent deletion paths
    # from stale submissions and later source rebuilds or imports.
    purge_card_data(database, deleted_ids)
    # All surviving admissions follow the surviving primary owner, including
    # cards owned elsewhere. Existing triggers retain the original attempt scope.
    storage.reassign_queued_admission(database, repertoire_id)
    storage.clear_repertoire_findings(database, repertoire_id)
    storage.delete_comparisons(database, repertoire_id)
    storage.delete_repertoire(database, repertoire_id)
    if repertoire["is_main"]:
        replacement = storage.replacement_main(database, SYSTEM_REPERTOIRE_IDS)
        if replacement:
            storage.select_main(database, replacement[0])
    from .queue_commands import request_queue_refresh_in_transaction
    from .services.durable_tasks import enqueue_task_in_transaction
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    enqueue_task_in_transaction(database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90)
    return {"deleted": True, "id": repertoire_id}


def card_deletion_preview(database, identifier: str) -> dict[str, Any]:
    card = storage.preview_card(database, identifier)
    if card is None:
        raise HTTPException(404, "Card not found")
    repertoires = storage.preview_repertoires(database, identifier)
    return {"card_id": identifier, "revision": int(card["revision"]), "repertoires": [dict(row) for row in repertoires]}


def permanent_delete_card(database, identifier: str, expected_revision: int) -> dict[str, Any]:
    storage.lock_card(database, identifier)
    card = storage.locked_revision(database, identifier)
    if card is None:
        raise HTTPException(404, "Card not found")
    if int(card["revision"]) != expected_revision:
        raise HTTPException(409, "The card changed; refresh it before deleting")
    # Link each child to the deleted step's predecessor within its own source
    # route, preserving all other cards and their schedules without fake reviews.
    _reparent_stored_graph_dependencies(database, identifier)
    storage.delete_card_graph_steps(database, identifier)
    purge_card_data(database, [identifier])
    from .queue_commands import request_queue_refresh_in_transaction
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {"deleted": True, "card_id": identifier}


def _reparent_stored_graph_dependencies(database, identifier: str) -> None:
    """Use the same occurrence-aware ancestor rule at deletion and rebuild."""
    routes = storage.graph_routes(database, identifier)
    for route in routes:
        dependencies = tuple(_StoredGraphDependency(route["repertoire_id"], route["line_id"],
            int(row["decision_index"]), row["card_id"], row["parent_card_id"])
            for row in storage.graph_dependencies(database, route))
        previous_parents = {step.decision_index: step.parent_card_id for step in dependencies}
        updates = [(step.parent_card_id, route["repertoire_id"], route["generation"], route["line_id"], step.decision_index)
            for step in omit_deleted_graph_steps(dependencies, {identifier})
            if step.parent_card_id != previous_parents[step.decision_index]]
        storage.reparent_dependencies(database, updates)


def omit_deleted_graph_steps(steps, deleted_ids: set[str]):
    occurrences = defaultdict(list)
    for step in steps:
        occurrences[(step.repertoire_id, step.line_id, step.card_id)].append(step)
    for route_occurrences in occurrences.values():
        route_occurrences.sort(key=lambda step: step.decision_index)
    retained = []
    for step in steps:
        if step.card_id in deleted_ids:
            continue
        parent_id = step.parent_card_id
        search_before_decision = step.decision_index
        while parent_id in deleted_ids or parent_id == step.card_id:
            # Repeated positions share a card identity. Follow the preceding
            # occurrence in this route, never a later visit or a self dependency.
            parent_occurrences = occurrences[(step.repertoire_id, step.line_id, parent_id)]
            preceding_index = bisect_left(parent_occurrences, search_before_decision,
                key=lambda occurrence: occurrence.decision_index) - 1
            if preceding_index < 0:
                parent_id = None
                break
            predecessor = parent_occurrences[preceding_index]
            search_before_decision = predecessor.decision_index
            parent_id = predecessor.parent_card_id
        retained.append(replace(step, parent_card_id=parent_id))
    return tuple(retained)
