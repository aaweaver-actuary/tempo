"""Bounded position discovery before unchanged complete authority capture.

Discovery never chooses an alternative or authorizes an effect. Each retained
observation still has the full source traversal/proof for one explicit ply.
"""
from dataclasses import dataclass
import json

from tempo_learning_remediation import RemediationError
from tempo_legacy_database.adapters.games.authority import headers
from tempo_legacy_database.adapters.games.authority.candidate_lookup import (
    CandidateOverflow, decision_bindings,
)

from app.legacy_game_observation import capture_game
from . import legacy_game_authority as owner


@dataclass(frozen=True)
class RetainedCaptureSlice:
    game: dict
    positions: int
    captures: tuple[tuple[int, dict], ...]
    unresolved: tuple[tuple[int, str], ...]
    remaining_positions: int


def capture_history_positions(repertoire_id, game_id, *, start_ply, maximum_plies,
                              reader=None, metadata=None, capture=None):
    """Replay one bounded interval, retaining every alternative at each lead.

    Overflow at an early position is unresolved evidence for that position;
    later positions can still receive their own complete scoped capture.
    All source/header/generation checks must agree across the returned slice.
    """
    if (type(start_ply) is not int or not 0 <= start_ply <= 1000
            or type(maximum_plies) is not int or not 1 <= maximum_plies <= 16):
        raise ValueError("explicit_bounded_history_ply_interval_required")
    reader, metadata = reader or owner.read_rows, metadata or owner.metadata
    rows = headers.imported_game(reader, game_id)
    if len(rows) != 1:
        raise ValueError("one_explicit_existing_game_required")
    game = dict(rows[0])
    owner.completed_game_time(game)
    moves = json.loads(game["moves_json"])
    if not isinstance(moves, list) or not 1 <= len(moves) <= 1000:
        raise ValueError("retained_history_move_bound_invalid")
    frames = owner.positions(game["start_fen"], moves)
    before = metadata(repertoire_id)
    if before.get("graph_state") != "ready" or not before.get("integrity_current"):
        raise ValueError("legacy_completed_current_integrity_required")
    end = min(len(moves), start_ply + maximum_plies)
    captures, unresolved = [], []
    for ply in range(min(start_ply, len(moves)), end):
        if frames[ply].turn != (game["color"] == "white"):
            continue
        try:
            bindings = decision_bindings(reader, repertoire_id, before["graph_generation"],
                                         owner.comparison_key(frames[ply]))
            if not bindings:
                unresolved.append((ply, "retained_position_without_published_binding"))
                continue
            # No status/chronology/alternative filtering: every lead goes to
            # complete capture, including missing/blocked/conflicting bindings.
            captured = (capture(repertoire_id, game_id, (ply,)) if capture is not None
                else capture_game(repertoire_id, game_id, (ply,), reader=reader, metadata=metadata))
            if (captured["game"] != game
                    or captured["catalogue_scope"]["scope"]["decision_plies"] != [ply]):
                raise ValueError("retained_history_discovery_source_changed")
            captures.append((ply, captured))
        except (CandidateOverflow, RemediationError) as error:
            unresolved.append((ply, getattr(error, "reason", None) or str(error)))
        if metadata(repertoire_id) != before:
            raise ValueError("legacy_source_changed_during_capture")
    if metadata(repertoire_id) != before or headers.imported_game(reader, game_id) != rows:
        raise ValueError("retained_history_discovery_source_changed")
    return RetainedCaptureSlice(game, max(0, end-start_ply), tuple(captures), tuple(unresolved),
                                max(0, len(moves)-end))
