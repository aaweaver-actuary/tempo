"""Bounded retained-history application facts; no provider/DB/Study writer."""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from app.contracts.product_v02.games import Finding, GameWindow
from app.games.feedback.history import (HistoryComparison, HistorySelection,
    RetainedHistoryGame, RetainedHistoryPage, preview_history)


def selection(**changes):
    return HistorySelection(workspace_id="legacy", provider="lichess", account="Learner",
        window=GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="second"),
        repertoire_ids=("rep",), speeds=("rapid",), rated=True, **changes)


def finding(kind="confirmed_miss", *, ply=1, played="d2d4"):
    confirmed = kind == "confirmed_miss"
    return Finding(kind=kind, provider="lichess", game_id="retained-game", source_revision=1,
        source_digest="a"*64, ply=ply, position_key="position", expected_uci=("e2e4",),
        played_uci=played, repertoire_id="rep", repertoire_revision=1,
        decision_id="decision-"+str(ply), presentation_id="card-"+str(ply), presentation_revision=1,
        introduced_before_game=True if confirmed else False if kind=="known_unintroduced" else None,
        membership="present", lapse_eligible=confirmed, diagnostic="supplied_owner_finding")


class Reader:
    def __init__(self, games, more=False):
        self.result = RetainedHistoryPage(tuple(games), more)
    def page(self, request):
        return self.result


class Comparer:
    projection_available = False
    def __init__(self, result):
        self.result = result
    def compare(self, *args):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def prepare(reader, comparer, request=None, policy=None):
    return preview_history(request or selection(), reader=reader, comparer=comparer,
        run_id="bounded-history", detected_at="2026-10-27T00:00:00Z", policy_identity=policy)


def test_zero_eligible_misses_with_unknown_authority_is_not_reported_as_correct():
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 4)]),
                   Comparer(ValueError("current_source_mapping_bound_exceeded")))
    assert plan.preview.eligible_lapses == plan.preview.matches == plan.preview.compared_games == 0
    assert plan.preview.unknown_authority_or_mapping_games == 1
    assert plan.preview.matches_are_lower_bound and not plan.preview.execution_ready
    assert plan.preview.diagnostics[0].reason == "current_source_mapping_bound_exceeded"


def test_preview_distinguishes_matches_misses_unintroduced_excluded_and_unreported_positions():
    facts = (finding(), finding("known_unintroduced", ply=2, played="e2e4"),
             finding("known_unavailable", ply=3))
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 4),
                           RetainedHistoryGame("excluded-game", True, 8)], more=True),
                   Comparer(HistoryComparison(4, facts)))
    summary = plan.preview
    assert (summary.selected_games, summary.compared_games, summary.positions_compared) == (2,1,4)
    assert (summary.matches, summary.differing_responses, summary.eligible_lapses) == (1,2,1)
    assert (summary.unintroduced_encounters, summary.excluded_games, summary.unknown_authority_or_mapping_games) == (1,1,1)
    assert summary.unreported_positions == 1 and summary.matches_are_lower_bound
    assert summary.more_available and summary.next_game_id == "excluded-game"


def test_no_reportable_finding_preserves_unresolved_position_count_instead_of_inventing_matches():
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 4)]), Comparer(HistoryComparison(4, ())))
    assert plan.preview.matches == plan.preview.eligible_lapses == 0
    assert plan.preview.positions_compared == plan.preview.unreported_positions == 4
    assert plan.preview.matches_are_lower_bound


def test_position_budget_refuses_execution_without_silently_processing_a_prefix():
    comparer = Comparer(HistoryComparison(4, ()))
    comparer.projection_available = True
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 4),
                           RetainedHistoryGame("later-game", False, 4)]), comparer,
                   selection(maximum_positions=4), policy="published-targeted-owner-policy")
    assert plan.preview.compared_games == 1 and not plan.preview.execution_ready
    assert plan.preview.diagnostics[-1].reason == "history_position_budget_exhausted"


