"""Versioned, restartable comparison of one imported game with repertoires."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.games.derivation.jobs import lock_job as lock_derivation_job
from tempo_legacy_database.adapters.games.repertoire import comparison as storage
from .canonical_scope_freshness import game_scope_generation
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)
from .repertoire_comparison import (
    _load_repertoire_index_snapshot, prepare_game_comparisons,
)


def _prepared_comparison(game_id: str) -> tuple[dict | None, str, list[dict]]:
    with background_read_connection() as database:
        row = storage.imported_game(database, game_id)
    if row is None:
        return None, "", []
    game = {**dict(row), "moves": json.loads(row["moves_json"])}
    signature, repertoires, graphs, colors, card_positions = (
        _load_repertoire_index_snapshot(background=True)
    )
    matches, evidence_owner = prepare_game_comparisons(game, repertoires, graphs, colors, card_positions)
    game["decision_evidence_owner"] = evidence_owner
    return game, signature, matches


def _stage_match(database, game_id: str, version: int, index: int, match: dict) -> None:
    deviation = match["deviation"]
    storage.stage_match_values(
        database,
        (game_id, version, match["repertoire_id"], int(index == 0),
         match["classification"], match["matched"], match["opportunities"],
         match["deepest"], deviation["ply"] if deviation else None,
         deviation["fen"] if deviation else None,
         json.dumps(deviation["expected"] if deviation else []),
         deviation["actual"] if deviation else None, match["deviation_card_id"],
         match["opponent_gap"], match["out_of_book"],
         json.dumps(match["timeline"]), datetime.now(timezone.utc).isoformat(), match.get("canonical_prefix_revision", 0)),
    )


def _stage_primary_event(database, game: dict, version: int, primary: dict,
                         event: dict) -> None:
    event_id = hashlib.sha256(
        f"{game['id']}\0{primary['repertoire_id']}\0{event['ply']}".encode()
    ).hexdigest()
    storage.stage_decision_event_values(
        database,
        (event_id, game["id"], version, primary["repertoire_id"],
         event["card_id"], event["ply"], event["fen_key"],
         event["expected_uci"], event["actual_uci"], event["outcome"],
         game["played_at"], datetime.now(timezone.utc).isoformat()),
    )


def _publish_comparison(database, task: dict, game: dict, version: int,
                        matches: list[dict]) -> bool:
    game_id = game["id"]
    storage.record_repertoire_scope(database, game_id, game_scope_generation(database))
    primary = matches[0] if matches else None
    evidence_owner = game.get("decision_evidence_owner", primary)
    expected_events = len(evidence_owner["decision_events"]) if evidence_owner else 0
    saved_matches = storage.staged_match_count(database, game_id, version)
    saved_events = storage.staged_decision_event_count(database, game_id, version)
    if saved_matches != len(matches) or saved_events != expected_events:
        raise RuntimeError("Game repertoire comparison is incomplete; publication was withheld")
    deviation = primary["deviation"] if primary else None
    storage.stage_comparison_values(
        database,
        (game_id, version, primary["repertoire_id"] if primary else None,
         primary["classification"] if primary else "no applicable repertoire",
         deviation["ply"] if deviation else None,
         deviation["fen"] if deviation else None,
         json.dumps(deviation["expected"] if deviation else []),
         deviation["actual"] if deviation else None,
         datetime.now(timezone.utc).isoformat()),
    )
    storage.publish_repertoire_generation(database, game_id, version)
    enqueue_compact_postgres_task_in_transaction(
        database, "game_derivation_findings", game_id,
        {"game_id": game_id, "derivation_version": version,
         "phase": "stage", "cursor": 0}, priority=126,
    )
    return complete_task_slice_in_transaction(database, task)


def execute_game_repertoire_comparison_slice(task: dict[str, Any]) -> bool:
    payload = task["payload"]
    game_id = str(payload["game_id"])
    version = int(payload["derivation_version"])
    phase = str(payload.get("phase", "matches"))
    cursor = int(payload.get("cursor", 0))
    with background_read_connection() as database:
        scope_generation = game_scope_generation(database)
    game, signature, matches = _prepared_comparison(game_id)
    evidence_owner = game.get("decision_evidence_owner", matches[0] if matches else None) if game else None
    primary_events = evidence_owner["decision_events"] if evidence_owner else []
    with connection(background=True) as database:
        current_scope_generation = game_scope_generation(database, lock=True)
        if not lock_current_slice(database, task):
            return False
        job = lock_derivation_job(database, game_id)
        if (game is None or job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 1
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if (current_scope_generation != scope_generation
                or payload.get("game_scope_generation", scope_generation) != scope_generation
                or (expected_signature is not None and expected_signature != signature)):
            new_version = version + 1
            storage.restart_changed_source(database, game_id, version, new_version)
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_compare", game_id,
                {"game_id": game_id, "derivation_version": new_version,
                 "phase": "matches", "cursor": 0}, priority=127,
            )
            return True
        next_payload = {**payload, "source_signature": signature, "game_scope_generation": scope_generation}
        if phase == "matches":
            if cursor < len(matches):
                _stage_match(database, game_id, version, cursor, matches[cursor])
                return advance_task_slice_in_transaction(
                    database, task, next_phase="matches",
                    next_payload={**next_payload, "cursor": cursor + 1},
                )
            return advance_task_slice_in_transaction(
                database, task, next_phase="events",
                next_payload={**next_payload, "phase": "events", "cursor": 0},
            )
        if phase == "events":
            if cursor < len(primary_events):
                _stage_primary_event(database, game, version, evidence_owner, primary_events[cursor])
                return advance_task_slice_in_transaction(
                    database, task, next_phase="events",
                    next_payload={**next_payload, "cursor": cursor + 1},
                )
            return advance_task_slice_in_transaction(
                database, task, next_phase="publish",
                next_payload={**next_payload, "phase": "publish", "cursor": 0},
            )
        if phase == "publish":
            return _publish_comparison(database, task, game, version, matches)
        raise ValueError(f"Unknown game repertoire comparison phase: {phase}")
