"""Synthetic legal routes; no storage, provider, engine or Study runtime."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import chess
import pytest

from app.repertoire import build_index, position_key
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot, RouteBoundary
from tempo_evidence_math import opening_position_key
from tempo_game_review_contracts import AuthoredRoute, ReentryAuthority, RepertoireSnapshot as MembershipSnapshot


def route(route_id="r", moves=("e2e4", "e7e5", "g1f3"), **changes):
    return dict(repertoire_id="rep", route_id=route_id, learner_color="white",
        starting_fen=chess.STARTING_FEN, moves=list(moves)) | changes


def snapshot(*routes, variant="standard"):
    authored, boundaries, mappings = [], [], []
    for raw in routes:
        item = {key: value for key, value in raw.items() if key not in {"complete", "bindings", "variant"}}
        reentries = []
        for supplied in raw.get("bindings", ()):
            board = chess.Board(raw["starting_fen"])
            for move in raw["moves"][:supplied["ply"]]: board.push_uci(move)
            mappings.append(DecisionPresentation(
                decision_id=supplied["decision_id"], repertoire_id=raw["repertoire_id"],
                position_key=opening_position_key(board.fen()),
                selected_response_uci=raw["moves"][supplied["ply"]],
                presentation_id=supplied["presentation_id"], presentation_revision=supplied["card_revision"],
                repertoire_revision=1, introduction="unknown", introduced_at=supplied["introduced_at"],
                route_id=supplied.get("route_id"), route_ply=supplied.get("route_ply")))
            if supplied["reentry_authority_id"]:
                reentries.append(ReentryAuthority(ply=supplied["ply"], authority_id=supplied["reentry_authority_id"]))
        item["reentries"] = reentries
        item["policy_id"] = raw["repertoire_id"]
        authored.append(AuthoredRoute(**item))
        boundaries.append(RouteBoundary(route_id=raw["route_id"], terminal_ply=len(raw["moves"]),
                                        deliberate=raw.get("complete", True)))
    membership = MembershipSnapshot(workspace_id="fixture", snapshot_id="snapshot", revision=1,
        digest="a"*64, source_revision="source-1", captured_at="2026-10-26T12:00:00Z",
        scope_id="synthetic", scope_repertoire_ids=sorted({item["repertoire_id"] for item in routes}),
        complete=True, available=True, routes=authored)
    return RepertoireSnapshot(membership=membership, variant=variant, learner_color="white",
        boundaries=tuple(boundaries), decision_presentations=tuple(mappings))


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
    unsupported = build_index(snapshot(route(), variant="chess960"))
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
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN).binding.presentation_id == "card"
    source = snapshot(route(bindings=[binding()]), route("second", bindings=[binding()]))
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN) is None
    source = snapshot(route(bindings=[binding(reentry_authority_id=None)]))
    assert build_index(source).reentry_candidate("rep", chess.STARTING_FEN) is None


def test_index_does_not_alias_inputs():
    source = snapshot(route(bindings=[binding()]))
    before = deepcopy(source)
    index = build_index(source)
    assert source == before
    with pytest.raises(TypeError): source.membership.routes[0].moves[0] = "d2d4"
    assert index.routes["rep", "r"][0].route_moves is not source.membership.routes[0].moves
    assert index.routes["rep", "r"][0].binding is not source.decision_presentations[0]
    occurrence = index.routes["rep", "r"][0]
    assert occurrence.next_move == "e2e4" and occurrence.binding.presentation_id == "card"
    assert occurrence.binding.introduction == "unknown"
    with pytest.raises(TypeError): index.nodes[occurrence.key] = None
    with pytest.raises(FrozenInstanceError): occurrence.route_id = "changed"


def test_invalid_source_routes_are_diagnosed_deterministically():
    first, bad = route(), route("bad", starting_fen="invalid")
    index = build_index(snapshot(first, bad))
    assert not index.complete
    assert [item.code for item in index.diagnostics] == ["invalid_source_position"]
    assert build_index(snapshot(bad, first)).diagnostics == index.diagnostics


def test_route_scoped_binding_does_not_authorize_other_transposed_occurrence():
    index = build_index(snapshot(
        route("a", bindings=[binding(route_id="a", route_ply=0)]), route("b")))
    assert index.routes["rep", "a"][0].binding.presentation_id == "card"
    assert index.routes["rep", "b"][0].binding is None
    assert index.reentry_candidate("rep", chess.STARTING_FEN) is None
    assert any(item.route_id == "b" and item.code == "physical_mapping_unknown"
               for item in index.mapping_diagnostics)


@pytest.mark.parametrize("starting_fen,moves", [
    (chess.STARTING_FEN, ("e2e4", "a7a6", "e4e5", "d7d5", "e5d6", "a6a5", "e2e3")),
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 17 42", ("e1g1", "e8c8", "f1e1", "c8c6")),
    ("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 7 10", ("e1f1", "a8b8", "e5e6", "b8b6")),
])
def test_index_reuses_legal_replay_facts_without_reparsing_occurrences(
        monkeypatch, starting_fen, moves):
    """Legal/false EP, castling and illegal tails retain exact route evidence."""
    source = snapshot(
        route("a", moves, starting_fen=starting_fen,
              bindings=[binding(route_id="a", route_ply=0)]),
        route("alias", moves, starting_fen=starting_fen, complete=False))
    board = chess.Board(starting_fen)
    full_fens = [starting_fen]
    legal_moves = []
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        board.push(move)
        legal_moves.append(uci)
        full_fens.append(board.fen(en_passant="fen"))
    expected_keys = [position_key("rep", fen) for fen in full_fens]
    expected_turns = [chess.Board(fen).turn for fen in full_fens]
    parsed = []
    original_board = chess.Board

    class CountedBoard(original_board):
        def __init__(self, fen=chess.STARTING_FEN, **kwargs):
            parsed.append(fen)
            super().__init__(fen, **kwargs)

    monkeypatch.setattr(chess, "Board", CountedBoard)
    index = build_index(source)
    # Each route validates its root once; occurrence construction uses only
    # immutable facts captured from that same board after legal moves.
    assert parsed == [starting_fen, starting_fen]
    assert len(legal_moves) < len(moves)
    for route_id in ("a", "alias"):
        occurrences = index.routes["rep", route_id]
        assert [item.key for item in occurrences] == expected_keys
        assert [item.full_fen for item in occurrences] == full_fens
        assert [item.learner_turn for item in occurrences] == expected_turns
        assert all(item.route_id == route_id and item.starting_fen == starting_fen
                   and item.route_moves == tuple(legal_moves) and not item.terminal
                   for item in occurrences)
        assert [(item.ply, item.code) for item in index.diagnostics if item.route_id == route_id
                and item.code == "illegal_move"] == [(len(legal_moves), "illegal_move")]
    assert index.routes["rep", "a"][0].binding.presentation_id == "card"
    assert index.routes["rep", "a"][0].reentry_authority_id == "explicit-reentry"
    assert index.routes["rep", "alias"][0].binding is None
    assert index.routes["rep", "alias"][0].reentry_authority_id is None
    assert {(item.before.route_id, item.after.route_id) for item in index.adjacency} == {
        ("a", "a"), ("alias", "alias")}
    assert not index.complete


@pytest.mark.parametrize("failure", ["routes", "plies", "schema"])
def test_unanchored_reentry_index_preserves_full_source_bounds_and_revalidation(failure):
    from app.repertoire import build_reentry_index
    source = snapshot(route())
    supplied = source.membership.routes[0]
    if failure == "routes":
        routes = (supplied,) * 10001
    elif failure == "plies":
        routes = (supplied.model_copy(update={"moves": ("g1f3",) * 1000}),) * 101
    else:
        routes = (supplied.model_copy(update={"learner_color": "invalid"}),)
    invalid = source.model_copy(update={"membership": source.membership.model_copy(update={"routes": routes})})
    for factory in (build_index, build_reentry_index):
        with pytest.raises(ValueError, match="snapshot_index_exceeds_bound" if failure != "schema" else "learner_color"):
            factory(invalid)
    assert build_reentry_index(source) is None
