"""Synthetic fake-capability proof for #499; no content or learning effects."""
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple

import chess
import pytest

ROOT = Path(__file__).resolve().parents[2]
for source in ("packages/chess-core/src", "packages/evidence-math/src",
               "packages/game-review-contracts/src", "contracts/game-evidence/src",
               "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / source))

from tempo_evidence_math import move_universe, opening_position_key
from app.contracts.product_v02.analysis import (
    CandidateEvaluation, DisplayedCardChange, EngineContext, EngineEvidence,
    PopulationSelection, ProbabilityEvidence, ProposalDecision,
)
from app.contracts.product_v02.repertoire import DecisionPresentation
from app.analysis.proposals.acceptability import QualityPolicy, assess
from app.analysis.proposals.blunders import BlunderPolicy, BlunderRequest, prepare_blunder
from app.analysis.proposals.coordinator import ProposalCoordinator, approval_requires_revalidation
from app.analysis.proposals.ports import CapabilityUnavailable, ProposalLimits, ProposalTarget


def engine_fixture():
    return json.loads((ROOT / "contracts/product-v02/fixtures/engine-explicit-candidate.json").read_text())["payload"]


@pytest.mark.parametrize("color", ["white", "black"])
@pytest.mark.parametrize("loss,expected", [(30, "acceptable"), (31, "rejected")])
def test_30_cp_accepted_31_rejected_both_colors(color, loss, expected):
    payload = engine_fixture()
    if color == "black":
        board = chess.Board()
        board.push_uci("e2e4")
        payload["context"].update(learner_color="black", history_uci=["e2e4"], target_fen=board.fen())
        payload["best"]["move_uci"] = "e7e5"
        payload["requested_candidates"] = ["a7a6"]
        payload["candidates"] = [payload["candidates"][-1] | {"move_uci": "a7a6"}]
    payload["candidates"][-1]["score"]["value"] = 50 - loss
    evidence = EngineEvidence.model_validate_json(json.dumps(payload))
    result = assess(evidence.context, evidence, evidence.candidates[-1].move_uci)
    assert result.status == expected and result.loss_cp == loss


@pytest.mark.parametrize("change", ["mate", "context", "depth", "bounded"])
def test_inconclusive_mate_or_mismatched_settings_not_cp_acceptance(change):
    payload = engine_fixture()
    if change == "mate": payload["candidates"][-1]["score"] = {"kind": "mate", "value": 3}
    if change == "depth": payload["candidates"][-1]["depth"] = 11
    if change == "bounded": payload["candidates"][-1].update(score=None, unavailable_reason="lowerbound")
    evidence = EngineEvidence.model_validate_json(json.dumps(payload))
    context = evidence.context if change != "context" else evidence.context.model_copy(update={"hash_mb": 32})
    assert assess(context, evidence, "a2a3").status == "inconclusive"


def test_threshold_override_is_explicit():
    with pytest.raises(ValueError): QualityPolicy(max_loss_cp=31)
    assert QualityPolicy(max_loss_cp=31, adjustment_reason="explicit displayed research scenario").max_loss_cp == 31


class Comparison(NamedTuple):
    score: int = 900
    similarities: tuple = ("same learner pawns", "same colored material")
    differences: tuple = ("opponent pawn on different square",)


class Source(NamedTuple):
    repertoire_id: str
    route_id: str
    ply: int
    source_revision: str
    full_fen: str
    starting_fen: str
    history_uci: tuple


class Support(NamedTuple):
    position_key: str
    selected_response_uci: str
    comparison: Comparison
    occurrences: tuple


class Familiar(NamedTuple):
    move_uci: str
    exact_continuation: bool
    supporting_positions: tuple


class RetrievalRequest(NamedTuple):
    repertoire_id: str
    target_fen: str
    learner_color: str
    variant: str


class Retrieval(NamedTuple):
    request: RetrievalRequest
    revision: int
    digest: str
    status: str
    target_position_key: str
    exact_reason: str
    source_complete: bool
    candidates: tuple
    truncation: tuple = ()


def context(history=(), color="white"):
    board = chess.Board()
    for move in history: board.push_uci(move)
    return EngineContext(starting_fen=chess.STARTING_FEN, history_uci=history, target_fen=board.fen(),
        variant="standard", learner_color=color, engine_version="fake-stockfish-v1", max_depth=12,
        max_nodes=1000, max_time_ms=100, threads=1, hash_mb=16)


def target(**changes):
    return replace(ProposalTarget("synthetic-proposal", "white", 1, "a" * 64,
                                  "synthetic-route", context()), **changes)


def support(move, number=0, *, exact_fen=None, copies=1):
    board = chess.Board(exact_fen or chess.STARTING_FEN)
    if exact_fen is None:
        pawn = chess.A7 if number % 2 == 0 else chess.H7
        piece = board.remove_piece_at(pawn)
        board.set_piece_at(pawn - 8 * (number // 2 + 1), piece)
    fen = board.fen()
    source = Source("white", f"source-route-{number}", 0, "synthetic-source", fen, fen, ())
    return Support(opening_position_key(fen), move, Comparison(), (source,) * copies)


def familiar(move, count=1, *, exact_fen=None, copies=1):
    return Familiar(move, exact_fen is not None, tuple(support(move, n, exact_fen=exact_fen, copies=copies)
                    for n in range(count)))


def retrieval(goal, candidates, **changes):
    return Retrieval(RetrievalRequest(goal.repertoire_id, goal.context.target_fen,
        goal.context.learner_color, goal.context.variant), goal.source_revision, goal.source_digest,
        "ready", opening_position_key(goal.context.target_fen), "exact_continuation", True,
        tuple(candidates))._replace(**changes)


class FakeSimilarity:
    def __init__(self, value): self.value, self.calls = value, []
    def retrieve(self, **request):
        self.calls.append(request)
        return self.value


class FakeEngine:
    def __init__(self, scores=None, *, best="e2e4", best_cp=50):
        self.scores, self.best, self.best_cp = dict(scores or {}), best, best_cp
        self.calls = []

    def evaluate(self, context, candidates):
        self.calls.append((context, candidates))
        def evaluation(move, cp):
            return CandidateEvaluation(move_uci=move, score={"kind": "centipawns", "value": cp},
                depth=12, nodes=1000, unavailable_reason=None)
        return EngineEvidence(evidence_id="fake-engine-evidence", context=context, requested_candidates=candidates,
            best=evaluation(self.best, self.best_cp), candidates=tuple(
                evaluation(move, self.scores.get(move, self.best_cp)) for move in candidates), unavailable_reason=None)


def coordinator(goal, candidates, engine=None, **options):
    similar = FakeSimilarity(retrieval(goal, candidates))
    return ProposalCoordinator(similar, engine or FakeEngine(), **options)


def test_familiar_legal_candidate_outside_top_five_considered():
    goal = target()
    moves = ("e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "g2g3", "a2a3")
    engine = FakeEngine({"a2a3": 20})
    policy = coordinator(goal, [familiar(move, count=2 if move == "a2a3" else 1) for move in moves], engine)
    result = policy.propose(goal)
    assert engine.calls[0][1] == moves  # All seven, not a top-five intersection.
    assert result.status == "proposal" and result.selected_move_uci == "a2a3"
    assert result.proposal.max_loss_cp == 30
    assert "differing features" in result.proposal.explanation and "source-route" in result.proposal.explanation


def test_exact_continuation_preferred():
    goal = target()
    policy = coordinator(goal, [familiar("d2d4", 3), familiar("e2e4", exact_fen=goal.context.target_fen)],
                         FakeEngine({"e2e4": 20}, best="d2d4"))
    result = policy.propose(goal)
    assert result.selected_move_uci == "e2e4" and result.status == "proposal"


def test_illegal_or_unsound_familiar_move_not_recommended():
    goal = target()
    engine = FakeEngine({"a2a3": 19, "d2d4": 45})
    result = coordinator(goal, [familiar("a2a5", 3), familiar("a2a3", 2), familiar("d2d4")], engine).propose(goal)
    assert "a2a5" not in engine.calls[0][1]
    assert result.selected_move_uci == "d2d4"
    assert next(item for item in result.quality if item.move_uci == "a2a3").status == "rejected"
    none = coordinator(goal, [familiar("a2a3")], FakeEngine({"a2a3": 19})).propose(goal)
    assert none.status == "no_sound_candidate" and none.proposal is None


def test_outlier_support_counts_distinct_positions():
    goal = target(kind="covered_outlier", previous_response_uci="e2e4")
    current = familiar("e2e4", exact_fen=goal.context.target_fen)
    repeated = support("d2d4", copies=9)
    one_position = Familiar("d2d4", False, (repeated, repeated, repeated))
    none = coordinator(goal, [current, one_position]).propose(goal)
    assert none.status == "no_sound_candidate" and goal.previous_response_uci == "e2e4"
    result = coordinator(goal, [current, familiar("d2d4", 2)]).propose(goal)
    assert result.status == "proposal" and result.proposal.edits[0].previous_response_uci == "e2e4"
    assert len(result.proposal.supporting_position_keys) == 2
    assert goal.previous_response_uci == "e2e4"  # Original remains selected until owner approval.


def test_missing_reply_proposes_displayed_addition():
    goal = target()
    result = coordinator(goal, [familiar("a2a3")], FakeEngine({"a2a3": 20})).propose(goal)
    proposal = result.proposal
    assert proposal.edits[0].previous_response_uci is None
    assert proposal.edits[0].displayed_pgn == "1. a3 *"
    assert proposal.card_changes[0].action == "add" and proposal.card_changes[0].moves == ("a2a3",)
    assert proposal.card_changes[0].presentation_id is None
    assert proposal.display_digest == proposal.content_digest()
    assert proposal.engine_evidence[0].context == goal.context


def mapping(goal, move, introduction="unintroduced"):
    return DecisionPresentation(decision_id="real-decision", repertoire_id=goal.repertoire_id,
        position_key=opening_position_key(goal.context.target_fen), selected_response_uci=move,
        presentation_id="actual-existing-card", presentation_revision=7,
        repertoire_revision=goal.source_revision, introduction=introduction,
        introduced_at="2026-10-25T12:00:00Z" if introduction == "introduced" else None,
        available=True, introduction_matches_current_content=introduction == "introduced")


class FakeProbability:
    def __init__(self, move="d7d5", chance=0.2): self.move, self.chance, self.calls = move, chance, []
    def evidence(self, position, population):
        self.calls.append((position, population))
        payload = dict(evidence_id="synthetic-frequency", position=position.model_dump(mode="json"),
            provider="fake-explorer", model=None, basis="population", population=population.model_dump(mode="json"),
            personal_account=None, observed_at="2026-10-26T12:00:00Z", unavailable_reason=None,
            counts={"kind": "counts", "total_count": 100, "moves": [{"move_uci": self.move, "count": 20}]},
            distribution={"position": position.model_dump(mode="json"), "unknown_mass": 1 - self.chance,
                "moves": [{"move_uci": move, "probability": self.chance if move == self.move else None}
                          for move in position.legal_moves]})
        return ProbabilityEvidence.model_validate_json(json.dumps(payload))


def blunder_case(*, introduced="unintroduced", opponent_loss=150, own_loss=0, policy=BlunderPolicy()):
    root = context(("e2e4",))
    goal = target(context=context(("e2e4", "d7d5")), previous_response_uci="e4d5")
    goal = replace(goal, existing_card=mapping(goal, "e4d5", introduced))
    own = coordinator(goal, [familiar("e4d5", exact_fen=goal.context.target_fen)],
                      FakeEngine({"e4d5": 200 - own_loss}, best="g1f3", best_cp=200))
    opponent = FakeEngine({"d7d5": opponent_loss}, best="e7e5", best_cp=0)
    population = PopulationSelection(rating_min=1600, rating_max=2000, time_controls=("rapid",))
    request = BlunderRequest(goal, root, "d7d5", population, policy)
    return request, own, opponent


@pytest.mark.parametrize("introduced,intent", [("unintroduced", "introduction_candidate"),
                                                ("introduced", "practice_candidate")])
def test_common_blunder_already_covered_targets_existing_card(introduced, intent):
    request, own, opponent = blunder_case(introduced=introduced)
    result = prepare_blunder(request, probability=FakeProbability(), opponent_engine=opponent, coordinator=own)
    candidate = result.result.existing_card
    assert result.result.status == "existing_card" and result.result.proposal is None
    assert candidate.presentation.presentation_id == "actual-existing-card" and candidate.intent == intent
    assert len(candidate.engine_evidence) == 2
    assert result.frequency.distribution.unknown_mass == 0.8
    assert "opponent-error-frequency-cp-v1" in candidate.explanation
    assert "no recorded game lapse, Again or introduction" in candidate.explanation


def test_blunder_threshold_separate_from_own_tolerance():
    request, own, opponent = blunder_case(opponent_loss=70)
    low = prepare_blunder(request, probability=FakeProbability(), opponent_engine=opponent, coordinator=own)
    assert low.result.reason == "opponent_loss_below_separate_blunder_threshold"
    assert not own.engine.calls  # 70 exceeds 30, but not the explicit 100 CP blunder criterion.
    request = replace(request, policy=BlunderPolicy(min_opponent_loss_cp=50))
    accepted = prepare_blunder(request, probability=FakeProbability(), opponent_engine=opponent, coordinator=own)
    assert accepted.result.status == "existing_card"
    request, own, opponent = blunder_case(opponent_loss=200, own_loss=31)
    rejected = prepare_blunder(request, probability=FakeProbability(), opponent_engine=opponent, coordinator=own)
    assert rejected.result.status == "no_sound_candidate" and rejected.result.quality[0].loss_cp == 31


def test_edited_or_stale_proposal_requires_revalidation():
    goal = target()
    proposal = coordinator(goal, [familiar("a2a3")]).propose(goal).proposal
    decision = ProposalDecision(proposal_id=proposal.proposal_id, expected_proposal_revision=1,
        expected_source_revision=1, display_digest=proposal.display_digest, action="accept", edited_proposal=None)
    check = dict(current_source_revision=1, current_source_digest=goal.source_digest)
    assert approval_requires_revalidation(proposal, decision, **check) is None
    assert approval_requires_revalidation(proposal, decision, **(check | {"current_source_revision": 2}))
    changed = proposal.model_copy(update={"card_changes": ()})
    assert approval_requires_revalidation(changed, decision, **check) == "changed_display_requires_revalidation"
    edited = decision.model_copy(update={"action": "edit", "edited_proposal": changed})
    assert approval_requires_revalidation(proposal, edited, **check) == "edited_proposal_requires_revalidation"


def test_existing_card_replacement_needs_exact_displayed_executable():
    goal = target(kind="covered_outlier", previous_response_uci="e2e4")
    goal = replace(goal, existing_card=mapping(goal, "e2e4"))
    choices = [familiar("e2e4", exact_fen=goal.context.target_fen), familiar("d2d4", 2)]
    assert coordinator(goal, choices).propose(goal).reason == "exact_displayed_existing_card_edit_required"
    card = DisplayedCardChange(action="update", presentation_id="actual-existing-card", expected_revision=7,
        starting_fen=goal.context.target_fen, moves=("d2d4",), learner_color="white")
    goal = replace(goal, displayed_card_changes=(card,))
    result = coordinator(goal, choices).propose(goal)
    assert result.status == "proposal" and result.proposal.card_changes == (card,)


def test_stale_existing_mapping_cannot_be_reused_for_replacement_or_practice():
    goal = target(previous_response_uci="e2e4")
    stale = mapping(goal, "e2e4").model_copy(update={"repertoire_revision": 2})
    goal = replace(goal, existing_card=stale)
    result = coordinator(goal, [familiar("e2e4", exact_fen=goal.context.target_fen)]).propose(goal)
    assert result.status == "inconclusive" and result.existing_card is None
    assert result.proposal is None


@pytest.mark.parametrize("kind", ["common_blunder", "covered_outlier"])
@pytest.mark.parametrize("route_id,route_ply,matched", [
    ("synthetic-route", 0, True), ("other-route", 0, False), ("synthetic-route", 2, False),
])
def test_existing_card_route_local_mapping_matches_actual_target(kind, route_id, route_ply, matched):
    goal = target(kind=kind, previous_response_uci="e2e4")
    payload = mapping(goal, "e2e4").model_dump(mode="json") | {"route_id": route_id, "route_ply": route_ply}
    card = DecisionPresentation.model_validate_json(json.dumps(payload))
    displayed = DisplayedCardChange(action="update", presentation_id=card.presentation_id,
        expected_revision=card.presentation_revision, starting_fen=goal.context.target_fen,
        moves=("d2d4",), learner_color="white")
    goal = replace(goal, existing_card=card, displayed_card_changes=(displayed,))
    choices = [familiar("e2e4", exact_fen=goal.context.target_fen), familiar("d2d4", 2)]
    result = coordinator(goal, choices).propose(goal)
    if matched:
        assert result.status == ("existing_card" if kind == "common_blunder" else "proposal")
    else:
        assert result.status == "inconclusive" and result.proposal is None and result.existing_card is None
        assert result.reason == "existing_card_identity_or_introduction_unproved"


def test_no_content_or_learning_side_effects():
    goal = target()
    before = repr(goal)
    policy = coordinator(goal, [familiar("a2a3")])
    one, two = policy.propose(goal), policy.propose(goal)
    assert one == two and repr(goal) == before
    assert len(policy.similarity.calls) == 2 and len(policy.engine.calls) == 2
    # Import the complete policy with all concrete owners/infrastructure denied.
    code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(("app.repertoire", "app.study", "app.games", "app.services", "app.main",
                        "app.analysis.similarity", "app.analysis.coverage", "sqlalchemy", "psycopg", "httpx")):
        raise AssertionError("forbidden implementation import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import app.analysis.proposals.coordinator
import app.analysis.proposals.blunders
'''
    subprocess.run([sys.executable, "-c", code], check=True)


def test_changed_boundary_guard():
    from test_product_contracts import boundary_errors, has_import_cycle
    files = tuple((ROOT / "backend/app/analysis/proposals").glob("*.py"))
    modules = {"app.analysis.proposals." + path.stem: path.read_text() for path in files}
    assert not boundary_errors([("analysis", source, module) for module, source in modules.items()])
    assert not has_import_cycle(modules)
    assert all("app.analysis.similarity" not in source and "app.analysis.coverage" not in source
               for source in modules.values())


def test_stale_retrieval_and_candidate_budget_fail_without_engine():
    goal = target()
    policy = coordinator(goal, [familiar("a2a3")])
    policy.similarity.value = policy.similarity.value._replace(revision=2)
    assert policy.propose(goal).reason == "stale_or_mismatched_retrieval" and not policy.engine.calls
    policy = coordinator(goal, [familiar("a2a3"), familiar("d2d4")], limits=ProposalLimits(max_candidates=1))
    assert policy.propose(goal).reason == "candidate_budget_exceeded" and not policy.engine.calls


def test_covered_scan_deduplicates_targets_and_reports_bound():
    goal = target(previous_response_uci="e2e4")
    policy = coordinator(goal, [familiar("e2e4", exact_fen=goal.context.target_fen), familiar("d2d4", 2)],
                         limits=ProposalLimits(max_targets=1))
    result = policy.scan_covered((goal, goal, replace(goal, context=context(("e2e4", "e7e5")))))
    assert result.examined_targets == 1 and result.truncated and len(policy.engine.calls) == 1


def test_unknown_or_rare_frequency_is_not_common_blunder_proof():
    request, own, opponent = blunder_case()
    probability = FakeProbability(chance=0.01)
    result = prepare_blunder(request, probability=probability, opponent_engine=opponent, coordinator=own)
    assert result.result.status == "no_sound_candidate" and not opponent.calls and not own.engine.calls


def test_common_blunder_black_learner_uses_learner_gain():
    root = context(("e2e4", "d7d5"), "black")
    goal = target(repertoire_id="black", context=context(("e2e4", "d7d5", "e4d5"), "black"),
                  previous_response_uci="d8d5")
    goal = replace(goal, existing_card=mapping(goal, "d8d5"))
    own = coordinator(goal, [familiar("d8d5", exact_fen=goal.context.target_fen)],
                      FakeEngine(best="d8d5", best_cp=200))
    request = BlunderRequest(goal, root, "e4d5",
        PopulationSelection(rating_min=1600, rating_max=2000, time_controls=("rapid",)))
    result = prepare_blunder(request, probability=FakeProbability(move="e4d5"),
        opponent_engine=FakeEngine({"e4d5": 150}, best="g1f3", best_cp=0), coordinator=own)
    assert result.opponent_loss_cp == 150 and result.result.status == "existing_card"


def test_common_blunder_missing_branch_binds_both_engine_contexts_and_frequency():
    request, own, opponent = blunder_case()
    request = replace(request, response=replace(request.response, previous_response_uci=None, existing_card=None))
    result = prepare_blunder(request, probability=FakeProbability(), opponent_engine=opponent, coordinator=own)
    proposal = result.result.proposal
    assert result.result.status == "proposal" and len(proposal.engine_evidence) == 2
    assert proposal.engine_evidence[0].context == request.opponent_context
    assert proposal.engine_evidence[1].context == request.response.context
    assert "unknown mass 0.8" in proposal.explanation and "population" in proposal.explanation
    assert proposal.display_digest == proposal.content_digest()


@pytest.mark.parametrize("failed", ["similarity", "engine", "probability", "opponent"])
def test_optional_capability_unavailable_is_an_explicit_finding(failed):
    class Unavailable:
        def retrieve(self, **kwargs): raise CapabilityUnavailable("offline")
        def evaluate(self, *args): raise CapabilityUnavailable("offline")
        def evidence(self, *args): raise CapabilityUnavailable("offline")
    if failed in {"similarity", "engine"}:
        goal = target()
        own = coordinator(goal, [familiar("a2a3")])
        if failed == "similarity": own.similarity = Unavailable()
        else: own.engine = Unavailable()
        result = own.propose(goal)
    else:
        request, own, opponent = blunder_case()
        result = prepare_blunder(request,
            probability=Unavailable() if failed == "probability" else FakeProbability(),
            opponent_engine=Unavailable() if failed == "opponent" else opponent,
            coordinator=own).result
    assert result.status == "inconclusive" and result.proposal is None and result.existing_card is None


def test_unknown_frequency_and_partial_retrieval_preserve_uncertainty():
    class UnknownProbability(FakeProbability):
        def evidence(self, position, population):
            known = super().evidence(position, population)
            return known.model_copy(update={"distribution": None, "counts": None,
                                            "unavailable_reason": "no_population_sample"})
    request, own, opponent = blunder_case()
    result = prepare_blunder(request, probability=UnknownProbability(), opponent_engine=opponent, coordinator=own)
    assert result.result.reason == "frequency_unavailable_for_opponent_move" and not opponent.calls
    goal = target()
    own = coordinator(goal, [])
    own.similarity.value = own.similarity.value._replace(truncation=("nodes",))
    assert own.propose(goal).status == "inconclusive" and not own.engine.calls


def test_inconsistent_actual_history_is_rejected_before_ports():
    goal = target(context=context().model_copy(update={"history_uci": ("e2e4",)}))
    policy = coordinator(goal, [familiar("a2a3")])
    with pytest.raises(ValueError, match="history_does_not_reach_target"): policy.propose(goal)
    assert not policy.similarity.calls and not policy.engine.calls
