"""Task-owned legal positions; actual #483 index, no runtime dependencies."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys

import chess
import pytest

from app.analysis.similarity.retrieval import RetrievalRequest, retrieve, scan_outliers
from app.contracts.product_v02.repertoire import RepertoireSnapshot, RouteBoundary
from app.repertoire import build_index
from tempo_evidence_math import opening_position_key
from tempo_game_review_contracts import AuthoredRoute, RepertoireSnapshot as MembershipSnapshot


def shifted(*moves, fen=chess.STARTING_FEN):
    board = chess.Board(fen)
    for start, end in moves:
        piece = board.remove_piece_at(chess.parse_square(start))
        board.set_piece_at(chess.parse_square(end), piece)
    assert board.is_valid()
    return board.fen(en_passant="fen")


def route(name, fen=chess.STARTING_FEN, move="e2e4", *, learner="white", repertoire="rep", moves=None):
    return AuthoredRoute(repertoire_id=repertoire, route_id=name, policy_id=repertoire,
        learner_color=learner, starting_fen=fen, moves=list(moves if moves is not None else (move,)))


def source(*routes, available=True):
    membership = MembershipSnapshot(workspace_id="similarity-fixture", snapshot_id="immutable-snapshot",
        revision=1, digest="a" * 64, source_revision="synthetic-1", captured_at="2026-10-26T12:00:00Z",
        scope_id="whole-synthetic-catalogue", scope_repertoire_ids=sorted({item.repertoire_id for item in routes}),
        complete=True, available=available, routes=list(routes))
    return RepertoireSnapshot(membership=membership, variant="standard", learner_color="white",
        boundaries=tuple(RouteBoundary(route_id=item.route_id, terminal_ply=len(item.moves), deliberate=True)
                         for item in routes), decision_presentations=())


def request(fen=chess.STARTING_FEN, **kwargs):
    return RetrievalRequest("rep", fen, "white", **kwargs)


def test_exact_continuation_precedes_approximate_neighbours():
    index = build_index(source(route("exact", move="d2d4"),
        route("a", shifted(("a7", "a6"))), route("b", shifted(("b7", "b6")))))
    result = retrieve(index, request())
    assert result.exact.selected_response_uci == "d2d4"
    assert [item.move_uci for item in result.candidates] == ["d2d4", "e2e4"]
    assert result.candidates[0].exact_continuation
    assert result.candidates[1].distinct_support == 2
    assert len(result.neighbours) == 2


def test_similar_position_does_not_become_same_identity():
    fen = shifted(("b8", "c6"))
    result = retrieve(build_index(source(route("similar", fen))), request())
    assert result.exact is None
    assert result.neighbours[0].position_key != result.target_position_key
    assert result.neighbours[0].comparison.differences
    assert not result.candidates[0].exact_continuation
    assert opening_position_key(fen) != opening_position_key(chess.STARTING_FEN)


def test_duplicate_routes_do_not_inflate_support():
    first = ("g1f3", "d7d5", "g2g3", "c7c5", "f1g2")
    second = ("g2g3", "c7c5", "g1f3", "d7d5", "f1g2")
    index = build_index(source(route("transposed-a", moves=first), route("transposed-b", moves=second)))
    target = chess.Board()
    for move in first[:4]: target.push_uci(move)
    target.push_uci("f1g2"); target.push_uci("b8c6")
    # Query from the same pawn structure with the bishop back at f1, enemy Nc6.
    target.set_piece_at(chess.F1, target.remove_piece_at(chess.G2))
    result = retrieve(index, request(target.fen()))
    candidate = next(item for item in result.candidates if item.move_uci == "f1g2")
    assert candidate.distinct_support == 1
    assert len(candidate.supporting_positions[0].occurrences) == 2
    assert {item.route_id for item in candidate.supporting_positions[0].occurrences} == {"transposed-a", "transposed-b"}
    # Even identical source positions in separate repertoires are one support.
    duplicate = shifted(("a7", "a6"))
    result = retrieve(build_index(source(route("x", duplicate), route("y", duplicate, repertoire="other"))), request())
    assert result.candidates[0].distinct_support == 1
    assert len(result.neighbours[0].occurrences) == 2


def test_illegal_transferred_move_is_excluded():
    source_fen = "4k3/8/8/8/8/8/8/4K1N1 w - - 0 1"
    target_fen = "4k3/8/8/8/8/8/7N/4K3 w - - 0 1"
    assert chess.Board(source_fen).san(chess.Move.from_uci("g1f3")) == "Nf3"
    assert chess.Board(target_fen).san(chess.Move.from_uci("h2f3")) == "Nf3"
    result = retrieve(build_index(source(route("same-san", source_fen, "g1f3"))), request(target_fen))
    assert result.status == "empty_neighbourhood" and not result.candidates


def test_turn_variant_and_material_filters():
    source_fen = shifted(("b8", "c6"))
    index = build_index(source(route("similar", source_fen)))
    assert retrieve(index, request(variant="chess960")).status == "unsupported_variant"
    assert retrieve(index, request(chess.STARTING_FEN.replace(" w ", " b "))).status == "not_learner_turn"
    board = chess.Board(); board.remove_piece_at(chess.A2)
    assert not retrieve(index, request(board.fen())).neighbours
    board = chess.Board(); board.push_uci("e2e4")
    black_index = build_index(source(route("black", board.fen(), "e7e5", learner="black")))
    assert not retrieve(black_index, request()).candidates
    # Same turn/material, but the learner pawn structure is outside this feature policy.
    assert not retrieve(index, request(shifted(("a2", "a3")))).neighbours


def test_castling_and_ep_differences_explained():
    index = build_index(source(route("rights", chess.STARTING_FEN.replace("KQkq", "-"))))
    result = retrieve(index, request())
    assert result.exact is None
    assert any("castling rights" in item for item in result.neighbours[0].comparison.differences)
    board = chess.Board()
    for move in ("e2e4", "a7a6", "e4e5", "d7d5"): board.push_uci(move)
    fen = board.fen(en_passant="fen")
    index = build_index(source(route("ep", fen.replace("d6", "-"), "g1f3")))
    result = retrieve(index, request(fen))
    assert result.exact is None
    assert any("en passant" in item for item in result.neighbours[0].comparison.differences)
    ep_index = build_index(source(route("ep-capture", fen, "e5d6")))
    assert not retrieve(ep_index, request(fen.replace("d6", "-"))).candidates


def test_familiar_candidate_not_limited_to_engine_list():
    fixture = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures/proposal.json"
    explicit = json.loads(fixture.read_text())["payload"]["engine_evidence"][0]["requested_candidates"]
    routes = [route(str(i), shifted((f"{file}7", f"{file}6")), move)
              for i, (file, move) in enumerate(zip("abcdefg", explicit))]
    result = retrieve(build_index(source(*routes)), request())
    assert {item.move_uci for item in result.candidates} == set(explicit)
    assert len(result.candidates) == 7 and "a2a3" in explicit[5:]
    assert not result.truncation


def test_covered_outlier_has_distinct_support():
    index = build_index(source(route("unusual", move="d2d4"),
        route("a", shifted(("a7", "a6"))), route("b", shifted(("b7", "b6"))),
        route("copy", shifted(("a7", "a6")))))
    result = scan_outliers(index, request())
    item = next(item for item in result.outliers if item.position_key == opening_position_key(chess.STARTING_FEN))
    assert item.current_response_uci == "d2d4" and item.current_neighbour_support == 0
    assert item.alternatives[0].move_uci == "e2e4" and item.alternatives[0].distinct_support == 2
    assert item.alternatives[0].evaluation_status == "not_evaluated"
    # Duplicate copies alone cannot establish an outlier.
    copies = build_index(source(route("unusual", move="d2d4"),
        route("a", shifted(("a7", "a6"))), route("b", shifted(("a7", "a6")))))
    assert not scan_outliers(copies, request()).outliers


def test_empty_neighbourhood_is_explicit():
    empty = build_index(source(route("terminal", moves=())))
    result = retrieve(empty, request())
    assert result.status == "empty_neighbourhood"
    assert result.exact_reason == "no_usable_exact_continuation"
    assert result.neighbours == result.candidates == ()
    unavailable = build_index(source(route("a"), available=False))
    assert retrieve(unavailable, request()).status == "source_unavailable"
    assert scan_outliers(unavailable, request()).status == "source_unavailable"


def test_deterministic_bounded_output_and_unchanged_inputs():
    routes = [route(str(i), shifted((f"{file}7", f"{file}6")), move)
              for i, (file, move) in enumerate(zip("abcdef", ("e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "g2g3")))]
    value = source(*routes)
    before = value.model_dump_json()
    index = build_index(value)
    index_before = repr(index)
    req = request(max_nodes=4, max_neighbours=1, max_candidates=1)
    result = retrieve(index, req)
    assert result == retrieve(index, req) == retrieve(build_index(source(*reversed(routes))), req)
    assert result.scanned_nodes == 4 and len(result.neighbours) == len(result.candidates) == 1
    assert {"nodes", "neighbours"} <= set(result.truncation)
    assert value.model_dump_json() == before
    assert repr(index) == index_before
    with pytest.raises(FrozenInstanceError): result.status = "changed"
    with pytest.raises(FrozenInstanceError): result.neighbours[0].occurrences[0].route_id = "changed"
    scan = scan_outliers(index, request(), max_targets=3, max_comparison_nodes=1)
    assert scan.comparison_nodes == 1 and "comparison_nodes" in scan.truncation
    for field in ("max_nodes", "max_neighbours", "max_candidates", "max_occurrences", "max_provenance"):
        with pytest.raises(ValueError): replace(req, **{field: 0})


def test_conflicting_and_incomplete_sources_never_select_a_winner():
    value = source(route("a", move="d2d4"), route("b"), route("familiar", shifted(("a7", "a6"))))
    result = retrieve(build_index(value), request())
    assert result.exact is None and result.exact_reason == "conflicting_own_responses"
    assert result.candidates[0].move_uci == "e2e4" and not result.candidates[0].exact_continuation
    assert not result.source_complete
    incomplete = source(route("partial", shifted(("a7", "a6"))))
    incomplete = incomplete.model_copy(update={"boundaries":
        (RouteBoundary(route_id="partial", terminal_ply=1, deliberate=False),)})
    result = retrieve(build_index(incomplete), request())
    assert result.status == "empty_neighbourhood" and not result.source_complete


def test_provenance_and_candidate_limits_are_explicit():
    duplicate = shifted(("a7", "a6"))
    index = build_index(source(route("a", duplicate), route("b", duplicate), route("c", duplicate),
        route("other", shifted(("b7", "b6")), "d2d4")))
    result = retrieve(index, request(max_candidates=1, max_provenance=1))
    assert result.legal_candidate_count == 2 and len(result.candidates) == 1
    assert {"candidates", "provenance"} <= set(result.truncation)
    support = next(item for item in result.neighbours if item.selected_response_uci == "e2e4")
    assert len(support.occurrences) == 1 and support.provenance_truncated
    assert result.candidates[0].distinct_support == 1  # Copies cannot win a lexical tie.
    # The occurrence scan cap is reported even when it hides the only complete route.
    value = source(route("a", duplicate), route("b", duplicate))
    value = value.model_copy(update={"boundaries": (RouteBoundary(route_id="a", terminal_ply=1, deliberate=False),
        RouteBoundary(route_id="b", terminal_ply=1, deliberate=True))})
    limited = retrieve(build_index(value), request(max_occurrences=1))
    assert limited.status == "empty_neighbourhood" and "provenance" in limited.truncation


def test_exact_clocks_and_irrelevant_ep_keep_full_context():
    board = chess.Board(); board.push_uci("e2e4")
    source_fen = board.fen(en_passant="fen")
    target_fen = board.fen().rsplit(" ", 2)[0] + " 19 50"
    index = build_index(source(route("black-exact", source_fen, "e7e5", learner="black")))
    result = retrieve(index, RetrievalRequest("rep", target_fen, "black"))
    assert result.exact.full_fen == source_fen
    assert not result.neighbours and result.exact.position_key == result.target_position_key
    assert result.candidates[0].target_san == "e5"
    assert result.exact.occurrences[0].full_fen.endswith("e3 0 1")
    assert any("en passant" in difference for difference in result.exact.comparison.differences)
    black_source = shifted(("b1", "c3"), fen=board.fen())
    approximate = retrieve(build_index(source(route("black-similar", black_source, "e7e5", learner="black"))),
        RetrievalRequest("rep", board.fen(), "black"))
    assert approximate.exact is None and approximate.candidates[0].move_uci == "e7e5"


def test_check_context_and_last_target_comparison_budget_are_visible():
    source_fen = "r6k/8/8/8/8/8/8/3QK1N1 w - - 0 1"
    target_fen = "4r2k/8/8/8/8/8/8/3QK1N1 w - - 0 1"
    result = retrieve(build_index(source(route("check-context", source_fen, "e1f1"))), request(target_fen))
    assert any("king attack/check" in difference for difference in result.neighbours[0].comparison.differences)
    # One covered target, multiple index nodes: even the last target cannot hide budget truncation.
    index = build_index(source(route("target")))
    scan = scan_outliers(index, request(), max_targets=1, max_comparison_nodes=1)
    assert scan.examined_positions == 1 and scan.comparison_nodes == 1
    assert {"neighbourhoods", "comparison_nodes"} <= set(scan.truncation)
    assert scan.max_comparison_nodes == 1 and scan.request == request()


def test_tactically_different_neighbour_is_never_marked_sound():
    source_fen = "r6k/8/8/8/8/8/8/3QK1N1 w - - 0 1"
    target_fen = "3r3k/8/8/8/8/8/8/3QK1N1 w - - 0 1"
    result = retrieve(build_index(source(route("analogy", source_fen, "d1d2"))), request(target_fen))
    candidate = result.candidates[0]
    assert candidate.evaluation_status == "not_evaluated"
    assert "tactical quality not evaluated" in candidate.reason
    assert result.neighbours[0].comparison.differences
    board = chess.Board(target_fen); board.push_uci(candidate.move_uci)
    assert chess.Move.from_uci("d8d2") in board.legal_moves  # Queen can be lost in the target.


def test_module_imports_without_database_http_or_engine():
    script = """
import importlib.abc, sys
class DenyRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('app.repertoire', 'app.services', 'app.main', 'app.games',
            'app.study', 'app.product_composition', 'tempo_legacy_database', 'psycopg',
            'httpx', 'requests', 'redis', 'chess.engine', 'stockfish')):
            raise AssertionError(fullname)
sys.meta_path.insert(0, DenyRuntime())
from app.analysis.similarity.retrieval import RetrievalRequest, retrieve, scan_outliers
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    # Reuse #482's scoped guard on the new owner without editing its shared file.
    from test_product_contracts import boundary_errors, has_import_cycle
    root = Path(__file__).resolve().parents[1] / "app/analysis/similarity"
    modules = {f"app.analysis.similarity.{file.stem}": file.read_text() for file in root.glob("*.py")}
    assert not boundary_errors([("analysis", text, name) for name, text in modules.items()])
    assert not has_import_cycle(modules)
    assert boundary_errors([("analysis", "import app.repertoire")])