def test_frozen_detection_and_explicit_owner_policy_bind_the_original_run():
    reader = Reader([RetainedHistoryGame("retained-game", False, 4)])
    blocked = prepare(reader, Comparer(HistoryComparison(4, ())))
    with pytest.raises(ValueError, match="projection_and_owner_policy"):
        blocked.require_execution("published-targeted-owner-policy")
    comparer = Comparer(HistoryComparison(4, ()))
    comparer.projection_available = True
    plan = prepare(reader, comparer, policy="published-targeted-owner-policy")
    plan.require_execution("published-targeted-owner-policy")
    with pytest.raises(ValueError, match="policy_changed"):
        plan.require_execution("a-different-policy")
    with pytest.raises(FrozenInstanceError):
        plan.detected_at = "2026-10-28T00:00:00Z"
    assert replace(plan, detected_at="2026-10-28T00:00:00Z").digest != plan.digest
    assert replace(plan, run_id="another-run").task_identity != plan.task_identity
    assert "pgn" not in str(plan.payload()) and "moves_json" not in str(plan.payload())


def test_reader_cannot_repeat_a_game_or_exceed_the_explicit_selection_bound():
    game = RetainedHistoryGame("retained-game", False, 4)
    with pytest.raises(ValueError, match="exceeded_bound_or_repeated"):
        prepare(Reader([game,game]), Comparer(HistoryComparison(4, ())))


def test_run_once_has_no_default_permissive_admission_and_does_not_claim_on_denial():
    from contextlib import contextmanager
    from app.product_composition.game_history import run_history_once
    events = []
    def claim():
        events.append("claimed")
        pytest.fail("Denied/unknown operation claimed a history task")
    @contextmanager
    def denied():
        events.append("guard")
        raise ValueError("whole_operation_control_unreadable")
        yield
    with pytest.raises(ValueError, match="whole_operation_history_admission_required"):
        run_history_once(receiver=object(), write=None, admission=None, claim=claim)
    with pytest.raises(ValueError, match="control_unreadable"):
        run_history_once(receiver=object(), write=None, admission=denied, claim=claim)
    assert events == ["guard"]


def projection_inputs(source_id="lichess", provider="lichess"):
    from app.contracts.product_v02.games import CanonicalGame
    from app.contracts.product_v02.repertoire import RepertoireSnapshot
    fixtures = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures"
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(json.loads((fixtures/"snapshot.json").read_text())["payload"]))
    payload = json.loads((fixtures/"lapse.json").read_text())["payload"]["finding"]
    payload["provider"] = provider
    fact = Finding.model_validate_json(json.dumps(payload))
    game = CanonicalGame(workspace_id=snapshot.membership.workspace_id, game_id=fact.game_id,
        game_revision=1, source_id=source_id, source_revision=fact.source_revision,
        source_digest=fact.source_digest, starting_fen=snapshot.membership.routes[0].starting_fen,
        moves=[fact.played_uci], learner_color=snapshot.learner_color, played_at="2026-10-25T12:00:00Z")
    return game, snapshot, (fact,)


@pytest.mark.parametrize("source_id,provider,precision,occurred", [
    ("lichess","lichess","second","2026-10-25T12:00:00Z"),
    ("chess.com","chesscom","second","2026-10-25T12:00:00Z"),
    ("lichess","lichess","unknown",None),
])
def test_retained_binding_uses_real_shared_projection_and_preserves_source_and_time(source_id,provider,precision,occurred):
    from app.product_composition.game_history import project_retained_encounters
    game, snapshot, findings = projection_inputs(source_id,provider)
    command, = project_retained_encounters(game=game,provider=provider,snapshot=snapshot,
        findings=findings,detected_at="2026-10-27T00:00:00Z",time_precision=precision)
    assert command.key.game_id == game.game_id and command.key.provider == provider
    assert command.source.source_digest == command.payload_digest == game.source_digest
    assert command.source.source_revision == game.source_revision
    assert command.detected_at == "2026-10-27T00:00:00Z" and command.occurred_at == occurred
    assert (command.expected_uci,command.played_uci) == ("e2e4","d2d4")


