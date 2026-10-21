"""Recalculate a coverage node's blended reply projection in two SQL calls."""

from __future__ import annotations

from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.controls import coverage as storage
from .repertoire_coverage import CoverageCandidate, blend_probabilities, required_reply_moves


def recalculate_coverage_node(
    database: PostgresConnection, node_id: str, settings: dict,
    explorer_games: int,
) -> None:
    candidates = storage.candidate_probability_rows(database, node_id)
    blended = {
        candidate["move_uci"]: blend_probabilities(
            candidate["explorer_probability"], candidate["maia_probability"],
            explorer_games=explorer_games if candidate["explorer_probability"] is not None else 0,
        ) for candidate in candidates
    }
    total = sum(probability for probability in blended.values() if probability is not None)
    normalized = {
        move_uci: probability / total if probability is not None and total else None
        for move_uci, probability in blended.items()
    }
    required = required_reply_moves(
        [CoverageCandidate(move_uci, probability) for move_uci, probability in normalized.items()
         if probability is not None],
        denominator=int(settings["reply_denominator"]),
        cumulative_target=float(settings["cumulative_target"]),
    )
    updates = [{
        "move_uci": candidate["move_uci"],
        "blended": normalized[candidate["move_uci"]],
        "required": int(candidate["move_uci"] in required),
        "source_state": "blended" if candidate["explorer_probability"] is not None
        and candidate["maia_probability"] is not None else "explorer-only"
        if candidate["explorer_probability"] is not None else "maia-only"
        if candidate["maia_probability"] is not None else "unknown",
    } for candidate in candidates]
    if updates:
        storage.save_candidate_projection(database, node_id, updates)
