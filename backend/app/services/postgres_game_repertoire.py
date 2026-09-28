"""Versioned, restartable comparison of one imported game with repertoires."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ..database import background_read_connection, connection
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)
from .repertoire_comparison import (
    _compare_game_to_repertoire, _load_repertoire_index_snapshot,
)


def _prepared_comparison(game_id: str) -> tuple[dict | None, str, list[dict]]:
    with background_read_connection() as database:
        row = database.execute(
            "SELECT id,color,start_fen,moves_json,played_at FROM imported_games WHERE id=?",
            (game_id,),
        ).fetchone()
    if row is None:
        return None, "", []
    game = {**dict(row), "moves": json.loads(row["moves_json"])}
    signature, repertoires, graphs, colors, card_positions = (
        _load_repertoire_index_snapshot(background=True)
    )
    matches = [
        _compare_game_to_repertoire(
            game, repertoire, graphs.get(repertoire["id"], ({}, set())), card_positions,
        )
        for repertoire in repertoires
        if game["color"] in colors.get(repertoire["id"], set())
    ]
    matches.sort(key=lambda match: (
        -match["matched"], -match["deepest"], -match["is_main"], match["repertoire_id"],
    ))
    return game, signature, matches


def _stage_match(database, game_id: str, version: int, index: int, match: dict) -> None:
    deviation = match["deviation"]
    database.execute(
        """INSERT INTO game_repertoire_matches_staged(
             game_id,derivation_version,repertoire_id,is_primary,classification,
             matched_player_decisions,repertoire_opportunities,deepest_covered_ply,
             first_player_deviation_ply,first_player_deviation_fen,
             first_player_deviation_expected_json,first_player_deviation_actual_uci,
             deviation_card_id,first_opponent_gap_ply,out_of_book_ply,
             timeline_json,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version,repertoire_id) DO UPDATE SET
             is_primary=excluded.is_primary,classification=excluded.classification,
             matched_player_decisions=excluded.matched_player_decisions,
             repertoire_opportunities=excluded.repertoire_opportunities,
             deepest_covered_ply=excluded.deepest_covered_ply,
             first_player_deviation_ply=excluded.first_player_deviation_ply,
             first_player_deviation_fen=excluded.first_player_deviation_fen,
             first_player_deviation_expected_json=excluded.first_player_deviation_expected_json,
             first_player_deviation_actual_uci=excluded.first_player_deviation_actual_uci,
             deviation_card_id=excluded.deviation_card_id,
             first_opponent_gap_ply=excluded.first_opponent_gap_ply,
             out_of_book_ply=excluded.out_of_book_ply,
             timeline_json=excluded.timeline_json,updated_at=excluded.updated_at""",
        (game_id, version, match["repertoire_id"], int(index == 0),
         match["classification"], match["matched"], match["opportunities"],
         match["deepest"], deviation["ply"] if deviation else None,
         deviation["fen"] if deviation else None,
         json.dumps(deviation["expected"] if deviation else []),
         deviation["actual"] if deviation else None, match["deviation_card_id"],
         match["opponent_gap"], match["out_of_book"],
         json.dumps(match["timeline"]), datetime.now(timezone.utc).isoformat()),
    )


def _stage_primary_event(database, game: dict, version: int, primary: dict,
                         event: dict) -> None:
    event_id = hashlib.sha256(
        f"{game['id']}\0{primary['repertoire_id']}\0{event['ply']}".encode()
    ).hexdigest()
    database.execute(
        """INSERT INTO repertoire_decision_events_staged(
             id,game_id,derivation_version,repertoire_id,card_id,ply,fen_key,
             expected_uci,actual_uci,outcome,played_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version,id) DO UPDATE SET
             card_id=excluded.card_id,expected_uci=excluded.expected_uci,
             actual_uci=excluded.actual_uci,outcome=excluded.outcome,
             updated_at=excluded.updated_at""",
        (event_id, game["id"], version, primary["repertoire_id"],
         event["card_id"], event["ply"], event["fen_key"],
         event["expected_uci"], event["actual_uci"], event["outcome"],
         game["played_at"], datetime.now(timezone.utc).isoformat()),
    )


def _publish_comparison(database, task: dict, game: dict, version: int,
                        matches: list[dict]) -> bool:
    game_id = game["id"]
    primary = matches[0] if matches else None
    expected_events = len(primary["decision_events"]) if primary else 0
    saved_matches = database.execute(
        "SELECT COUNT(*) FROM game_repertoire_matches_staged "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]
    saved_events = database.execute(
        "SELECT COUNT(*) FROM repertoire_decision_events_staged "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]
    if saved_matches != len(matches) or saved_events != expected_events:
        raise RuntimeError("Game repertoire comparison is incomplete; publication was withheld")
    deviation = primary["deviation"] if primary else None
    database.execute(
        """INSERT INTO repertoire_comparisons_staged(
             game_id,derivation_version,repertoire_id,classification,
             divergence_ply,divergence_fen,expected_json,actual_uci,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version) DO UPDATE SET
             repertoire_id=excluded.repertoire_id,
             classification=excluded.classification,
             divergence_ply=excluded.divergence_ply,
             divergence_fen=excluded.divergence_fen,
             expected_json=excluded.expected_json,
             actual_uci=excluded.actual_uci,updated_at=excluded.updated_at""",
        (game_id, version, primary["repertoire_id"] if primary else None,
         primary["classification"] if primary else "no applicable repertoire",
         deviation["ply"] if deviation else None,
         deviation["fen"] if deviation else None,
         json.dumps(deviation["expected"] if deviation else []),
         deviation["actual"] if deviation else None,
         datetime.now(timezone.utc).isoformat()),
    )
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=2,"
        "phase='refreshing_findings',published_repertoire_version=? "
        "WHERE game_id=? AND derivation_version=?",
        (version, game_id, version),
    )
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
    game, signature, matches = _prepared_comparison(game_id)
    primary_events = matches[0]["decision_events"] if matches else []
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (game is None or job is None or int(job["derivation_version"]) != version
                or int(job["completed_phases"]) != 1
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        expected_signature = payload.get("source_signature")
        if expected_signature is not None and expected_signature != signature:
            new_version = version + 1
            database.execute(
                "UPDATE game_derivation_jobs SET derivation_version=?,"
                "phase='comparing_repertoire' WHERE game_id=? AND derivation_version=?",
                (new_version, game_id, version),
            )
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_compare", game_id,
                {"game_id": game_id, "derivation_version": new_version,
                 "phase": "matches", "cursor": 0}, priority=127,
            )
            return True
        next_payload = {**payload, "source_signature": signature}
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
                _stage_primary_event(database, game, version, matches[0], primary_events[cursor])
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
