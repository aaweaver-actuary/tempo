"""Authored routes need no card, introduction, recall or ingestion implementation."""
from copy import deepcopy
import json
import pytest
from tempo_game_review_contracts import CanonicalGame, RepertoireSnapshot
from tempo_repertoire_comparison import compare

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def game(moves, *, fen=FEN, color="white"):
    return CanonicalGame(workspace_id="workspace", game_id="game", game_revision=1,
        source_id="file", source_revision=1, source_digest="a" * 64,
        starting_fen=fen, moves=moves, learner_color=color, played_at=None)


def route(moves, *, fen=FEN, color="white", policy="policy", repertoire="repertoire", identifier="route", reentries=None):
    return dict(repertoire_id=repertoire, route_id=identifier, policy_id=policy,
        learner_color=color, starting_fen=fen, moves=moves, reentries=reentries or [])


def snapshot(routes, **changes):
    document = dict(workspace_id="workspace", snapshot_id="snapshot", revision=1,
        digest="b" * 64, source_revision="source-1", captured_at="2026-10-20T12:00:00Z",
        scope_id="selected", scope_repertoire_ids=["repertoire"], complete=True, routes=routes)
    document.update(changes)
    return RepertoireSnapshot.model_validate(document)


def test_slim_authored_membership_matches_without_learning_or_remediation_facts():
    report = compare(game(["e2e4", "e7e5"]), snapshot([route(["e2e4", "e7e5"])]))
    assert [fact.status for fact in report.positions] == ["matched", "matched"]
    assert [fact.side for fact in report.positions] == ["learner", "opponent"]
    assert report.matched_prefix_plies == 2 and report.first_difference is None
    assert report.source_id == "file" and report.source_revision == 1
    assert report.scope_repertoire_ids == ["repertoire"]
    assert report.snapshot_complete and report.snapshot_available
    assert report.snapshot_captured_at == "2026-10-20T12:00:00Z"


def test_first_learner_difference_retains_actual_alternatives_and_route_context():
    report = compare(game(["c2c4"]), snapshot([
        route(["e2e4"], identifier="e4"), route(["d2d4"], identifier="d4"),
    ]))
    first = report.first_difference
    assert first.ply == 0 and first.side == "learner"
    assert first.actual_move == "c2c4" and first.expected_moves == ["d2d4", "e2e4"]
    assert first.fen == FEN
    assert {context.route_id for context in first.route_contexts} == {"e4", "d4"}
    assert report.matched_prefix_plies == 0
    assert compare(game(["d2d4"]), snapshot([route(["e2e4"], identifier="e4"), route(["d2d4"], identifier="d4")])).first_difference is None


def test_opponent_departure_is_first_difference_and_later_own_move_remains_unknown():
    report = compare(game(["e2e4", "c7c5", "g1f3"]), snapshot([route(["e2e4", "e7e5", "g1f3"])]))
    assert [fact.status for fact in report.positions] == ["matched", "different", "unknown"]
    assert report.first_difference.ply == 1 and report.first_difference.side == "opponent"
    assert report.first_difference.expected_moves == ["e7e5"]
    assert report.positions[2].reason == "after_departure"
    assert report.positions[2].expected_moves == [] and report.positions[2].route_contexts == []
    assert report.matched_prefix_plies == 1


@pytest.mark.parametrize("authorized", [False, True])
def test_transposition_after_departure_requires_explicit_authored_reentry(authorized):
    anchors = [{"ply": 4, "authority_id": "approved-reentry"}] if authorized else []
    report = compare(game(["g2g3", "c7c5", "g1f3", "d7d5", "f1g2"]),
        snapshot([route(["g1f3", "d7d5", "g2g3", "c7c5", "f1g2"], reentries=anchors)]))
    assert report.positions[4].status == ("matched" if authorized else "unknown")
    if authorized:
        assert report.positions[4].route_contexts[0].reentry_authority_id == "approved-reentry"
    else:
        assert report.positions[4].reason == "after_departure"
    assert report.first_difference.ply == 0


def test_ended_route_and_missing_start_are_unknown_not_uncovered():
    ended = compare(game(["e2e4", "e7e5"]), snapshot([route(["e2e4"])]))
    assert ended.positions[1].reason == "route_ended" and ended.first_difference is None
    empty = compare(game(["e2e4"]), snapshot([route([])]))
    assert empty.positions[0].reason == "route_ended" and empty.first_difference is None
    absent = compare(game(["e2e4"]), snapshot([]))
    assert absent.positions[0].reason == "no_applicable_route" and absent.first_difference is None


