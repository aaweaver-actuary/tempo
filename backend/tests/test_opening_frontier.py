"""Sequence eligibility contracts, independent of production admissions."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
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
