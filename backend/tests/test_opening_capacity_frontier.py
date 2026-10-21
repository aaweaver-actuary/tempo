"""Deterministic default-off, one-decision capacity policy regression matrix."""

from dataclasses import FrozenInstanceError, replace
import itertools
import subprocess
import sys

import pytest

from app.study.openings.capacity_frontier import CapacityCandidate, CapacityRoute, CapacitySnapshot, propose_capacity_expansion, plan_prioritized_opening_admissions

DAY = "2026-10-12"
MISS = "miss"


def candidate(
    card_id="child", *, repertoire="rep", parent="root", introduced=True, **changes
):
    route = CapacityRoute(repertoire, 1, "line", 1, parent, introduced)
    return CapacityCandidate(card_id, repertoire, (route,), **changes)


def snapshot(
    *candidates, normal=(), allowances=(("rep", 10),), capacity=10, enabled=True
):
    return CapacitySnapshot(enabled, DAY, normal, allowances, capacity, candidates)


def proposals(value):
    return propose_capacity_expansion(value, miss_reason=MISS)


def selected(value):
    return tuple(
        (proposal.candidate.repertoire_id, proposal.candidate.card_id)
        for proposal in proposals(value)
    )


def test_capacity_flag_disabled_preserves_normal_frontier():
    assert proposals(snapshot(candidate(), enabled=False)) == ()


def test_capacity_sufficient_normal_candidates_leave_no_expansion_allowance():
    assert (
        proposals(
            snapshot(candidate(), normal=(("rep", "root"),), allowances=(("rep", 0),))
        )
        == ()
    )


def test_capacity_insufficient_normal_candidates_fill_only_residual_capacity():
    assert selected(
        snapshot(candidate("a"), candidate("b"), allowances=(("rep", 1),), capacity=1)
    ) == (("rep", "a"),)


def test_capacity_no_available_capacity_returns_empty():
    assert proposals(snapshot(candidate(), capacity=0)) == ()


def test_capacity_no_additional_frontier_candidates_returns_empty():
    assert proposals(snapshot()) == ()


def test_capacity_introduced_unreviewed_parent_needs_no_maturity():
    assert selected(snapshot(candidate())) == (("rep", "child"),)


def test_capacity_reviewed_immature_parent_retains_normal_priority_and_selection():
    normal_child = candidate().priority_row()
    assert plan_prioritized_opening_admissions(
        (normal_child,), {}, DAY, 1, miss_reason=MISS
    ) == [("rep", normal_child)]
    assert proposals(snapshot(candidate(), normal=(("rep", "child"),))) == ()


def test_capacity_never_introduced_parent_requires_normal_selection_first():
    unseen = candidate(introduced=False)
    assert proposals(snapshot(unseen)) == ()
    assert selected(snapshot(unseen, normal=(("rep", "root"),))) == (("rep", "child"),)


def test_capacity_one_level_boundary_excludes_unreviewed_earlier_prerequisite():
    deep = candidate("grandchild", parent="child")
    deep = replace(
        deep,
        routes=(
            replace(
                deep.routes[0], decision_index=2, earlier_unreviewed_card_ids=("root",)
            ),
        ),
    )
    assert selected(snapshot(candidate(), deep)) == (("rep", "child"),)


def test_capacity_repeated_refresh_cannot_use_relaxed_admission_as_exposure():
    first = snapshot(candidate(introduced=False), normal=(("rep", "root"),))
    assert len(proposals(first)) == 1
    grandchild = candidate("grandchild", parent="child")
    route = replace(
        grandchild.routes[0], decision_index=2, earlier_unreviewed_card_ids=("root",)
    )
    assert proposals(snapshot(replace(grandchild, routes=(route,)))) == ()
    assert selected(
        snapshot(
            replace(
                grandchild, routes=(replace(route, earlier_unreviewed_card_ids=()),)
            )
        )
    ) == (("rep", "grandchild"),)


def test_capacity_few_prefixes_fill_ten_without_any_mature_cards():
    children = tuple(
        candidate(f"{root}-child-{index}", parent=root, introduced=False)
        for root in ("prefix-a", "prefix-b")
        for index in range(4)
    )
    value = snapshot(
        *children,
        normal=(("rep", "prefix-a"), ("rep", "prefix-b")),
        allowances=(("rep", 8),),
        capacity=8,
    )
    assert len(proposals(value)) == 8


def test_capacity_shared_descendant_consumes_one_admission():
    shared = (candidate("shared", repertoire="a"), candidate("shared", repertoire="b"))
    assert selected(snapshot(*shared, allowances=(("a", 1), ("b", 1)), capacity=2)) == (
        ("a", "shared"),
    )


def test_capacity_transposition_one_complete_route_suffices_without_splicing():
    shared = candidate("shared")
    missing_a = replace(
        shared.routes[0], line_id="a", earlier_unreviewed_card_ids=("a-root",)
    )
    missing_b = replace(
        shared.routes[0], line_id="b", earlier_unreviewed_card_ids=("b-root",)
    )
    assert proposals(snapshot(replace(shared, routes=(missing_a, missing_b)))) == ()
    good_b = replace(missing_b, earlier_unreviewed_card_ids=())
    result = proposals(snapshot(replace(shared, routes=(missing_a, good_b))))
    assert result[0].route.line_id == "b"


