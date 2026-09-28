"""Recalculate a coverage node's blended reply projection in two SQL calls."""

from __future__ import annotations

import json

from ..postgres_store import PostgresConnection
from .repertoire_coverage import CoverageCandidate, blend_probabilities, required_reply_moves


def recalculate_coverage_node(
    database: PostgresConnection, node_id: str, settings: dict,
    explorer_games: int,
) -> None:
    candidates = database.execute_native(
        "SELECT move_uci,explorer_probability,maia_probability "
        "FROM repertoire_coverage_candidates WHERE node_id=%s", (node_id,),
    ).fetchall()
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
        database.execute_native(
            "UPDATE repertoire_coverage_candidates candidate SET "
            "blended_probability=updated.blended,required=updated.required,"
            "source_state=updated.source_state "
            "FROM jsonb_to_recordset(%s::jsonb) AS updated("
            "move_uci text,blended double precision,required bigint,source_state text) "
            "WHERE candidate.node_id=%s AND candidate.move_uci=updated.move_uci",
            (json.dumps(updates), node_id),
        )
