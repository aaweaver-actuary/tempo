"""Sequence eligibility contracts, independent of production admissions."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import sys

import chess
import pytest

from app.services.cards import card_id
from app.services.opening_frontier import (
    OpeningFrontierCard, OpeningFrontierObligation, OpeningFrontierPublication,
    OpeningFrontierReview, OpeningFrontierRoute, OpeningFrontierSnapshot,
    project_opening_frontier,
)
from app.services.opening_graph import GraphInput, build_graph
from app.services import opening_frontier as frontier_contract
from app.services.opening_policy_reach import propagate_policy_reach, PolicyReachRoute
from app.services.opening_preparedness import (
    ProbabilityEvidence, DecisionReadiness, CardLearningEffect, rank_card_values,
)
from app.services.opening_segmentation import decision_identity
from app.services.decision_readiness import (
    PresentationIdentity, ReadinessSnapshot, StudyReviewEvidence, decision_readiness,
)
from test_opening_policy_reach import policy_for, evidence_for, CONTEXT, ITALIAN, RUY


MAIN_MOVES = tuple("e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4".split())


def _route(line_id="main", moves=MAIN_MOVES, *, prefix=1, repertoire_id="rep", color="white", starting_fen=chess.STARTING_FEN):
    graph = build_graph(GraphInput(repertoire_id, ({
        "id": line_id, "start_fen": starting_fen, "moves_json": json.dumps(moves),
        "trained_color": color,
    },), prefix))
    return OpeningFrontierRoute(OpeningFrontierPublication(repertoire_id, 2, "current-scope"), line_id, graph)


def _snapshot(*routes, reviewed=(), introduced=()):
    if not routes:
        routes = (_route(),)
    memberships = {}
    for route in routes:
        for step in route.steps:
            memberships.setdefault(step.card_id, set()).add(route.publication.repertoire_id)
    reviewed_ids = set(reviewed)
    introduced_ids = set(introduced) | reviewed_ids
    return OpeningFrontierSnapshot(
        "fixture-snapshot", tuple(set(route.publication for route in routes)), tuple(routes),
        tuple(OpeningFrontierCard(identifier, tuple(sorted(repertoires)),
              introduced_at="2026-10-09" if identifier in introduced_ids else None,
              state="learning" if identifier in introduced_ids else "locked")
              for identifier, repertoires in sorted(memberships.items())),
        tuple(OpeningFrontierReview(f"review-{identifier}", identifier) for identifier in sorted(reviewed_ids)),
    )


def _result(snapshot, identifier):
    return next(card for card in project_opening_frontier(snapshot).cards if card.card_id == identifier)


def _replace_card(snapshot, identifier, **changes):
    return replace(snapshot, cards=tuple(replace(card, **changes) if card.card_id == identifier else card for card in snapshot.cards))


@pytest.mark.parametrize("state", ["locked", "new", "learning", "mature"])
@pytest.mark.parametrize("outcome,guided", [("correct", False), ("again", False), ("again", True)])
def test_relaxed_frontier_saved_practice_never_requires_parent_maturity(state, outcome, guided):
    route = _route()
    root_id, child_id, grandchild_id = (step.card_id for step in route.steps[:3])
    snapshot = _replace_card(_snapshot(route, reviewed=(root_id,)), root_id, state=state)
    snapshot = replace(snapshot, reviews=(replace(snapshot.reviews[0], outcome=outcome, guided=guided),))
    assert _result(snapshot, child_id).status == "eligible"
    assert _result(snapshot, child_id).qualifying_routes[0].exposure_review_ids == (snapshot.reviews[0].review_id,)
    assert _result(snapshot, grandchild_id).status == "prerequisite_unseen"


@pytest.mark.parametrize("evidence", ["introduced_only", "mature_only", "invalidated", "gameplay"])
def test_relaxed_frontier_admission_maturity_and_invalid_evidence_do_not_expose_parents(evidence):
    route = _route()
    root_id, child_id = (step.card_id for step in route.steps[:2])
    snapshot = _snapshot(route, introduced=(root_id,))
    if evidence == "mature_only":
        snapshot = _replace_card(snapshot, root_id, state="mature")
    elif evidence in {"invalidated", "gameplay"}:
        snapshot = replace(snapshot, reviews=(OpeningFrontierReview(
            "saved-review", root_id, source_kind="gameplay" if evidence == "gameplay" else "study",
            invalidated_at="2026-10-10" if evidence == "invalidated" else None,
        ),))
    child = _result(snapshot, child_id)
    assert child.status == "prerequisite_unseen"
    assert child.routes[0].missing_prerequisite_card_ids == (root_id,)


def test_relaxed_frontier_requires_every_prerequisite_on_one_complete_route():
    route = _route()
    root_id, child_id, grandchild_id = (step.card_id for step in route.steps[:3])
    assert _result(_snapshot(route, reviewed=(child_id,)), grandchild_id).status == "prerequisite_unseen"
    complete = _snapshot(route, reviewed=(root_id, child_id))
    assert _result(complete, grandchild_id).status == "eligible"
    assert _result(complete, grandchild_id).routes[0].prerequisite_card_ids == (root_id, child_id)


@pytest.mark.parametrize("color,prefix", [("white", 1), ("white", 2), ("black", 1), ("black", 2)])
def test_relaxed_frontier_handles_short_and_cumulative_prefixes_for_both_colors(color, prefix):
    route = _route(color=color, prefix=prefix)
    root_id, child_id = (step.card_id for step in route.steps[:2])
    root = _result(_snapshot(route), root_id)
    child = _result(_snapshot(route, reviewed=(root_id,)), child_id)
    assert root.status == "eligible"
    assert child.status == "eligible"
    for assessment, step in zip((root, child), route.steps):
        provenance = assessment.qualifying_routes[0]
        assert provenance.publication == route.publication
        assert provenance.line_id == route.line_id
        assert provenance.trained_color == color
        assert provenance.decision_index == step.decision_index
        assert provenance.first_decision_index == step.first_decision_index
        assert provenance.last_decision_index == step.last_decision_index


def test_relaxed_frontier_accepts_an_explicit_custom_position_root_and_clock_only_transitions():
    board = chess.Board()
    board.push_uci("e2e4")
    route = _route(moves=tuple("c7c5 g1f3 d7d6 d2d4 c5d4".split()), color="black", starting_fen=board.fen())
    child = route.steps[1]
    fields = child.starting_fen.split()
    fields[-2:] = ["18", "42"]
    changed = replace(child, starting_fen=" ".join(fields))
    route = replace(route, steps=(route.steps[0], changed, *route.steps[2:]))
    assert _result(_snapshot(route, reviewed=(route.steps[0].card_id,)), child.card_id).status == "eligible"


def test_relaxed_frontier_unpracticed_sibling_does_not_block_an_exposed_branch():
    likely = _route("likely")
    rare = _route("rare", tuple("e2e4 c7c5 g1f3 d7d6 d2d4".split()))
    snapshot = _snapshot(likely, rare, reviewed=tuple(step.card_id for step in likely.steps[:2]))
    assert _result(snapshot, likely.steps[2].card_id).status == "eligible"
    assert _result(snapshot, rare.steps[2].card_id).status == "prerequisite_unseen"


def _transposed_routes(prefix=2, *, separate_repertoires=False):
    first = _route("first", tuple("g1f3 d7d5 g2g3 g8f6 f1g2".split()), prefix=prefix)
    second = _route("second", tuple("g2g3 d7d5 g1f3 g8f6 f1g2".split()), prefix=prefix,
                    repertoire_id="other" if separate_repertoires else "rep")
    assert first.steps[-1].card_id == second.steps[-1].card_id
    return first, second


@pytest.mark.parametrize("separate_repertoires", [False, True])
def test_relaxed_frontier_one_exposed_transposed_route_suffices_without_duplicate_candidates(separate_repertoires):
    first, second = _transposed_routes(separate_repertoires=separate_repertoires)
    shared_id = first.steps[-1].card_id
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id,))
    frontier = project_opening_frontier(snapshot)
    assert frontier.eligible_card_ids.count(shared_id) == 1
    shared = _result(snapshot, shared_id)
    assert len(shared.routes) == 2
    assert len(shared.qualifying_routes) == 1
    assert shared.qualifying_routes[0].line_id == "first"


def test_relaxed_frontier_never_splices_exposure_from_different_transposed_routes():
    first, second = _transposed_routes(prefix=1)
    shared_id = first.steps[-1].card_id
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id, second.steps[1].card_id))
    shared = _result(snapshot, shared_id)
    assert shared.status == "prerequisite_unseen"
    assert {route.missing_prerequisite_card_ids for route in shared.routes} == {
        (first.steps[1].card_id,), (second.steps[0].card_id,),
    }


def test_relaxed_frontier_shared_card_exposure_is_reused_but_unrelated_repertoire_evidence_is_not():
    first = _route()
    second = _route(repertoire_id="other")
    unrelated = _route("unrelated", tuple("d2d4 d7d5 c2c4".split()), repertoire_id="third")
    snapshot = _snapshot(first, second, unrelated, reviewed=(first.steps[0].card_id,))
    assert len(_result(snapshot, first.steps[1].card_id).qualifying_routes) == 2
    assert _result(snapshot, unrelated.steps[1].card_id).status == "prerequisite_unseen"


@pytest.mark.parametrize("defect,reason", [
    ("missing_root", "missing_root"), ("parent", "parent_link_mismatch"),
    ("index_gap", "decision_index_gap"), ("range_gap", "decision_range_gap"),
    ("illegal", "illegal_move"), ("identity", "card_identity_mismatch"),
    ("metadata", "decision_metadata_mismatch"), ("color", "trained_color_mismatch"),
    ("context", "route_context_mismatch"), ("kind", "invalid_segment_kind"),
    ("descendant_prefix", "invalid_segment_kind"),
    ("invalid_fen", "invalid_segment"), ("board", "board_discontinuity"),
])
def test_relaxed_frontier_structurally_impossible_descendants_are_ineligible(defect, reason):
    route = _route()
    root, child, *remaining = route.steps
    if defect == "missing_root":
        changed_route = replace(route, steps=route.steps[1:])
    else:
        changes = {
            "parent": {"parent_card_id": "absent-parent"},
            "index_gap": {"decision_index": 8}, "range_gap": {"first_decision_index": 8, "last_decision_index": 8},
            "illegal": {"moves": ("e2e5",)}, "identity": {"card_id": "invented-card"},
            "metadata": {"decision_fen_key": "wrong-position"}, "color": {"trained_color": "black"},
            "context": {"line_id": "other-line"}, "kind": {"segment_kind": "unknown"},
            "descendant_prefix": {"segment_kind": "prefix"},
            "invalid_fen": {"starting_fen": "not a fen"},
            "board": {"starting_fen": chess.STARTING_FEN, "moves": ("g1f3",),
                      "card_id": card_id(chess.STARTING_FEN, ("g1f3",))},
        }[defect]
        child = replace(child, **changes)
        changed_route = replace(route, steps=(root, child, *remaining))
    snapshot = _snapshot(changed_route, reviewed=(root.card_id,))
    result = _result(snapshot, child.card_id)
    assert result.status == "structurally_unreachable"
    assert reason in result.reasons
    assert child.card_id not in project_opening_frontier(snapshot).eligible_card_ids


@pytest.mark.parametrize("change", ["generation", "scope", "unpublished", "membership"])
def test_relaxed_frontier_stale_publication_and_scope_cannot_unlock_cards(change):
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    if change == "generation":
        snapshot = replace(snapshot, publications=(replace(route.publication, generation=3),))
    elif change == "scope":
        snapshot = replace(snapshot, publications=(replace(route.publication, scope_digest="new-scope"),))
    elif change == "unpublished":
        snapshot = replace(snapshot, publications=())
    else:
        snapshot = _replace_card(snapshot, route.steps[0].card_id, repertoire_ids=("elsewhere",))
    assert route.steps[1].card_id not in project_opening_frontier(snapshot).eligible_card_ids


def test_relaxed_frontier_reimport_reuses_real_exposure_but_resegmentation_does_not_invent_it():
    old = _route(prefix=2)
    current = replace(old, publication=replace(old.publication, generation=3))
    assert _result(_snapshot(current, reviewed=(old.steps[0].card_id,)), current.steps[1].card_id).status == "eligible"
    shortened = _route(prefix=1)
    snapshot = replace(_snapshot(shortened), reviews=(OpeningFrontierReview("old-review", old.steps[0].card_id),))
    assert _result(snapshot, shortened.steps[1].card_id).status == "prerequisite_unseen"


def test_relaxed_frontier_conflicting_or_missing_occurrences_never_manufacture_a_path():
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    conflicting = replace(route, steps=(replace(route.steps[0], parent_card_id="bad"), *route.steps[1:]))
    assert _result(replace(snapshot, routes=(route, conflicting)), route.steps[1].card_id).status == "structurally_unreachable"
    duplicate_slot = replace(route, steps=(route.steps[0], route.steps[1], replace(route.steps[1], parent_card_id="bad"), *route.steps[2:]))
    assert _result(replace(snapshot, routes=(duplicate_slot,)), route.steps[1].card_id).status == "structurally_unreachable"
    missing_parent = replace(snapshot, cards=tuple(card for card in snapshot.cards if card.card_id != route.steps[0].card_id))
    assert "missing_card" in _result(missing_parent, route.steps[1].card_id).reasons
    assert _result(replace(snapshot, routes=()), route.steps[1].card_id).reasons == ("no_current_route",)


@pytest.mark.parametrize("flag", ["archived", "deleted", "superseded", "pending_validation"])
def test_relaxed_frontier_lifecycle_exclusions_override_candidates_and_introduction(flag):
    route = _route()
    target = route.steps[1].card_id
    snapshot = _replace_card(_snapshot(route, reviewed=(route.steps[0].card_id,), introduced=(target,)), target, **{flag: True})
    result = _result(snapshot, target)
    assert result.status is None
    assert result.exclusion_reasons == (flag,)
    assert target not in project_opening_frontier(snapshot).eligible_card_ids


def test_relaxed_frontier_unavailable_ancestors_block_only_their_route():
    first, second = _transposed_routes()
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id, second.steps[0].card_id))
    snapshot = _replace_card(snapshot, first.steps[0].card_id, deleted=True)
    assert _result(snapshot, first.steps[-1].card_id).status == "eligible"
    assert [route.line_id for route in _result(snapshot, first.steps[-1].card_id).qualifying_routes] == ["second"]


def test_relaxed_frontier_integrity_blocks_are_scoped_to_each_repertoire():
    first = _route()
    second = _route(repertoire_id="other")
    target = first.steps[1].card_id
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id,))
    one_block = _replace_card(snapshot, target, integrity_blocked_repertoire_ids=("rep",))
    assert _result(one_block, target).status == "eligible"
    assert _result(one_block, target).qualifying_routes[0].publication.repertoire_id == "other"
    both_blocked = _replace_card(snapshot, target, integrity_blocked_repertoire_ids=("rep", "other"))
    assert _result(both_blocked, target).exclusion_reasons == ("integrity_blocked",)


def test_relaxed_frontier_introduced_descendant_survives_parent_failure_and_invalidated_exposure():
    route = _route()
    parent_id, target = (step.card_id for step in route.steps[:2])
    original = _snapshot(route, reviewed=(parent_id,), introduced=(target,))
    invalidated = replace(original, reviews=(replace(original.reviews[0], invalidated_at="2026-10-10"),))
    failed = _replace_card(invalidated, parent_id, state="learning")
    result = _result(failed, target)
    assert result.status == "already_introduced"
    assert result.routes[0].missing_prerequisite_card_ids == (parent_id,)
    assert target not in project_opening_frontier(failed).eligible_card_ids


@pytest.mark.parametrize("kind", ["real_game_miss", "approved_gameplay_opportunity"])
def test_relaxed_frontier_game_obligations_are_annotations_not_prerequisite_exposure(kind):
    route = _route()
    target = route.steps[2].card_id
    snapshot = _snapshot(route)
    obligation = OpeningFrontierObligation("rep", target, kind, "game-evidence-7")
    annotated = replace(snapshot, obligations=(obligation, obligation))
    assert project_opening_frontier(snapshot).eligible_card_ids == project_opening_frontier(annotated).eligible_card_ids
    result = _result(annotated, target)
    assert result.status == "prerequisite_unseen"
    assert result.obligations == (obligation,)


def test_relaxed_frontier_finite_repeated_positions_do_not_require_a_card_identity_dag():
    route = _route(moves=tuple("g1f3 g8f6 f3g1 f6g8 g1f3 g8f6 f3g1 f6g8 e2e4".split()))
    assert route.steps[1].card_id == route.steps[3].card_id
    snapshot = _snapshot(route, reviewed=tuple(step.card_id for step in route.steps[:-1]))
    assert _result(snapshot, route.steps[-1].card_id).status == "eligible"
    assert all(not assessment.blocked_reasons for card in project_opening_frontier(snapshot).cards for assessment in card.routes)


def test_relaxed_frontier_is_immutable_pure_idempotent_and_deterministic_under_shuffling(monkeypatch):
    first, second = _transposed_routes()
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id,))
    original = deepcopy(snapshot)
    expected = project_opening_frontier(snapshot)
    import builtins
    import socket
    import time

    def forbidden(*args, **kwargs):
        raise AssertionError("The pure model must not perform I/O or read a clock")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    randomizer = random.Random(118)
    for _ in range(12):
        def shuffled(values):
            reordered = list(values)
            randomizer.shuffle(reordered)
            return tuple(reordered)
        changed = replace(snapshot, publications=shuffled(snapshot.publications),
                          routes=shuffled(tuple(replace(route, steps=shuffled(route.steps)) for route in snapshot.routes)),
                          cards=shuffled(snapshot.cards), reviews=shuffled(snapshot.reviews))
        assert project_opening_frontier(changed) == expected
    assert project_opening_frontier(snapshot) == expected
    assert snapshot == original
    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_id = "changed"
    with pytest.raises(FrozenInstanceError):
        expected.cards[0].status = "eligible"
    assert project_opening_frontier(replace(snapshot, routes=(*snapshot.routes, *snapshot.routes), reviews=(*snapshot.reviews, *snapshot.reviews))) == expected


@pytest.mark.parametrize("record", ["publication", "card", "review"])
def test_relaxed_frontier_rejects_conflicting_snapshot_records(record):
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    if record == "publication":
        snapshot = replace(snapshot, publications=(*snapshot.publications, replace(snapshot.publications[0], generation=3)))
    elif record == "card":
        snapshot = replace(snapshot, cards=(*snapshot.cards, replace(snapshot.cards[0], archived=True)))
    else:
        snapshot = replace(snapshot, reviews=(*snapshot.reviews, replace(snapshot.reviews[0], source_kind="gameplay")))
    with pytest.raises(ValueError, match="Conflicting"):
        project_opening_frontier(snapshot)


def test_relaxed_frontier_equivalent_route_copies_collapse_even_when_occurrences_are_reordered():
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    reordered_copy = replace(route, steps=tuple(reversed(route.steps)))
    assert project_opening_frontier(replace(snapshot, routes=(route, reordered_copy))) == project_opening_frontier(snapshot)


def test_relaxed_frontier_empty_snapshot_and_fixture_diagnostic_are_explicit():
    empty = OpeningFrontierSnapshot("empty", (), (), ())
    assert project_opening_frontier(empty).eligible_card_ids == ()
    script = Path(__file__).resolve().parents[2] / "scripts/show_opening_frontier.py"
    completed = subprocess.run([sys.executable, str(script)], check=True, text=True, capture_output=True)
    example = json.loads(completed.stdout)
    assert example["diagnostic_only"] is True
    assert example["policy_version"] == "sequence-exposure-v1"
    assert {card["status"] for card in example["cards"]} >= {
        "eligible", "already_introduced", "prerequisite_unseen", "structurally_unreachable",
    }
    assert any(card["obligations"] for card in example["cards"])
    assert len(example["eligible_card_ids"]) == len(set(example["eligible_card_ids"]))


def _policy_projection(snapshot, *routes, valuation_inputs=(), policy=None):
    policy = policy or policy_for(routes[0].publication.repertoire_id,
        (tuple(move for step in route.steps for move in step.moves) for route in routes),
        trained_color=routes[0].steps[0].trained_color, root=routes[0].steps[0].starting_fen)
    bindings = tuple(frontier_contract.OpeningPolicyRouteBinding(route.publication, route.line_id)
                     for route in routes)
    return frontier_contract.project_policy_opening_frontier(snapshot, policy=policy,
        route_bindings=bindings, valuation_inputs=valuation_inputs)


def _policy_card(result, identifier):
    return next(card for card in result.cards if card.eligibility.card_id == identifier)


def _valuation_input(snapshot, policy, route, step, *, reach=None, readiness=None):
    board = chess.Board(step.starting_fen)
    for move in step.moves:
        if board.turn == (policy.trained_color == "white"):
            identifier = decision_identity(policy.repertoire_id, board.fen(), policy.trained_color,
                                           move, policy.policy_version)
        board.push_uci(move)
    return frontier_contract.OpeningFrontierValuationInput(snapshot.snapshot_id,
        policy.policy_id, policy.policy_version, step.card_id, identifier,
        (frontier_contract.OpeningPolicyRouteBinding(route.publication, route.line_id),),
        reach if reach is not None else ProbabilityEvidence.exact(.25),
        readiness if readiness is not None else ProbabilityEvidence.exact(.2),
        ("explicit-fixture-input-v1",))


def test_policy_frontier_foreign_route_cannot_establish_selected_policy_eligibility():
    first, second = _transposed_routes(separate_repertoires=True)
    snapshot = _snapshot(first, second, reviewed=(first.steps[0].card_id,))
    shared_identifier = first.steps[-1].card_id
    # Existing #151 remains deliberately global; selecting after aggregation is unsafe.
    assert shared_identifier in project_opening_frontier(snapshot).eligible_card_ids
    result = _policy_projection(snapshot, second)
    assert shared_identifier not in result.eligible_card_ids
    blocked = _policy_card(result, shared_identifier)
    assert blocked.eligibility.status == "prerequisite_unseen"
    assert blocked.eligibility.routes[0].missing_prerequisite_card_ids == (second.steps[0].card_id,)
    assert all(route.publication.repertoire_id == "other" for route in blocked.eligibility.routes)


@pytest.mark.parametrize("evidence,expected", [
    ("failed_learning", "eligible"), ("queued_only", "prerequisite_unseen"),
    ("invalidated", "prerequisite_unseen"), ("reimport", "eligible"),
    ("changed_prefix", "prerequisite_unseen"),
])
def test_policy_frontier_preserves_durable_exposure_without_maturity(evidence, expected):
    route = _route()
    root_identifier, child_identifier = (step.card_id for step in route.steps[:2])
    snapshot = _snapshot(route, reviewed=(root_identifier,))
    if evidence == "queued_only":
        snapshot = replace(snapshot, reviews=())  # Introduced/new/queued metadata cannot be a review.
    elif evidence == "invalidated":
        snapshot = replace(snapshot, reviews=(replace(snapshot.reviews[0], invalidated_at="2026-10-11"),))
    elif evidence == "reimport":
        route = replace(route, publication=replace(route.publication, generation=3))
        snapshot = _snapshot(route, reviewed=(root_identifier,))
    elif evidence == "changed_prefix":
        old = _route(prefix=2)
        snapshot = replace(_snapshot(route), reviews=(OpeningFrontierReview("old-prefix-review", old.steps[0].card_id),))
    result = _policy_projection(snapshot, route)
    assert _policy_card(result, child_identifier).eligibility.status == expected
    assert _policy_card(result, root_identifier).decisions[0].readiness.status == "unknown"
    assert result.exposure_version == "sequence-exposure-v1"


def test_policy_frontier_one_exposed_opponent_transposition_suffices_and_duplicates_collapse():
    first = _route("first", tuple("g1f3 d7d5 g2g3 g8f6 f1g2 e7e6 e1g1".split()), prefix=2)
    second = _route("second", tuple("g1f3 g8f6 g2g3 d7d5 f1g2 e7e6 e1g1".split()), prefix=2)
    assert first.steps[-1].card_id == second.steps[-1].card_id
    snapshot = _snapshot(first, second, reviewed=tuple(step.card_id for step in first.steps[:-1]))
    result = _policy_projection(snapshot, first, second)
    shared = _policy_card(result, first.steps[-1].card_id)
    assert shared.eligibility.status == "eligible"
    assert len(shared.eligibility.qualifying_routes) == 1
    assert len(shared.eligibility.routes) == 2
    assert result.eligible_card_ids.count(shared.eligibility.card_id) == 1
    duplicated = replace(snapshot, routes=(*snapshot.routes, *snapshot.routes), reviews=snapshot.reviews * 2)
    assert _policy_projection(duplicated, first, second, first) == result


def test_policy_frontier_never_splices_transposed_practice_or_uses_valuation_as_exposure():
    first = _route("first", tuple("g1f3 d7d5 g2g3 g8f6 f1g2 e7e6 e1g1".split()))
    second = _route("second", tuple("g1f3 g8f6 g2g3 d7d5 f1g2 e7e6 e1g1".split()))
    policy = policy_for("rep", (tuple(move for step in route.steps for move in step.moves)
                                for route in (first, second)))
    snapshot = _snapshot(first, second,
        reviewed=(first.steps[0].card_id, first.steps[1].card_id, second.steps[2].card_id))
    supplied = _valuation_input(snapshot, policy, first, first.steps[-1],
        reach=ProbabilityEvidence.exact(1), readiness=ProbabilityEvidence.exact(1))
    result = _policy_projection(snapshot, first, second, policy=policy, valuation_inputs=(supplied,))
    shared = _policy_card(result, first.steps[-1].card_id)
    assert shared.eligibility.status == "prerequisite_unseen"
    assert shared.eligibility.qualifying_routes == ()
    assert shared.decisions == (supplied,)
    assert {route.missing_prerequisite_card_ids for route in shared.eligibility.routes} == {
        (first.steps[2].card_id,), (second.steps[1].card_id,)}


@pytest.mark.parametrize("missing_action", [False, True])
def test_policy_frontier_incompatible_or_unresolved_actions_block_only_their_route(missing_action):
    italian = _route("italian", ITALIAN)
    ruy = _route("ruy", RUY)
    policy = policy_for("rep", (ITALIAN,))
    if missing_action:
        policy = replace(policy, actions=tuple(action for action in policy.actions if action.move_uci != "f1c4"))
    snapshot = _snapshot(italian, ruy, reviewed=tuple(step.card_id for step in italian.steps[:2]))
    result = _policy_projection(snapshot, italian, ruy, policy=policy)
    assert _policy_card(result, ruy.steps[2].card_id).eligibility.status == "structurally_unreachable"
    assert _policy_card(result, italian.steps[2].card_id).eligibility.status == (
        "structurally_unreachable" if missing_action else "eligible")
    reason = "missing_policy_action" if missing_action else "policy_action_mismatch"
    assert reason in _policy_card(result, ruy.steps[2].card_id).eligibility.routes[0].blocked_reasons
    assert reason in _policy_card(result, ruy.steps[-1].card_id).eligibility.routes[0].blocked_reasons
    assert _policy_card(result, italian.steps[1].card_id).eligibility.status == "already_introduced"


@pytest.mark.parametrize("color", ["white", "black"])
def test_policy_frontier_retains_explicit_policy_identity_and_trained_color(color):
    moves = ITALIAN if color == "white" else (*ITALIAN, "g8f6")
    route = _route(moves=moves, color=color)
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    policy = replace(policy_for("rep", (moves,), trained_color=color), policy_id="chosen-experiment",
        actions=tuple(replace(action, policy_id="chosen-experiment")
                      for action in policy_for("rep", (moves,), trained_color=color).actions), policy_version=3)
    result = _policy_projection(snapshot, route, policy=policy)
    assert result.policy == policy and result.policy.policy_id != result.policy.repertoire_id
    assert route.steps[1].card_id in result.eligible_card_ids
    assert all(value.policy_id == policy.policy_id and value.policy_version == 3
               for card in result.cards for value in card.decisions)


@pytest.mark.parametrize("selected_moves,other_moves", [(ITALIAN, RUY), (RUY, ITALIAN)])
def test_policy_frontier_italian_ruy_conditional_values_are_isolated(selected_moves, other_moves):
    selected = _route("selected", selected_moves, repertoire_id="selected")
    other = _route("other", other_moves, repertoire_id="other")
    snapshot = _snapshot(selected, reviewed=tuple(step.card_id for step in selected.steps[:-1]))
    policy = policy_for("selected", (selected_moves,))
    reach = propagate_policy_reach(policy, (PolicyReachRoute("selected", "selected", selected_moves),),
                                   evidence_for((selected_moves,)), context=CONTEXT)
    value = _valuation_input(snapshot, policy, selected, selected.steps[-1])
    actual_reach = next(decision for decision in reach.decisions if decision.identity == value.decision_id)
    value = replace(value, reach=actual_reach.probability, provenance=(reach.model_version, reach.context_id))
    expected = _policy_projection(snapshot, selected, policy=policy, valuation_inputs=(value,))
    for copies in (1, 2, 4):
        expanded = _snapshot(selected, *((other,) * copies),
                             reviewed=tuple(step.card_id for step in selected.steps[:-1]))
        assert _policy_projection(expanded, selected, policy=policy, valuation_inputs=(value,)) == expected
    assert _policy_card(expected, value.card_id).decisions == (value,)


def test_policy_frontier_supplied_reach_orders_deep_common_and_shallow_rare_with_existing_kernel():
    common_moves = ITALIAN[:5]
    rare_moves = ("e2e4", "c7c5", "g1f3")
    common, rare = _route("common", common_moves), _route("rare", rare_moves)
    policy = policy_for("rep", (common_moves, rare_moves))
    snapshot = _snapshot(common, rare, reviewed=tuple(step.card_id for step in common.steps[:-1]))
    reach = propagate_policy_reach(policy,
        (PolicyReachRoute("rep", "common", common_moves), PolicyReachRoute("rep", "rare", rare_moves)),
        evidence_for((common_moves, rare_moves)), context=CONTEXT)
    supplied = tuple(_valuation_input(snapshot, policy, route, route.steps[-1]) for route in (common, rare))
    by_decision = {decision.identity: decision for decision in reach.decisions}
    supplied = tuple(replace(value, reach=by_decision[value.decision_id].probability,
                            provenance=(reach.model_version, reach.context_id)) for value in supplied)
    assert supplied[0].reach.lower == pytest.approx(.42 * .50)
    assert supplied[1].reach.lower == pytest.approx(.10)
    assert common.steps[-1].last_decision_index > rare.steps[-1].last_decision_index

    def ordering(values):
        result = _policy_projection(snapshot, common, rare, policy=policy, valuation_inputs=values)
        rows = tuple(value for card in result.cards if card.eligibility.status == "eligible"
                     for value in card.decisions if value.card_id in {row.card_id for row in supplied})
        assert {row.card_id for row in rows} == {row.card_id for row in supplied}
        memory = tuple(DecisionReadiness(row.decision_id,
            (replace(by_decision[row.decision_id].kernel_routes()[0], probability=row.reach),),
            row.readiness, policy_id=policy.policy_id) for row in rows)
        effects = tuple(CardLearningEffect(row.card_id, ((row.decision_id, ProbabilityEvidence.exact(.8)),),
                                          policy_id=policy.policy_id) for row in rows)
        ranking = rank_card_values(memory, effects, policy_id=policy.policy_id)
        return tuple(value.card_id for value in ranking.ranked)

    assert ordering(supplied) == (supplied[0].card_id, supplied[1].card_id)
    assert ordering((replace(supplied[0], reach=supplied[1].reach),
                     replace(supplied[1], reach=supplied[0].reach))) == (supplied[1].card_id, supplied[0].card_id)


def test_policy_frontier_preserves_real_readiness_and_unknown_prefix_decisions():
    route = _route(prefix=2)
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    policy = policy_for("rep", (MAIN_MOVES,))
    as_of = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    values = []
    for step in route.steps[:2]:
        memory = ReadinessSnapshot(PresentationIdentity(step.card_id, 1, "rep", "white"),
            step.starting_fen, step.moves,
            reviews=(StudyReviewEvidence(1, step.card_id, as_of, "again", card_revision=1),))
        estimate = decision_readiness(memory, as_of,
                                      decision_index=step.last_decision_index - step.first_decision_index)
        probability = (ProbabilityEvidence.unknown(";".join(estimate.reasons)) if estimate.readiness_score is None
                       else ProbabilityEvidence.exact(estimate.readiness_score))
        row = _valuation_input(snapshot, policy, route, step, readiness=probability)
        values.append(replace(row, provenance=(f"readiness:{estimate.version}:{as_of.isoformat()}",)))
    result = _policy_projection(snapshot, route, policy=policy, valuation_inputs=tuple(values))
    root, child = (_policy_card(result, step.card_id) for step in route.steps[:2])
    assert all(row.readiness.status == "unknown" for row in root.decisions)
    assert child.decisions[0].readiness == ProbabilityEvidence.exact(0)
    assert child.eligibility.status == "eligible"
    assert child.prerequisites == root.decisions
    assert len(root.decisions) == 2  # No coarse prefix estimate was copied into either decision.


def test_policy_frontier_missing_values_stay_unknown_and_supplied_bounds_survive():
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    policy = policy_for("rep", (MAIN_MOVES,))
    value = _valuation_input(snapshot, policy, route, route.steps[1],
        reach=ProbabilityEvidence.bounded(.1, .4, "missing_source_distribution"),
        readiness=ProbabilityEvidence.unknown("unavailable_readiness"))
    result = _policy_projection(snapshot, route, policy=policy, valuation_inputs=(value,))
    child = _policy_card(result, value.card_id)
    assert child.decisions == (value,)
    assert child.eligibility.status == "eligible"
    assert child.prerequisites[0].reach.status == child.prerequisites[0].readiness.status == "unknown"
    assert child.prerequisites[0].readiness != ProbabilityEvidence.exact(0)


@pytest.mark.parametrize("defect", ["snapshot", "policy", "version", "boolean_version", "card", "decision",
    "generation", "scope", "line", "empty_routes", "empty_provenance", "conflict"])
def test_policy_frontier_rejects_mismatched_stale_and_conflicting_valuation_sources(defect):
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    policy = policy_for("rep", (MAIN_MOVES,))
    value = _valuation_input(snapshot, policy, route, route.steps[1])
    changes = {"snapshot": {"snapshot_id": "old-snapshot"}, "policy": {"policy_id": "other"},
        "version": {"policy_version": 2}, "boolean_version": {"policy_version": True},
        "card": {"card_id": "foreign-card"}, "decision": {"decision_id": "foreign-decision"},
        "empty_routes": {"route_bindings": ()}, "empty_provenance": {"provenance": ()}}
    if defect in changes:
        value = replace(value, **changes[defect])
    elif defect in {"generation", "scope", "line"}:
        binding = value.route_bindings[0]
        publication = replace(binding.publication, **({"generation": 1} if defect == "generation"
                              else {"scope_digest": "old-scope"} if defect == "scope" else {}))
        value = replace(value, route_bindings=(replace(binding, publication=publication,
                                                      line_id="other" if defect == "line" else binding.line_id),))
    inputs = (value, replace(value, readiness=ProbabilityEvidence.exact(.3))) if defect == "conflict" else (value,)
    with pytest.raises(ValueError):
        _policy_projection(snapshot, route, policy=policy, valuation_inputs=inputs)


@pytest.mark.parametrize("defect", ["foreign", "generation", "missing", "color", "game_source"])
def test_policy_frontier_invalid_route_bindings_and_game_provenance_are_explicit(defect):
    route = _route()
    snapshot = _snapshot(route)
    policy = policy_for("rep", (MAIN_MOVES,))
    binding = frontier_contract.OpeningPolicyRouteBinding(route.publication, route.line_id)
    if defect == "foreign":
        binding = replace(binding, publication=replace(binding.publication, repertoire_id="other"))
    elif defect == "generation":
        binding = replace(binding, publication=replace(binding.publication, generation=1))
    elif defect == "missing":
        binding = replace(binding, line_id="absent")
    elif defect == "game_source":
        snapshot = replace(snapshot, obligations=(OpeningFrontierObligation("rep", route.steps[1].card_id,
                                                                           "real_game_miss", ""),))
    else:
        black = _route(color="black")
        snapshot = _snapshot(black)
        result = frontier_contract.project_policy_opening_frontier(snapshot, policy=policy, route_bindings=(binding,))
        assert all("policy_trained_color_mismatch" in card.eligibility.reasons for card in result.cards)
        return
    with pytest.raises(ValueError):
        frontier_contract.project_policy_opening_frontier(snapshot, policy=policy, route_bindings=(binding,))


@pytest.mark.parametrize("kind", ["real_game_miss", "approved_gameplay_opportunity"])
def test_policy_frontier_durable_game_obligations_remain_scoped_annotations(kind):
    route = _route()
    snapshot = _snapshot(route)
    obligation = OpeningFrontierObligation("rep", route.steps[-1].card_id, kind, "durable-game-7")
    snapshot = replace(snapshot, obligations=(obligation, obligation, replace(obligation, repertoire_id="foreign")))
    result = _policy_projection(snapshot, route)
    card = _policy_card(result, obligation.card_id)
    assert card.eligibility.obligations == (obligation,)
    assert card.eligibility.status == "prerequisite_unseen"


def test_policy_frontier_is_detached_read_only_and_order_independent(monkeypatch):
    route = _route()
    snapshot = _snapshot(route, reviewed=(route.steps[0].card_id,))
    policy = policy_for("rep", (MAIN_MOVES,))
    value = _valuation_input(snapshot, policy, route, route.steps[1])
    parent_value = _valuation_input(snapshot, policy, route, route.steps[0])
    original = deepcopy((snapshot, policy, value, parent_value))
    expected = _policy_projection(snapshot, route, policy=policy, valuation_inputs=(value, parent_value))
    import builtins
    import socket
    import sqlite3
    import time
    import psycopg
    from app.services import scheduler

    def forbidden(*args, **kwargs):
        raise AssertionError("Policy projection must not read/write storage, use network/clock, or schedule FSRS")

    for target, name in ((builtins, "open"), (socket.socket, "connect"), (sqlite3, "connect"),
                         (psycopg, "connect"), (time, "time"), (scheduler, "schedule_review")):
        monkeypatch.setattr(target, name, forbidden)
    for index in range(8):
        randomizer = random.Random(index)
        def shuffled(items):
            items = list(items)
            randomizer.shuffle(items)
            return tuple(items)
        changed = replace(snapshot, cards=shuffled(snapshot.cards), reviews=shuffled(snapshot.reviews * 2),
            publications=shuffled(snapshot.publications * 2),
            routes=shuffled((route, replace(route, steps=shuffled(route.steps)))))
        assert _policy_projection(changed, route, policy=policy,
                                  valuation_inputs=shuffled((value, parent_value, value))) == expected
    assert (snapshot, policy, value, parent_value) == original
    with pytest.raises(FrozenInstanceError):
        expected.cards[0].eligibility.status = "eligible"
    with pytest.raises(FrozenInstanceError):
        expected.cards[0].decisions[0].readiness = ProbabilityEvidence.exact(0)
    mutable_bindings, mutable_provenance = list(value.route_bindings), ["explicit-fixture-input-v1"]
    detached = replace(value, route_bindings=mutable_bindings, provenance=mutable_provenance)
    mutable_bindings.clear()
    mutable_provenance.clear()
    assert detached == value
    mutable_steps = list(route.steps)
    mutable_snapshot = replace(snapshot, routes=[replace(route, steps=mutable_steps)], cards=list(snapshot.cards))
    detached_result = _policy_projection(mutable_snapshot, route, policy=policy)
    mutable_steps.clear()
    mutable_snapshot.cards.clear()
    assert detached_result == _policy_projection(snapshot, route, policy=policy)


def test_policy_frontier_empty_scope_and_unbound_cards_have_no_implicit_candidates():
    policy = policy_for("rep", (MAIN_MOVES,))
    empty = OpeningFrontierSnapshot("empty", (), (), ())
    result = frontier_contract.project_policy_opening_frontier(empty, policy=policy, route_bindings=())
    assert result.cards == result.eligible_card_ids == ()
    result = frontier_contract.project_policy_opening_frontier(_snapshot(), policy=policy, route_bindings=())
    assert result.eligible_card_ids == ()
    assert all(card.eligibility.reasons == ("no_current_route",) for card in result.cards)


def test_policy_frontier_composes_with_merged_valuation_adapter_without_fabricating_effects():
    from app.services import opening_card_valuation as valuation
    from app.services.opening_ranking_evaluation import OpeningRankingCandidate, canonical_json
    from test_opening_card_valuation import fixture

    current = fixture()
    route = _route("line", ITALIAN, repertoire_id="italian")
    route = replace(route, publication=replace(route.publication, generation=7))
    snapshot = _snapshot(route, reviewed=tuple(step.card_id for step in route.steps[:2]))
    selected = _policy_card(_policy_projection(snapshot, route, policy=current.policy), route.steps[2].card_id)
    assert selected.eligibility.status == "eligible"
    affected = tuple(value.decision_id for value in selected.decisions)
    metadata = {"frontier": asdict(selected)}
    candidate = valuation.ValuationCandidate(
        OpeningRankingCandidate(selected.eligibility.card_id, 1, canonical_json(metadata), policy_id="italian"),
        1, 1, affected, (("generation-7", "line"),),
        CardLearningEffect(selected.eligibility.card_id, ((affected[0], ProbabilityEvidence.exact(.8)),),
                           policy_id="italian"), "explicit-fixture-effect-v1")
    result = valuation.evaluate_opening_card_valuation(replace(current, candidates=(candidate,)))
    row = result["evaluations"][0]["rows"][0]
    assert row["score"] == pytest.approx(.42 * .50 * .8)
    assert row["metadata"]["frontier"] == json.loads(canonical_json(asdict(selected)))
    supplied = _valuation_input(snapshot, current.policy, route, route.steps[2],
        reach=ProbabilityEvidence(**row["metadata"]["valuation"]["reach"][0]["probability"]),
        readiness=ProbabilityEvidence(**row["metadata"]["valuation"]["readiness"][affected[0]]["recall"]))
    supplied = replace(supplied, provenance=(valuation.VALUATION_VERSION, current.as_of.isoformat()))
    enriched = _policy_card(_policy_projection(snapshot, route, policy=current.policy,
                                               valuation_inputs=(supplied,)), supplied.card_id)
    assert enriched.decisions == (supplied,)
    assert supplied.readiness == ProbabilityEvidence.exact(0)
    missing_effect = valuation.evaluate_opening_card_valuation(
        replace(current, candidates=(replace(candidate, effect=None, effect_reference=None),)))
    assert missing_effect["evaluations"][0]["rows"][0]["score"] is None
