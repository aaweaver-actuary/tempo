"""Global first-departure election; synthetic legal games and supplied owners."""
from copy import deepcopy

import chess
import pytest

from app.contracts.product_v02.repertoire import (
    DecisionPresentation, IntroductionGameScope, RepertoireSnapshot, RouteBoundary,
)
from app.games.feedback.first_boundary import select_first_boundary
from tempo_game_review_contracts import AuthoredRoute, CanonicalGame, RepertoireSnapshot as Membership, replay_line

START = chess.STARTING_FEN
NOW = "2026-10-30T00:00:00Z"


def game(moves=("d2d4",), *, color="white", start=START):
    return CanonicalGame(workspace_id="fixture", game_id="source-game", game_revision=1,
        source_id="lichess", source_revision=1, source_digest="a" * 64,
        starting_fen=start, moves=list(moves), learner_color=color, played_at=NOW)


def snapshot(moves=("e2e4", "e7e5", "g1f3"), *, repertoire="rep", route_id="route",
             color="white", start=START, deliberate=True, complete=True, mappings=True,
             introduction="unknown", available=True):
    authored = AuthoredRoute(repertoire_id=repertoire, route_id=route_id, policy_id=repertoire + "-policy",
        learner_color=color, starting_fen=start, moves=list(moves))
    bindings = tuple(DecisionPresentation(decision_id=f"decision-{route_id}-{frame['ply']}",
        repertoire_id=repertoire, position_key=frame["comparison_key"], selected_response_uci=frame["move"],
        presentation_id=f"card-{route_id}", presentation_revision=1, repertoire_revision=1,
        introduction=introduction, introduced_at=None, available=available,
        route_id=route_id, route_ply=frame["ply"])
        for frame in replay_line(start, moves)[:-1] if frame["turn"] == color) if mappings else ()
    return RepertoireSnapshot(membership=Membership(workspace_id="fixture", snapshot_id="snapshot-"+repertoire,
        revision=1, digest="b" * 64, source_revision="source-"+repertoire, captured_at=NOW,
        scope_id=repertoire, scope_repertoire_ids=[repertoire], complete=complete, routes=[authored]),
        variant="standard", learner_color=color, decision_presentations=bindings,
        boundaries=(RouteBoundary(route_id=route_id, terminal_ply=len(moves), deliberate=deliberate),))


def select(current=None, *snapshots, **kwargs):
    current = current or game()
    return select_first_boundary(current, snapshots=snapshots or (snapshot(),),
        source=IntroductionGameScope(provider="lichess", game_id=current.game_id,
            source_revision=current.source_revision, source_digest=current.source_digest), **kwargs)


def test_first_boundary_accepts_any_surviving_authored_alternative():
    current = game(("e2e4", "c7c5"))
    first = snapshot(repertoire="e4")
    other = snapshot(("d2d4", "d7d5"), repertoire="d4", route_id="d4")
    result = select(current, first, other)
    assert (result.status, result.actor, result.ply, result.expected_uci) == ("opponent_gap", "opponent", 2, ("e7e5",))
    assert result.decision is None
    assert select(current, other, first) == result


def test_opponent_first_gap_blocks_later_own_effect_after_reentry():
    from test_product_game_findings import FakeReentryIndex, record, route, snapshot as diagnostic_snapshot
    from app.games.feedback.comparison import compare_record, normalize_record
    from app.games.feedback.coordinator import project_encounters
    authored = route(("g1f3", "d7d5", "g2g3", "c7c5", "f1g2"), reentry=4)
    captured = diagnostic_snapshot(authored)
    original = record(("g1f3", "c7c5", "g2g3", "d7d5", "f1h3"))
    current = normalize_record(original, provider="lichess", account="Learner", workspace_id="workspace")
    findings = compare_record(original, provider="lichess", account="Learner", workspace_id="workspace",
        snapshot=captured, reentry_lookup=FakeReentryIndex(captured, 4))
    assert [(item.kind, item.ply) for item in findings] == [("opponent_gap", 2), ("confirmed_miss", 5)]
    source = IntroductionGameScope(provider="lichess", game_id=current.game_id,
        source_revision=current.source_revision, source_digest=current.source_digest)
    result = select_first_boundary(current, snapshots=(captured,), source=source)
    assert (result.status, result.ply) == ("opponent_gap", 2)
    assert project_encounters(current, snapshots=(captured,), findings=findings, source=source,
        detected_at=NOW, occurred_at=current.played_at) == ()


