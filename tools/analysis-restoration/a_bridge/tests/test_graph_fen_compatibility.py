"""Synthetic clock compatibility; no observed FENs, identities or graph records."""

from copy import deepcopy

import chess
import pytest

from tempo_a_bridge.graph_fen_compatibility import recorded_graph_starting_fen


ERROR = "recorded_graph_starting_fen_incompatible"
POSITION = " ".join(chess.STARTING_FEN.split()[:4])


def assert_rejected(source, graph):
    with pytest.raises(ValueError) as error:
        recorded_graph_starting_fen(source, graph)
    assert str(error.value) == ERROR


def test_same_counters_preserve_recorded_source_fen():
    assert recorded_graph_starting_fen(chess.STARTING_FEN, chess.STARTING_FEN) == chess.STARTING_FEN


@pytest.mark.parametrize("graph_clocks", ["0 1", "7 23", "99 120", "000 001"])
def test_only_clock_differences_return_exact_recorded_source(graph_clocks):
    source = POSITION + " 12 42"
    assert recorded_graph_starting_fen(source, POSITION + " " + graph_clocks) == source


def test_raw_source_spacing_and_counters_remain_unchanged():
    source = "  " + POSITION.replace(" ", "  ") + "  007  023  "
    graph = POSITION + " 0 1"
    inputs = [source, graph]
    original = deepcopy(inputs)
    assert recorded_graph_starting_fen(*inputs) == source
    assert inputs == original


def test_repeated_occurrences_with_distinct_clocks_have_one_stable_definition():
    source = POSITION + " 5 17"
    occurrences = [POSITION + " " + clocks for clocks in ("0 1", "1 2", "14 73")]
    original = deepcopy(occurrences)
    assert {recorded_graph_starting_fen(source, fen) for fen in occurrences} == {source}
    assert occurrences == original


def test_real_board_change_remains_incompatible():
    board = chess.Board()
    board.push_uci("e2e4")
    assert_rejected(chess.STARTING_FEN, board.fen(en_passant="fen"))


def test_turn_change_remains_incompatible():
    assert_rejected(chess.STARTING_FEN, chess.STARTING_FEN.replace(" w ", " b "))


def test_castling_rights_change_remains_incompatible():
    assert_rejected(chess.STARTING_FEN, chess.STARTING_FEN.replace(" KQkq ", " KQ "))


def test_legal_en_passant_change_remains_incompatible():
    board = chess.Board()
    for move in ("e2e4", "a7a6", "e4e5", "d7d5"):
        board.push_uci(move)
    source = board.fen(en_passant="fen")
    assert board.has_legal_en_passant()
    fields = source.split()
    fields[3] = "-"
    assert_rejected(source, " ".join(fields))


def test_even_non_capturable_raw_ep_change_is_outside_clock_only_scope():
    board = chess.Board()
    board.push_uci("e2e4")
    source = board.fen(en_passant="fen")
    assert not board.has_legal_en_passant()
    assert_rejected(source, board.fen(en_passant="legal"))


@pytest.mark.parametrize("clocks", ["-1 1", "0 0", "0 -1", "1.5 1", "+1 1", "NaN 1", "1 1.0"])
def test_invalid_clocks_in_either_record_are_rejected_with_fixed_error(clocks):
    invalid = POSITION + " " + clocks
    assert_rejected(invalid, chess.STARTING_FEN)
    assert_rejected(chess.STARTING_FEN, invalid)


@pytest.mark.parametrize("invalid", [None, True, 1, {}, [], "", POSITION,
                                    "8/8/8/8/8/8/8/8 w - - 0 1",
                                    chess.STARTING_FEN.replace(" w ", " x "),
                                    chess.STARTING_FEN.replace(" - ", " e9 ")])
def test_invalid_or_incomplete_fen_cannot_pass_even_when_both_inputs_match(invalid):
    assert_rejected(invalid, invalid)


def test_error_does_not_include_supplied_invalid_values():
    assert_rejected("synthetic-private-invalid-source", "synthetic-private-invalid-graph")