def test_capacity_multiple_repertoires_keep_separate_allowances():
    assert selected(
        snapshot(
            candidate("a", repertoire="a"),
            candidate("b", repertoire="b"),
            allowances=(("a", 0), ("b", 1)),
        )
    ) == (("b", "b"),)


def test_capacity_repertoire_specific_allowances_do_not_transfer():
    assert (
        len(
            proposals(
                snapshot(
                    candidate("a"),
                    candidate("b"),
                    allowances=(("rep", 1), ("empty", 9)),
                )
            )
        )
        == 1
    )


def test_capacity_total_capacity_limits_competing_repertoires():
    assert (
        len(
            proposals(
                snapshot(
                    candidate("a", repertoire="a"),
                    candidate("b", repertoire="b"),
                    allowances=(("a", 3), ("b", 3)),
                    capacity=1,
                )
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "defect", ["current_ready_publication", "structurally_valid", "single_decision"]
)
def test_capacity_invalidated_or_ineligible_routes_are_excluded(defect):
    child = candidate()
    assert (
        proposals(
            snapshot(
                replace(child, routes=(replace(child.routes[0], **{defect: False}),))
            )
        )
        == ()
    )


def test_capacity_duplicate_candidates_and_normal_shared_selection_are_suppressed():
    child = candidate()
    assert len(proposals(snapshot(child, child))) == 1
    assert proposals(snapshot(child, normal=(("other", "child"),))) == ()


def test_capacity_preserves_gameplay_breadth_and_score_priorities_within_tier():
    a = candidate("a", priority_score=9, completed_line_ids_json='["one"]')
    b = candidate("b", priority_score=8, completed_line_ids_json='["one"]')
    c = candidate("c", priority_score=1, completed_line_ids_json='["two"]')
    game = candidate("game", gameplay_priority_reason="game", priority_date=DAY)
    assert selected(snapshot(b, c, a, game)) == (
        ("rep", "game"),
        ("rep", "a"),
        ("rep", "c"),
        ("rep", "b"),
    )


def test_capacity_deterministic_order_under_candidate_and_allowance_permutations():
    candidates = (candidate("b"), candidate("a"), candidate("c", repertoire="other"))
    expected = selected(snapshot(*candidates, allowances=(("rep", 2), ("other", 1))))
    for order in itertools.permutations(candidates):
        assert (
            selected(snapshot(*order, allowances=(("other", 1), ("rep", 2))))
            == expected
        )


def test_capacity_previously_introduced_parent_precedes_new_normal_parent():
    old = candidate("old", priority_score=0)
    new = candidate("new", parent="new-root", introduced=False, priority_score=100)
    assert selected(snapshot(new, old, normal=(("rep", "new-root"),), capacity=1)) == (
        ("rep", "old"),
    )


def test_capacity_miss_priority_cannot_bypass_relaxed_allowances():
    assert (
        len(
            proposals(
                snapshot(
                    candidate("a", gameplay_priority_reason=MISS),
                    candidate("b", gameplay_priority_reason=MISS),
                    allowances=(("rep", 1),),
                )
            )
        )
        == 1
    )


def test_capacity_route_cannot_borrow_membership_or_use_self_parent():
    child = candidate()
    foreign = replace(child.routes[0], repertoire_id="other")
    assert proposals(snapshot(replace(child, routes=(foreign,)))) == ()
    assert proposals(snapshot(candidate(parent="child"))) == ()


def test_capacity_inadmissible_card_and_conflicting_duplicates_are_explicit():
    assert proposals(snapshot(candidate(admissible=False))) == ()
    with pytest.raises(ValueError, match="Conflicting capacity candidate"):
        proposals(snapshot(candidate(), candidate(priority_score=1)))


def test_capacity_inputs_are_frozen_and_policy_is_idempotent():
    value = snapshot(candidate())
    assert proposals(value) == proposals(value)
    assert value.remaining_allowances == (("rep", 10),)
    with pytest.raises(FrozenInstanceError):
        value.candidates[0].card_id = "changed"


def test_capacity_pure_module_has_no_database_network_clock_chess_or_fsrs_dependency():
    script = """import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    assert name not in ('os','datetime','time','chess','fsrs','socket','requests','tempo_legacy_database.connections','app.main'), name
    return original_import(name, *args, **kwargs)
import dataclasses, json
builtins.__import__ = guarded_import
from app.study.openings.capacity_frontier import CapacitySnapshot, propose_capacity_expansion
assert propose_capacity_expansion(CapacitySnapshot(True,'day',(),(),0),miss_reason='miss') == ()
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_capacity_route_permutations_and_duplicate_occurrences_are_deterministic():
    child = candidate()
    first_route = child.routes[0]
    second_route = replace(first_route, line_id="second")
    expected = proposals(snapshot(replace(child, routes=(first_route, second_route))))
    assert (
        proposals(
            snapshot(replace(child, routes=(second_route, first_route, first_route)))
        )
        == expected
    )