def test_learner_first_miss_emits_one_exact_target():
    from app.games.feedback.coordinator import project_encounters
    from app.games.feedback.comparison import compare_game
    current, captured = game(("e2e4", "e7e5", "d2d4")), snapshot()
    result = select(current, captured)
    assert (result.status, result.ply, result.actor) == ("own_actionable", 3, "learner")
    assert result.decision.presentation_id == "card-route" and result.decision.route_ply == 2
    assert result.position_key == replay_line(START, current.moves)[2]["comparison_key"]
    findings = compare_game(current, provider="lichess", snapshot=captured)
    command, = project_encounters(current, findings=findings + findings, snapshots=(captured,),
        source=result.source, detected_at=NOW, occurred_at=current.played_at)
    assert command.key.occurrence_ply == 3 and command.observation == "missed"
    assert command.decision == result.decision


def test_correct_unseen_encounter_has_no_urgent_consequence():
    from app.games.feedback.comparison import compare_game
    from app.games.feedback.coordinator import project_encounters
    current, captured = game(("e2e4", "e7e5", "g1f3")), snapshot(introduction="unintroduced")
    result = select(current, captured)
    assert result.status == "covered" and result.ply is None
    assert project_encounters(current, findings=compare_game(current, provider="lichess", snapshot=captured),
        snapshots=(captured,), source=result.source, detected_at=NOW, occurred_at=current.played_at) == ()


def test_interior_card_decision_maps_before_wrong_move():
    result = select(game(("e2e4", "e7e5", "d2d4")), snapshot())
    assert result.decision.decision_id == "decision-route-2"
    assert result.decision.presentation_id == "card-route"
    assert result.decision.route_ply == 2 and result.decision.selected_response_uci == "g1f3"


@pytest.mark.parametrize("available", [False, True])
def test_membership_survives_missing_or_ambiguous_physical_mapping(available):
    captured = snapshot(mappings=available, available=False)
    current = game(("e2e4", "e7e5", "d2d4"))
    result = select(current, captured)
    assert (result.status, result.ply) == ("own_unmapped", 3)
    assert result.expected_uci == ("g1f3",) and result.decision is None
    assert select(game(("e2e4", "e7e5", "g1f3")), captured).status == "covered"


def test_unsupported_alternative_does_not_authorize_ambiguous_target():
    first = snapshot(("e2e4",), repertoire="e4")
    other = snapshot(("d2d4",), repertoire="d4", route_id="d4")
    result = select(game(("c2c4",)), first, other)
    assert result.status == "own_unmapped" and result.expected_uci == ("d2d4", "e2e4")
    same_response_other_card = snapshot(("e2e4",), repertoire="other", route_id="other")
    result = select(game(), first, same_response_other_card)
    assert result.status == "own_unmapped" and result.decision is None


def test_conflicting_policy_context_is_not_erased_by_equal_physical_mapping():
    from test_product_game_findings import equivalent_routes, record, normalize_record
    original = record()
    captured = equivalent_routes(original, 2)
    authored = list(captured.membership.routes)
    authored[-1] = authored[-1].model_copy(update={"policy_id": "conflicting-policy"})
    captured = captured.model_copy(update={"membership": captured.membership.model_copy(update={"routes": tuple(authored)})})
    current = normalize_record(original, provider="lichess", account="Learner", workspace_id="workspace")
    source = IntroductionGameScope(provider="lichess", game_id=current.game_id,
        source_revision=current.source_revision, source_digest=current.source_digest)
    result = select_first_boundary(current, snapshots=(captured,), source=source)
    assert result.status == "own_unmapped" and result.diagnostic == "ambiguous_authored_policy"
    assert len(result.surviving_routes) == 2 and result.decision is None


@pytest.mark.parametrize("missing", ["incomplete", "unavailable", "conflicting"])
def test_terminal_or_unavailable_scope_cannot_manufacture_uniqueness(missing):
    captured = snapshot()
    if missing == "incomplete":
        result = select(game(), captured.model_copy(update={"membership": captured.membership.model_copy(update={"complete": False})}))
    elif missing == "unavailable":
        result = select(game(), captured, selected_repertoire_ids=("rep", "large"), unavailable_repertoire_ids=("large",))
        assert result.selected_scope[0].repertoire_id == "large" and not result.selected_scope[0].available
    else:
        revised = captured.model_copy(update={"membership": captured.membership.model_copy(update={"revision": 2}),
            "decision_presentations": tuple(item.model_copy(update={"repertoire_revision": 2}) for item in captured.decision_presentations)})
        result = select(game(), captured, revised)
    assert result.status == "unresolved" and result.decision is None


def test_ended_competing_route_is_not_discarded_to_manufacture_target():
    short = snapshot(("e2e4",), repertoire="short", route_id="short")
    long = snapshot(repertoire="long")
    result = select(game(("e2e4", "e7e5", "d2d4")), short, long)
    assert (result.status, result.ply, result.diagnostic) == ("unresolved", 3, "ended_competing_route")
    assert any(item.at_deliberate_end for item in result.surviving_routes)
    assert result.decision is None