def test_ended_candidate_does_not_turn_partial_continuation_into_uncovered():
    report = compare(game(["e2e4", "c7c5"]), snapshot([
        route(["e2e4"], identifier="short"), route(["e2e4", "e7e5"], identifier="long"),
    ]))
    assert report.positions[1].reason == "route_ended" and report.first_difference is None


def test_conflicting_policy_context_is_ambiguous_and_retains_expected_facts():
    report = compare(game(["c2c4"]), snapshot([
        route(["e2e4"], identifier="one"), route(["d2d4"], policy="another", identifier="two"),
    ]))
    assert report.positions[0].reason == "ambiguous"
    assert report.positions[0].expected_moves == ["d2d4", "e2e4"]
    assert report.first_difference is None


@pytest.mark.parametrize("changes", [dict(complete=False), dict(available=False, unavailable_reason="owner_unavailable")])
def test_incomplete_or_unavailable_snapshot_never_infers_membership(changes):
    report = compare(game(["c2c4"]), snapshot([route(["e2e4"])], **changes))
    assert report.positions[0].reason == "unavailable_evidence" and report.first_difference is None
    assert report.matched_prefix_plies == 0


def test_workspace_and_out_of_scope_route_are_rejected_before_classification():
    with pytest.raises(ValueError, match="workspace_mismatch"):
        compare(game(["e2e4"]), snapshot([], workspace_id="another"))
    with pytest.raises(ValueError, match="outside_snapshot_scope"):
        compare(game(["e2e4"]), snapshot([route(["e2e4"], repertoire="outside")]))


@pytest.mark.parametrize("fault", ["route", "scope", "anchor", "terminal_anchor", "illegal", "invalid_fen"])
def test_malformed_authored_evidence_is_rejected_without_fabricated_difference(fault):
    routes = [route(["e2e4"])]
    changes = {}
    if fault == "route": routes.append(deepcopy(routes[0]))
    elif fault == "scope": changes["scope_repertoire_ids"] = ["repertoire", "repertoire"]
    elif fault == "anchor": routes[0]["reentries"] = [{"ply": 0, "authority_id": "one"}, {"ply": 0, "authority_id": "two"}]
    elif fault == "terminal_anchor": routes[0]["reentries"] = [{"ply": 1, "authority_id": "terminal"}]
    elif fault == "illegal": routes[0]["moves"] = ["e2e5"]
    else: routes[0]["starting_fen"] = "8/8/8/8/8/8/8/8 w - - 0 1"
    with pytest.raises(ValueError): compare(game(["e2e4"]), snapshot(routes, **changes))


@pytest.mark.parametrize("fen,moves,color", [
    ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 37", ["e1g1", "e8c8"], "white"),
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 37", ["e5d6"], "white"),
    ("8/P7/8/8/8/8/7p/K5k1 b - - 0 37", ["h2h1q"], "black"),
])
def test_custom_black_starts_castling_promotion_and_legal_ep_use_relative_ply(fen, moves, color):
    report = compare(game(moves, fen=fen, color=color), snapshot([route(moves, fen=fen, color=color)]))
    assert report.positions[0].side == "learner" and report.positions[0].ply == 0
    assert all(fact.status == "matched" for fact in report.positions)


def test_actual_game_and_snapshot_bytes_bind_identity_even_when_digest_claims_are_reused():
    original_game = game(["e2e4"])
    original_snapshot = snapshot([route(["e2e4"])])
    first = compare(original_game, original_snapshot)
    assert compare(original_game, original_snapshot).model_dump() == first.model_dump()
    altered_game = original_game.model_copy(update={"moves": ["d2d4"]})
    altered_snapshot = original_snapshot.model_copy(update={"routes": snapshot([route(["d2d4"])]).routes})
    assert compare(altered_game, original_snapshot).report_id != first.report_id
    assert compare(original_game, altered_snapshot).report_id != first.report_id
    assert first.source_digest == altered_game.source_digest
    assert first.snapshot_digest == altered_snapshot.digest
    assert original_game.moves == ["e2e4"] and original_snapshot.routes[0].moves == ["e2e4"]


def test_report_containers_do_not_alias_or_mutate_supplied_snapshot_values():
    source_game = game(["e2e4"])
    source_snapshot = snapshot([route(["e2e4"])])
    before = (source_game.model_dump_json(), source_snapshot.model_dump_json())
    report = compare(source_game, source_snapshot)
    assert report.scope_repertoire_ids is not source_snapshot.scope_repertoire_ids
    report.scope_repertoire_ids.append("report-only")
    report.positions[0].expected_moves.append("report-only")
    report.positions[0].route_contexts.clear()
    assert (source_game.model_dump_json(), source_snapshot.model_dump_json()) == before