@pytest.mark.parametrize("field,value", [
    ("workspace_id","another-workspace"), ("provider","chesscom"),
    ("repertoire_ids",("another-repertoire",)),
    ("window", GameWindow(start_at="2026-10-26T00:00:00Z",end_at="2026-11-01T00:00:00Z",precision="second")),
])
def test_history_start_cannot_rebind_a_frozen_manifest_to_another_scope(field,value):
    from app.product_composition.game_history import project_retained_encounters, validate_history_plan_scope
    game, snapshot, findings = projection_inputs()
    commands = project_retained_encounters(game=game,provider="lichess",snapshot=snapshot,
        findings=findings,detected_at="2026-10-27T00:00:00Z",time_precision="second")
    request = selection().model_copy(update={"workspace_id":game.workspace_id,"repertoire_ids":("white",)})
    comparer = Comparer(HistoryComparison(1,findings,commands))
    comparer.projection_available = True
    plan = prepare(Reader([RetainedHistoryGame(game.game_id,False,1)]),comparer,request,
                   policy="published-targeted-owner-policy")
    validate_history_plan_scope(plan,selection=request)
    changed = request.model_copy(update={field:value})
    with pytest.raises(ValueError,match="selection_changed"):
        validate_history_plan_scope(plan,selection=changed)
    with pytest.raises(ValueError,match="outside_selection|outside_window"):
        validate_history_plan_scope(replace(plan,selection=changed),selection=changed)


def test_bounded_ply_preview_retains_unknowns_and_exposes_same_page_continuation():
    comparer = Comparer(HistoryComparison(4, (finding("known_unintroduced", ply=3),), (),
        False, 1, ("legacy_capture_candidate_budget",), 20))
    comparer.position_bound = lambda request, total: min(total-request.start_ply, request.maximum_plies)
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 24)], more=True),
        comparer, selection(maximum_positions=4, maximum_plies=4))
    assert plan.preview.compared_games == 1 and plan.preview.positions_compared == 4
    assert plan.preview.unresolved_positions == plan.preview.unknown_authority_or_mapping_games == 1
    assert plan.preview.remaining_positions == 20 and plan.preview.next_start_ply == 4
    assert plan.preview.more_available and plan.preview.next_game_id == "retained-game"
    assert plan.preview.matches_are_lower_bound and plan.preview.matches == 0


def test_original_version_one_manifest_round_trip_keeps_exact_digest():
    from app.games.feedback.history import PreparedHistoryPlan
    import hashlib
    plan = prepare(Reader([RetainedHistoryGame("retained-game", False, 4)]),
                   Comparer(HistoryComparison(4, ())))
    original = replace(plan, version=1).payload()
    expected = hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    restored = PreparedHistoryPlan.from_payload(original)
    assert restored.version == 1 and restored.payload() == original and restored.digest == expected


def test_exact_ply_window_is_part_of_the_frozen_selection_and_command_guard():
    from app.product_composition.game_history import project_retained_encounters, validate_history_plan_scope
    game, snapshot, findings = projection_inputs()
    commands = project_retained_encounters(game=game,provider="lichess",snapshot=snapshot,
        findings=findings,detected_at="2026-10-27T00:00:00Z",time_precision="second")
    request = selection().model_copy(update={"workspace_id":game.workspace_id,"repertoire_ids":("white",)})
    comparer = Comparer(HistoryComparison(1,findings,commands))
    comparer.projection_available = True
    plan = prepare(Reader([RetainedHistoryGame(game.game_id,False,1)]),comparer,request,policy="owner")
    changed = replace(plan, selection=request.model_copy(update={"start_ply":2}))
    with pytest.raises(ValueError,match="outside_ply_window"):
        validate_history_plan_scope(changed,selection=changed.selection)
