"""Issue 9 and 10 regressions for defensive evidence and real anchors."""

import chess
import pytest

from app.services.threat_detection import (
    defensive_fork_geometry, find_defensive_knight_forks,
    propose_exercise_anchors, trace_knight_route,
)
from app.services.threat_models import GameSnapshot, SourceLine, ThreatPolicy


FORK_FEN = "4k3/8/8/8/1n6/8/8/R3K3 b Q - 0 1"


def test_issue9_checking_knight_fork_targets_king_and_rook():
    geometry = defensive_fork_geometry(chess.Board(FORK_FEN), "b4c2")
    assert geometry is not None
    assert (geometry.attacker_color, geometry.knight_from, geometry.knight_to) == (
        "black", "b4", "c2",
    )
    assert (geometry.king.square, geometry.major.square, geometry.major.piece) == (
        "e1", "a1", "rook",
    )


def test_issue9_checking_knight_fork_targets_queen_without_king_value():
    geometry = defensive_fork_geometry(
        chess.Board(FORK_FEN.replace("R3K3", "Q3K3")), "b4c2"
    )
    assert geometry is not None and geometry.major.piece == "queen"
    assert not hasattr(geometry.king, "value_cp")


def test_issue9_records_every_attacked_major_square_and_piece():
    board = chess.Board("4k3/8/8/8/1n1Q4/8/8/R3K3 b Q - 0 1")
    geometry = defensive_fork_geometry(board, "b4c2")
    assert geometry is not None
    assert {(target.square, target.piece) for target in
            (geometry.major, *geometry.other_majors)} == {
        ("a1", "rook"), ("d4", "queen"),
    }


def test_issue9_king_only_and_minor_only_are_not_defensive_major_forks():
    assert defensive_fork_geometry(
        chess.Board(FORK_FEN.replace("R3K3", "4K3")), "b4c2"
    ) is None
    assert defensive_fork_geometry(
        chess.Board(FORK_FEN.replace("R3K3", "B3K3")), "b4c2"
    ) is None


def test_issue9_illegal_engine_move_is_rejected_as_evidence():
    game = GameSnapshot("game", 1, FORK_FEN, (), "white")
    with pytest.raises(ValueError, match="Illegal"):
        find_defensive_knight_forks(game, SourceLine("engine", 0, ("b4b5",)))


def test_issue9_route_follows_actual_knight_when_two_can_reach_fork_square():
    fen = "4k3/8/8/8/1n6/4n3/8/R3K3 b Q - 0 1"
    game = GameSnapshot("game", 1, fen, ("e3c2",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("played", 0, game.moves_uci)
    )[0]
    assert [(hop.from_square, hop.to_square) for hop in trace_knight_route(game, seed)] == [
        ("e3", "c2")
    ]


def test_issue9_source_origin_is_retained_for_played_and_engine_lines():
    game = GameSnapshot("game", 1, FORK_FEN, ("b4c2",), "white")
    played = find_defensive_knight_forks(game, SourceLine("played", 0, ("b4c2",)))[0]
    engine = find_defensive_knight_forks(game, SourceLine("engine", 0, ("b4c2",)))[0]
    assert played.source_line.origin == "played"
    assert engine.source_line.origin == "engine"
    assert trace_knight_route(game, engine)[0].origin == "engine"


def test_issue9_fork_evidence_excludes_unrelated_pv_suffix():
    game = GameSnapshot("game", 1, FORK_FEN, (), "white")
    short = find_defensive_knight_forks(
        game, SourceLine("engine", 0, ("b4c2",))
    )[0]
    longer = find_defensive_knight_forks(
        game, SourceLine("engine", 0, ("b4c2", "e1d2"))
    )[0]
    assert short == longer


def test_issue10_played_fork_anchors_only_real_preceding_learner_decisions():
    fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    game = GameSnapshot("game", 1, fen, ("a2a3", "b4c2"), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("played", 0, game.moves_uci)
    )[0]
    anchors = propose_exercise_anchors(game, seed, ThreatPolicy())
    assert [(item.player_ply, item.historical_move_uci) for item in anchors] == [
        (0, "a2a3")
    ]
    assert anchors[0].position.prefix_uci == ()


def test_issue10_engine_fork_excludes_hypothetical_future_learner_turns():
    fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    game = GameSnapshot("game", 1, fen, ("a2a3",), "white")
    seed = find_defensive_knight_forks(
        game, SourceLine("engine", 1, ("b4c2", "e1e2", "c2d4"))
    )[0]
    anchors = propose_exercise_anchors(game, seed, ThreatPolicy())
    assert [item.player_ply for item in anchors] == [0]


def test_issue10_black_learner_nonstandard_fen_uses_board_turn():
    fen = "4k3/8/8/8/1N6/8/8/R3K3 b Q - 0 1"
    game = GameSnapshot("game", 1, fen, ("e8e7",), "black")
    # Anchor selection is independent of a later fork's geometry.
    from app.services.threat_models import ForkGeometry, ForkTarget, ThreatSeed
    seed = ThreatSeed("seed", "game", 1, SourceLine("engine", 1, ("b4c2",)), 0,
                      ForkGeometry("white", "b4", "c2", ForkTarget("e8", "king"),
                                   ForkTarget("a8", "rook")))
    anchors = propose_exercise_anchors(game, seed, ThreatPolicy())
    assert [(anchor.player_ply, anchor.historical_move_uci) for anchor in anchors] == [
        (0, "e8e7")
    ]


def test_issue10_one_fork_incident_has_at_most_three_real_prior_decisions():
    fen = "4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1"
    moves = ("a2a3", "e8e7", "a3a4", "e7e8", "a4a5", "b4c2")
    game = GameSnapshot("game", 1, fen, moves, "white")
    seed = find_defensive_knight_forks(game, SourceLine("played", 0, moves))[0]
    anchors = propose_exercise_anchors(game, seed, ThreatPolicy())
    assert [(anchor.player_ply, anchor.historical_move_uci,
             anchor.decisions_before_event) for anchor in anchors] == [
        (4, "a4a5", 0), (2, "a3a4", 1), (0, "a2a3", 2),
    ]
    assert {anchor.seed_id for anchor in anchors} == {seed.seed_id}
