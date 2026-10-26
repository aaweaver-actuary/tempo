"""Synthetic legal routes; no storage, provider, engine or Study runtime."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import chess
import pytest

from app.repertoire import build_index, position_key


def route(route_id="r", moves=("e2e4", "e7e5", "g1f3"), **changes):
    return dict(repertoire_id="rep", route_id=route_id, learner_color="white",
        starting_fen=chess.STARTING_FEN, moves=list(moves)) | changes


def snapshot(*routes):
    return dict(snapshot_id="snapshot", source_revision="source-1", complete=True,
                available=True, routes=list(routes))


def binding(card="card", **changes):
    value = dict(ply=0, decision_id="decision", presentation_id=card, card_id=card,
        card_revision=3, introduced_at=None, introduced_before_game=None,
        reentry_authority_id="explicit-reentry")
    value.update(changes)
    return value


def test_clocks_do_not_split_position_identity():
    later = chess.STARTING_FEN.rsplit(" ", 2)[0] + " 37 42"
    assert position_key("rep", later) == position_key("rep", chess.STARTING_FEN)
    index = build_index(snapshot(route(), route("other", starting_fen=later)))
    node = index.lookup("rep", later)
    assert len(node.occurrences) == 2
    assert next(item for item in node.occurrences if item.route_id == "other").full_fen == later


def test_turn_castling_legal_ep_variant_do_split_identity():
    start = chess.STARTING_FEN
    assert position_key("rep", start) != position_key("rep", start.replace(" w ", " b "))
    assert position_key("rep", start) != position_key("rep", start.replace("KQkq", "-"))
    board = chess.Board()
    for move in ("e2e4", "a7a6", "e4e5", "d7d5"):
        board.push_uci(move)
    fen = board.fen(en_passant="fen")
    assert position_key("rep", fen) != position_key("rep", fen.replace("d6", "-"))
    # Irrelevant EP does not split identity.
    board = chess.Board(); board.push_uci("e2e4")
    assert position_key("rep", board.fen(en_passant="fen")) == position_key("rep", board.fen())
    with pytest.raises(ValueError, match="unsupported_variant"):
        position_key("rep", start, "chess960")
    unsupported = build_index(snapshot(route(variant="chess960")))
    assert not unsupported.nodes and unsupported.diagnostics[0].code == "unsupported_variant"


def test_transposed_occurrences_preserve_routes():
    first = ("g1f3", "d7d5", "g2g3", "c7c5", "f1g2")
    second = ("g2g3", "c7c5", "g1f3", "d7d5", "f1g2")
    index = build_index(snapshot(route("a", first), route("b", second)))
    board = chess.Board()
    for move in first[:4]: board.push_uci(move)
    node = index.lookup("rep", board.fen())
    assert [(item.route_id, item.ply) for item in node.occurrences] == [("a", 4), ("b", 4)]
    assert node.occurrences[0].history != node.occurrences[1].history
    assert node.selected_response == "f1g2"
    assert {(item.before.route_id, item.after.route_id) for item in index.adjacency} == {("a", "a"), ("b", "b")}
    assert index.adjacency[0].intervening_moves == first[:2]


def test_one_repertoire_conflict_other_repertoire_allowed():
    other = route("c", ("d2d4",)); other["repertoire_id"] = "other"
    index = build_index(snapshot(route("a", ("e2e4",)), route("b", ("d2d4",)), other))
    node = index.lookup("rep", chess.STARTING_FEN)
    assert node.conflict and node.selected_response is None
    assert node.own_responses == ("d2d4", "e2e4")
    assert index.lookup("other", chess.STARTING_FEN).selected_response == "d2d4"
    assert {item.route_id for item in index.diagnostics} == {"a", "b"}


def test_opponent_fork_is_not_own_response_conflict():
    index = build_index(snapshot(route("a", ("e2e4", "e7e5")), route("b", ("e2e4", "c7c5"))))
    board = chess.Board(); board.push_uci("e2e4")
    node = index.lookup("rep", board.fen())
    assert node.opponent_edges == ("c7c5", "e7e5") and not node.conflict
    assert index.complete


def test_terminal_and_incomplete_routes_remain_explicit():
    index = build_index(snapshot(route("end", ()), route("partial", ("e2e4",), complete=False)))
    assert index.routes["rep", "end"][0].terminal
    assert not index.routes["rep", "partial"][-1].terminal
    assert not index.complete
    assert index.diagnostics[0].code == "incomplete_route"
    assert build_index(snapshot(route("end", ()))).lookup("rep", chess.STARTING_FEN).selected_response is None


def test_illegal_tail_reports_diagnostic():
    index = build_index(snapshot(route(moves=("e2e4", "e7e5", "e2e3"))))
    assert not index.complete
    assert [(item.ply, item.code) for item in index.diagnostics] == [(2, "illegal_move")]
    assert len(index.routes["rep", "r"]) == 3
    assert not index.routes["rep", "r"][-1].terminal


def test_ambiguous_physical_mapping_not_guessed():
    index = build_index(snapshot(route(bindings=[binding(), binding("different")])) )
    occurrence = index.routes["rep", "r"][0]
    assert occurrence.binding is None
    assert index.reentry_candidate("rep", chess.STARTING_FEN) is None
    assert index.diagnostics[0].code == "ambiguous_physical_mapping"


def test_reentry_requires_explicit_single_route_and_mapping():
    source = snapshot(route(bindings=[binding()]))
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN).binding.card_id == "card"
    source["routes"].append(route("second", bindings=[binding()]))
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN) is None
    source["routes"] = [route(bindings=[binding(reentry_authority_id=None)])]
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN) is None


def test_index_does_not_alias_inputs():
    source = snapshot(route(bindings=[binding()]))
    before = deepcopy(source)
    index = build_index(source)
    assert source == before
    source["routes"][0]["moves"][0] = "d2d4"
    source["routes"][0]["bindings"][0]["card_id"] = "changed"
    occurrence = index.routes["rep", "r"][0]
    assert occurrence.next_move == "e2e4" and occurrence.binding.card_id == "card"
    assert occurrence.binding.introduced_before_game is None
    with pytest.raises(TypeError): index.nodes[occurrence.key] = None
    with pytest.raises(FrozenInstanceError): occurrence.route_id = "changed"


def test_invalid_and_duplicate_source_routes_are_diagnosed_deterministically():
    source = snapshot(route(), route(), route("bad", starting_fen="invalid"))
    index = build_index(source)
    assert not index.complete
    assert [item.code for item in index.diagnostics] == ["invalid_source_position", "duplicate_route"]
    assert build_index(snapshot(*reversed(source["routes"]))).diagnostics == index.diagnostics