def test_unfinished_competing_route_is_not_discarded_to_manufacture_target():
    short = snapshot(("e2e4",), repertoire="short", route_id="short", deliberate=False)
    result = select(game(("e2e4", "e7e5", "d2d4")), short, snapshot(repertoire="long"))
    assert result.status == "unresolved" and result.diagnostic == "authored_continuation_unavailable"
    assert result.decision is None


def test_coverage_end_and_missing_own_continuation_are_not_recall_failures():
    current = game(("e2e4", "e7e5", "d2d4"))
    ended = select(current, snapshot(("e2e4", "e7e5")))
    missing = select(current, snapshot(("e2e4", "e7e5"), deliberate=False))
    assert ended.status == "coverage_end" and ended.decision is None
    assert missing.status == "missing_own_continuation" and missing.expected_uci == () and missing.decision is None


def test_color_and_start_position_define_actor():
    start = replay_line(START, ("e2e4",))[-1]["fen"]
    result = select(game(("c7c5",), color="black", start=start),
        snapshot(("e7e5",), color="black", start=start))
    assert (result.status, result.actor, result.ply) == ("own_actionable", "learner", 1)
    assert result.decision.route_ply == 0
    with pytest.raises(ValueError):
        select(game(("e2e5",)), snapshot())


@pytest.mark.parametrize("introduction", ["unknown", "unintroduced", "introduced"])
def test_unknown_introduction_chronology_does_not_block_exact_miss(introduction):
    captured = snapshot(introduction=introduction)
    result = select(game(), captured)
    assert result.status == "own_actionable" and result.decision.introduction == introduction
    assert result.decision.introduced_at is result.decision.introduced_before_game is None


def test_selection_is_stable_under_scope_order_aliases_and_reimport():
    from test_product_game_findings import equivalent_routes, record, normalize_record
    original = record()
    captured = equivalent_routes(original, 62)
    current = normalize_record(original, provider="lichess", account="Learner", workspace_id="workspace")
    source = IntroductionGameScope(provider="lichess", game_id=current.game_id,
        source_revision=current.source_revision, source_digest=current.source_digest)
    before = deepcopy((current.model_dump(), captured.model_dump()))
    first = select_first_boundary(current, snapshots=(captured,), source=source)
    reordered = captured.model_copy(update={"membership": captured.membership.model_copy(update={
        "routes": tuple(reversed(captured.membership.routes))}),
        "decision_presentations": tuple(reversed(captured.decision_presentations))})
    repeated = select_first_boundary(current, snapshots=(reordered,), source=source)
    assert first == repeated and first.decision.route_id == "alias-00"
    assert (current.model_dump(), captured.model_dump()) == before


@pytest.mark.parametrize("historical", ["boolean", "timestamp"])
def test_alias_chronology_does_not_block_current_physical_target(historical):
    from test_product_game_findings import equivalent_routes, record, normalize_record
    original = record()
    captured = equivalent_routes(original, 2)
    mappings = list(captured.decision_presentations)
    if historical == "boolean":
        mappings[0] = mappings[0].model_copy(update={"introduced_before_game": None, "introduction_game": None})
    else:
        mappings = [item.model_copy(update={"introduction": "introduced",
            "introduced_before_game": None, "introduction_game": None,
            "introduced_at": "2026-10-01T00:00:00Z" if index else "2026-10-01T00:00:00+00:00"})
            for index, item in enumerate(mappings)]
    captured = captured.model_copy(update={"decision_presentations": tuple(mappings)})
    current = normalize_record(original, provider="lichess", account="Learner", workspace_id="workspace")
    source = IntroductionGameScope(provider="lichess", game_id=current.game_id,
        source_revision=current.source_revision, source_digest=current.source_digest)
    result = select_first_boundary(current, snapshots=(captured,), source=source)
    assert result.status == "own_actionable" and result.decision in mappings
    assert result.decision.route_id == "alias-00"


def test_opposite_color_scope_is_not_assumed_from_a_failed_read():
    captured = snapshot()
    opposite = snapshot(("e2e4", "e7e5"), repertoire="black", color="black")
    result = select(game(), captured, opposite, selected_repertoire_ids=("rep", "black"))
    assert result.status == "own_actionable" and len(result.selected_scope) == 1
    absent = select(game(()), opposite, selected_repertoire_ids=("black",))
    assert absent.status == "unresolved" and absent.diagnostic == "no_compatible_repertoire"
    assert absent.selected_scope == ()
    missing = select(game(), captured, selected_repertoire_ids=("rep", "black"), unavailable_repertoire_ids=("black",))
    assert missing.status == "unresolved" and len(missing.selected_scope) == 2


def test_positive_authored_coverage_remains_true_with_incomplete_scope():
    result = select(game(("e2e4",)), snapshot(complete=False))
    assert result.status == "covered" and result.diagnostic == "positive_coverage_with_incomplete_scope"
