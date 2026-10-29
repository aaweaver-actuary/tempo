"""Synthetic, legal-route policy proofs: no server, store, engine or provider."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest
import chess
from tempo_evidence_math import opening_position_key
from app.contracts.product_v02.presentation import DecisionComponent, JoinPlan, OpponentCue, Presentation

from app.study.recombination.models import (
    CleanStreak, ComponentRef, JoinMember, ParentConnection, ReviewEvent, RouteContext, RouteUse,
)
from app.study.recombination.policy import plan_join, reduce_clean_streak


REVIEW_TIME = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)


def review(index, presentation_id="A", **changes):
    values = dict(
        event_id=f"{presentation_id}:event:{index}", presentation_id=presentation_id,
        presentation_revision=1, evidence_epoch="initial", occurred_at=REVIEW_TIME + timedelta(minutes=index),
        source="study", outcome="correct", attempt_id=f"{presentation_id}:attempt:{index}",
        review_id=f"{presentation_id}:review:{index}", complete=True, assistance="unassisted",
    )
    values.update(changes)
    return ReviewEvent(**values)


def streak(presentation_id="A", reviews=3):
    state = CleanStreak(presentation_id, 1, "initial")
    for index in range(reviews):
        state = reduce_clean_streak(state, review(index, presentation_id))
    return state


def test_two_of_three_does_not_qualify():
    state = streak(reviews=2)
    assert state.count == 2 and not state.eligible
    left, right = member("A", 0, 1, streak=state), member("B", 2, 3)
    assert plan_join((left, right), context()).plan is None


def test_three_clean_reviews_qualify_each_member():
    assert streak("A").eligible and streak("B").eligible
    assert len(streak().completed_review_ids) == 3
    assert plan_join((member("A", 0, 1), member("B", 2, 3)), context()).plan is not None


@pytest.mark.parametrize("outcome,assistance", [
    ("wrong", "unassisted"), ("hint", "assisted"), ("reveal", "assisted"),
    ("guided", "assisted"), ("again", "unassisted"), ("correct", "assisted"),
])
def test_wrong_hint_guided_and_again_reset(outcome, assistance):
    reset = reduce_clean_streak(streak(), review(3, outcome=outcome, assistance=assistance))
    assert reset.count == 0
    correction = review(4, attempt_id="A:attempt:3", review_id="correction")
    assert reduce_clean_streak(reset, correction).count == 0


def test_partial_and_duplicate_do_not_increment():
    state = streak(reviews=2)
    partial = review(3, complete=False, review_id=None)
    assert reduce_clean_streak(state, partial).count == 2
    completed = review(4, attempt_id=partial.attempt_id)
    result = reduce_clean_streak(reduce_clean_streak(state, partial), completed)
    assert result.count == 3
    assert reduce_clean_streak(result, completed) is result
    another_event = replace(completed, event_id="transport-retry", occurred_at=REVIEW_TIME + timedelta(minutes=5))
    assert reduce_clean_streak(result, another_event).completed_review_ids == result.completed_review_ids
    another_review = replace(another_event, event_id="teaching-repeat", review_id="other-review")
    assert reduce_clean_streak(result, another_review).completed_review_ids == result.completed_review_ids
    # Same durable review cannot be counted again under a different attempt.
    empty = CleanStreak("A", 1, "initial")
    once = reduce_clean_streak(empty, completed)
    assert reduce_clean_streak(once, replace(another_event, attempt_id="other-attempt")).count == 1


def test_same_day_distinct_reviews_allowed():
    state = streak()
    assert state.count == 3
    assert state.last_event_at.date() == REVIEW_TIME.date()


def test_real_game_success_does_not_increment():
    state = streak(reviews=2)
    assert reduce_clean_streak(state, review(3, source="real_game")) is state
    assert reduce_clean_streak(state, review(4, source="real_game", outcome="wrong")) is state
    admitted = review(5, source="admitted_lapse", outcome="failure", attempt_id=None, review_id=None)
    assert reduce_clean_streak(state, admitted).count == 0


def test_unknown_historical_assistance_cannot_qualify():
    assert reduce_clean_streak(streak(reviews=2), review(3, assistance="unknown")).count == 0


def test_split_requires_fresh_streak():
    before = streak()
    fragment = CleanStreak("A", 1, "accepted-split:1")
    assert fragment.count == 0
    assert reduce_clean_streak(fragment, review(4)) is fragment
    for index in range(3):
        fragment = reduce_clean_streak(fragment, review(index, evidence_epoch="accepted-split:1"))
    assert before.eligible and fragment.eligible


def test_owner_orders_events_and_stale_revisions_do_not_resurrect():
    state = streak()
    with pytest.raises(ValueError, match="out_of_order_event"):
        reduce_clean_streak(state, review(-1))
    assert reduce_clean_streak(state, review(4, presentation_revision=2)) is state
    assert reduce_clean_streak(state, review(4, presentation_id="other")) is state


def test_retried_old_failure_cannot_reset_later_clean_reviews():
    failure = review(0, outcome="again")
    state = reduce_clean_streak(CleanStreak("A", 1, "initial"), failure)
    for index in (1, 2, 3):
        state = reduce_clean_streak(state, review(index))
    assert state.eligible
    # Stable review and attempt facts deduplicate before chronology checks.
    assert reduce_clean_streak(state, replace(failure, event_id="retry-again")) is state
    assert reduce_clean_streak(state, replace(failure, event_id="retry-renamed-review", review_id="renamed")) is state
    assert reduce_clean_streak(state, replace(failure, event_id="retry-renamed-attempt", attempt_id="renamed")) is state


# Frozen fixture for #483's structural authored-route read capability.
@dataclass(frozen=True)
class FrozenRoute:
    route_id: str = "main-line"
    repertoire_id: str = "white-repertoire"
    learner_color: str = "white"
    policy_id: str = "authored-policy"
    source_revision: str = "authored-source:7"
    starting_fen: str = chess.STARTING_FEN
    moves: tuple[str, ...] = ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4")


def frame(route, ply):
    board = chess.Board(route.starting_fen)
    for uci in route.moves[:ply]:
        board.push_uci(uci)
    return board.fen(en_passant="fen")


def member(name, start, stop, route=None, **changes):
    route = route or FrozenRoute()
    board = chess.Board(frame(route, start))
    components, cues = [], []
    for ply in range(start, stop):
        position = opening_position_key(board.fen())
        own = board.turn == (route.learner_color == "white")
        board.push_uci(route.moves[ply])
        resulting = opening_position_key(board.fen())
        if own:
            components.append(DecisionComponent(decision_id=f"decision:{name}:{ply}", position_key=position,
                                                response_uci=route.moves[ply], resulting_position_key=resulting))
        elif components and ply + 1 < stop:
            cues.append(OpponentCue(position_key=position, move_uci=route.moves[ply], resulting_position_key=resulting))
    state = changes.get("streak", streak(name))
    facts = Presentation(presentation_id=name, revision=1, repertoire_id=route.repertoire_id,
                         repertoire_revision=7, learner_color=route.learner_color, variant="standard",
                         route_id=route.route_id, components=tuple(components), opponent_cues=tuple(cues),
                         clean_streak=state.count, introduced=True, lineage=(), recorded_parent_id=None)
    values = dict(presentation=facts, streak=state, current_revision=1,
                  route_uses=(RouteUse(route.route_id, f"{route.route_id}:{name}:{start}", start, stop),),
                  starting_fen=frame(route, start), ending_fen=frame(route, stop),
                  moves=route.moves[start:stop], available=True)
    values.update(changes)
    return JoinMember(**values)


def context(route=None, **changes):
    values = dict(route=route or FrozenRoute(), repertoire_revision=7,
                  variant="standard", complete=True, available=True, expected_study_revision=10,
                  plan_id="join-command:10", result_presentation_id="new-composite:10")
    values.update(changes)
    return RouteContext(**values)


def reference(value):
    return ComponentRef(value.presentation.presentation_id, value.presentation.revision,
                        value.streak.evidence_epoch, value.route_uses[0])


def codes(decision):
    return {hold.code for hold in decision.holds}


def test_parent_preferred_over_other_neighbour():
    a, b, c = member("A", 0, 1), member("B", 2, 3), member("C", 4, 5)
    parent = ParentConnection(reference(b), reference(c))
    chosen = plan_join((a, b, c), context(parent_connections=(parent,)))
    assert chosen.plan.evidence.components == (reference(b), reference(c))
    assert chosen.plan.evidence.reason == "recorded_parent"
    assert chosen.plan.evidence.route_moves == FrozenRoute().moves
    # Missing/stale parent authority is held, never silently replaced by a FEN neighbour.
    missing = replace(parent, parent=replace(parent.parent, presentation_id="missing"))
    held = plan_join((b, c), context(parent_connections=(missing,)))
    assert held.plan is None and "unavailable_parent" in codes(held)
    stale = replace(parent, child=replace(parent.child, presentation_revision=2))
    held = plan_join((b, c), context(parent_connections=(stale,)))
    assert held.plan is None and "stale_parent_revision" in codes(held)


def test_ab_starts_zero_and_c_cannot_chain():
    a, b, c = member("A", 0, 1), member("B", 2, 3), member("C", 4, 5)
    first = plan_join((a, b, c), context()).plan
    assert first.evidence.components == (reference(a), reference(b))
    assert first.resulting_clean_streak == 0 and first.introduction_slots == 0 and first.result.introduced
    ab = member("AB", 0, 3, streak=CleanStreak("AB", 1, "join:AB:1"))
    assert plan_join((ab, c), context()).plan is None
    state = ab.streak
    for index in range(3):
        state = reduce_clean_streak(state, review(index, "AB", evidence_epoch="join:AB:1"))
    ab = replace(ab, streak=state, presentation=ab.presentation.model_copy(update={"clean_streak":3}))
    next_plan = plan_join((ab, c), context()).plan
    assert tuple(item.presentation_id for item in next_plan.evidence.components) == ("AB", "C")
    assert next_plan.resulting_clean_streak == 0
    assert next_plan.evidence.moves == FrozenRoute().moves[:5]


def test_fork_ambiguous_parent_overlap_and_conflict_refused():
    a, b, c = member("A", 0, 1), member("B", 1, 3), member("C", 4, 5)
    fork = replace(b, moves=("c7c5", "g1f3"))
    refused = plan_join((a, fork), context())
    assert refused.plan is None and "competing_opponent_fork" in codes(refused)
    parents = (ParentConnection(reference(a), reference(b)), ParentConnection(reference(c), reference(b)))
    refused = plan_join((a, b, c), context(parent_connections=parents))
    assert refused.plan is None and "ambiguous_parent" in codes(refused)
    overlap = member("overlap", 0, 3)
    refused = plan_join((a, overlap), context())
    assert refused.plan is None and "overlapping_components" in codes(refused)
    other = replace(FrozenRoute(), route_id="conflict-line", moves=("e2e4", "e7e5", "b1c3"))
    refused = plan_join((a, b), context(authored_routes=(other,)))
    assert refused.plan is None and "conflicting_own_response" in codes(refused)


def test_opponent_cues_and_exact_adjacency_preserved():
    a, b = member("A", 0, 1), member("B", 2, 3)
    plan = plan_join((a, b), context()).plan
    assert plan.evidence.moves == ("e2e4", "e7e5", "g1f3")
    assert plan.evidence.intervening_opponent_moves == ("e7e5",)
    assert plan.evidence.starting_fen == a.starting_fen
    assert plan.evidence.ending_fen == b.ending_fen
    assert plan.evidence.components[1].route_use.start_ply == 2
    wrong_start = replace(b, starting_fen=chess.STARTING_FEN)
    refused = plan_join((a, wrong_start), context())
    assert refused.plan is None and "endpoint_mismatch" in codes(refused)
    # No bridge across an absent learner decision, even on the same authored route.
    refused = plan_join((a, member("C", 4, 5)), context())
    assert refused.plan is None and "non_adjacent" in codes(refused)
    # A matching endpoint is not permission to combine another route occurrence.
    elsewhere = replace(b, route_uses=(replace(b.route_uses[0], route_id="other-route"),))
    assert plan_join((a, elsewhere), context()).plan is None


def test_other_memberships_retained():
    a, b = member("A", 0, 1), member("B", 2, 3)
    other_use = RouteUse("sicilian-route", "shared-A-in-sicilian", 0, 1)
    a = replace(a, route_uses=(*a.route_uses, other_use))
    other_branch = replace(FrozenRoute(), route_id="sicilian-route", moves=("e2e4", "c7c5", "g1f3"))
    plan = plan_join((a, b), context(authored_routes=(other_branch,))).plan
    assert plan.evidence.retained_uses[0].presentation_id == "A"
    assert plan.evidence.retained_uses[0].route_use == other_use
    assert plan.evidence.components[0].route_use.route_id == "main-line"
    assert not any(hasattr(plan, name) for name in ("delete_card_ids", "fsrs_state", "schedule", "sql"))


def test_exact_shared_fixture_and_obligation_ids_are_preserved():
    import json
    from pathlib import Path
    payload = json.loads((Path(__file__).resolve().parents[2] /
                          "contracts/product-v02/fixtures/join-a-b-to-ab.json").read_text())["payload"]
    shared = JoinPlan.model_validate_json(json.dumps(payload))
    route = replace(FrozenRoute(), route_id="route-a", repertoire_id="white", moves=("e2e4", "e7e5", "g1f3"))
    inputs = []
    for value, start, stop in ((shared.left, 0, 1), (shared.right, 2, 3)):
        state = CleanStreak(value.presentation_id, value.revision, "fixture-proven")
        for index in range(3):
            state = reduce_clean_streak(state, review(index, value.presentation_id,
                                        presentation_revision=value.revision, evidence_epoch="fixture-proven"))
        inputs.append(JoinMember(value, state, value.revision,
                      (RouteUse(route.route_id, f"occurrence:{value.presentation_id}", start, stop),),
                      frame(route, start), frame(route, stop), route.moves[start:stop], available=True))
    supplied = context(route, repertoire_revision=1, expected_study_revision=7,
                       plan_id=shared.plan_id, result_presentation_id=shared.result.presentation_id,
                       unresolved_obligation_ids=shared.unresolved_obligation_ids)
    result = plan_join(tuple(inputs), supplied).plan
    assert isinstance(result, JoinPlan)
    assert JoinPlan.model_validate(result.model_dump(exclude={"evidence"})) == shared
    assert result.unresolved_obligation_ids == ("existing-game-obligation",)
    assert result.evidence.reason == "recorded_parent"


def test_leading_opponent_execution_cue_is_included_once():
    a, b = member("A", 0, 1), member("B", 1, 3)
    plan = plan_join((a, b), context()).plan
    assert plan.evidence.moves == ("e2e4", "e7e5", "g1f3")
    assert tuple(cue.move_uci for cue in plan.result.opponent_cues) == ("e7e5",)
    assert plan.connecting_cues == plan.result.opponent_cues
    assert plan.right.opponent_cues == ()


def test_shared_decision_cue_and_streak_mismatch_are_refused():
    a, b = member("A", 0, 1), member("B", 2, 3)
    changed = b.presentation.components[0].model_copy(update={"response_uci":"b1c3"})
    held = plan_join((a, replace(b, presentation=b.presentation.model_copy(update={"components":(changed,)}))), context())
    assert held.plan is None and "decision_sequence_mismatch" in codes(held)
    composite = member("AB", 0, 3)
    held = plan_join((replace(composite, presentation=composite.presentation.model_copy(update={"opponent_cues":()})),
                      member("C", 4, 5)), context())
    assert held.plan is None and "cue_sequence_mismatch" in codes(held)
    held = plan_join((a, replace(b, presentation=b.presentation.model_copy(update={"clean_streak":0}))), context())
    assert held.plan is None and "needs_clean_reviews" in codes(held)


def test_unavailable_or_unsupported_execution_facts_are_held():
    a, b = member("A", 0, 1), member("B", 2, 3)
    held = plan_join((a, replace(b, available=False)), context())
    assert held.plan is None and "unavailable_member" in codes(held)
    held = plan_join((a, replace(b, starting_fen="not-a-position")), context())
    assert held.plan is None and "unsupported_position" in codes(held)


def test_existing_boundary_guard_covers_only_the_changed_policy():
    from pathlib import Path
    from test_product_contracts import boundary_errors, has_import_cycle
    root = Path(__file__).resolve().parents[1] / "app/study/recombination"
    modules = {f"app.study.recombination.{name}": (root / f"{name}.py").read_text()
               for name in ("models", "policy")}
    assert not boundary_errors([("study", source, name) for name, source in modules.items()])
    assert not has_import_cycle(modules)


@pytest.mark.parametrize("change,code", [
    ({"active_attempt": True}, "active_attempt"),
    ({"pending_attempt": True}, "active_attempt"),
    ({"current_revision": 2}, "stale_revision"),
    ({"streak": streak("B", reviews=2)}, "needs_clean_reviews"),
])
def test_application_holds_are_explicit(change, code):
    a, b = member("A", 0, 1), member("B", 2, 3, **change)
    held = plan_join((a, b), context())
    assert held.plan is None and code in codes(held)


@pytest.mark.parametrize("change,code", [
    ({"introduced": False}, "unintroduced_member"),
    ({"variant": "chess960"}, "scope_mismatch"),
    ({"repertoire_id": "black-repertoire"}, "scope_mismatch"),
    ({"learner_color": "black"}, "scope_mismatch"),
])
def test_ineligible_member_facts_refused(change, code):
    a, b = member("A", 0, 1), member("B", 2, 3)
    b = replace(b, presentation=b.presentation.model_copy(update=change))
    held = plan_join((a, b), context())
    assert held.plan is None and code in codes(held)


def test_unsupported_route_and_incomplete_scope_refused():
    members = (member("A", 0, 1), member("B", 2, 3))
    assert "unsupported_variant" in codes(plan_join(members, context(variant="chess960")))
    assert "unavailable_route" in codes(plan_join(members, context(complete=False)))
    assert "unavailable_route" in codes(plan_join(members, context(available=False)))
    illegal = replace(FrozenRoute(), moves=("e2e5",))
    assert "unsupported_position_or_route" in codes(plan_join(members, context(illegal)))
    stale = replace(FrozenRoute(), route_id="older", source_revision="old")
    assert "stale_revision" in codes(plan_join(members, context(authored_routes=(stale,))))


def test_stable_tie_breaking_chooses_one_pair_per_route_pass():
    from itertools import permutations
    members = (member("A", 0, 1), member("B", 2, 3), member("C", 4, 5), member("D", 6, 7))
    expected = plan_join(members, context())
    for supplied in permutations(members):
        assert plan_join(supplied, context()) == expected
    assert tuple(ref.presentation_id for ref in expected.plan.evidence.components) == ("A", "B")


def test_black_learner_and_move_clocks_use_exact_position_identity():
    route = replace(FrozenRoute(), learner_color="black")
    a, b = member("A", 0, 2, route), member("B", 2, 4, route)
    b = replace(b, starting_fen=b.starting_fen.rsplit(" ", 2)[0] + " 17 39")
    plan = plan_join((a, b), context(route)).plan
    assert plan.left.learner_color == "black" and plan.evidence.moves == route.moves[:4]


def test_inputs_unchanged_no_io(monkeypatch):
    import builtins
    import copy
    import socket
    import sqlite3
    import subprocess
    members = (member("A", 0, 1), member("B", 2, 3))
    supplied = context()
    original = copy.deepcopy((members, supplied))
    state, event = streak(reviews=2), review(4)

    def forbidden(*args, **kwargs):
        raise AssertionError("pure policy attempted I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert plan_join(members, supplied).plan is not None
    assert reduce_clean_streak(state, event).count == 3
    assert (members, supplied) == original and state.count == 2
    with pytest.raises(AttributeError):
        members[0].streak.count = 0
    plan = plan_join(members, supplied).plan
    with pytest.raises((AttributeError, ValueError)):
        plan.resulting_clean_streak = 3
