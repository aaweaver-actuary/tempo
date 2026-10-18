"""Policy isolation and event-union regressions for #169 / #156."""
from dataclasses import replace
import json
from math import fsum
from pathlib import Path

import chess
import pytest

from tempo_evidence_math import OpeningMoveEvidence, FusedMoveDistribution, move_universe
from tempo_evidence_math import (
    LearnerPolicy, LearnerPolicyAction, PolicyReachContext, PolicyReachRoute,
    SelectedOpponentEvidence, propagate_policy_reach,
)
from tempo_evidence_math import (
    ProbabilityEvidence as P, DecisionReadiness, CardLearningEffect,
    preparedness, marginal_card_value, rank_card_values,
)
from tempo_evidence_math import opening_position_key

EXAMPLES = json.loads((Path(__file__).parent / "fixtures/probability-examples.json").read_text())
ITALIAN = ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "a7a6", "d2d3")
RUY = (*ITALIAN[:4], "f1b5", "a7a6", "b5a4")
CONTEXT = PolicyReachContext("opponent", chess.STARTING_FEN)


def policy_for(policy_id, routes, trained_color="white", root=chess.STARTING_FEN):
    actions = []
    for moves in routes:
        board = chess.Board(root)
        for move in moves:
            if board.turn == (trained_color == "white"):
                actions.append(LearnerPolicyAction(policy_id, opening_position_key(board.fen()), move))
            board.push_uci(move)
    return LearnerPolicy(policy_id, policy_id, trained_color, tuple(actions))


def selected_evidence(board, assigned, unknown_mass=0, *, evidence_id=None, bounds=()):
    position = move_universe(board.fen())
    evidence_id = evidence_id or opening_position_key(board.fen())
    payload = json.loads(json.dumps(EXAMPLES["maia_dense"]))
    payload["evidence_id"] = evidence_id
    payload["quality"]["uncertainty"] = {"note": "opaque metadata"}
    payload["distribution"] = {"contract_version": 1, "position": position.model_dump(),
        "moves": [{"move_uci": move, "probability": assigned.get(move, None if unknown_mass else 0)}
                  for move in position.legal_moves], "unknown_mass": unknown_mass}
    payload["raw_evidence"] = {"kind": "scores", "basis": "probability", "moves": [
        {"move_uci": move, "value": probability} for move, probability in assigned.items()]}
    return SelectedOpponentEvidence("opponent", evidence_id, OpeningMoveEvidence.model_validate(payload), bounds)


def evidence_for(routes, trained_color="white", root=chess.STARTING_FEN):
    positions = {}
    for moves in routes:
        board = chess.Board(root)
        for move in moves:
            if board.turn != (trained_color == "white"):
                key = opening_position_key(board.fen())
                positions.setdefault(key, (board.copy(), {}))[1][move] = {
                    "e7e5": .42, "b8c6": .50, "a7a6": .25,
                }.get(move, .10)
            board.push_uci(move)
    result = []
    for board, assigned in positions.values():
        # Explicitly assign remaining mass outside represented paths.
        outside = next(move.uci() for move in board.legal_moves if move.uci() not in assigned)
        assigned[outside] = 1 - fsum(assigned.values())
        result.append(selected_evidence(board, assigned))
    return tuple(result)


def evaluate(policy_id, moves, *, all_routes=None):
    # Selection is explicit at the caller boundary; passing foreign routes is an error.
    all_routes = all_routes or {policy_id: (moves,)}
    selected = all_routes[policy_id]
    policy = policy_for(policy_id, selected)
    routes = tuple(PolicyReachRoute(policy_id, str(index), route) for index, route in enumerate(selected))
    return propagate_policy_reach(policy, routes, evidence_for(selected), context=CONTEXT)


def target(result, prefix=ITALIAN[:-1]):
    return next(decision for decision in result.decisions
                if any(path.route.move_prefix == prefix for path in decision.incoming_paths))


def scores(result, decision):
    readiness = (DecisionReadiness(decision.identity, decision.kernel_routes(), P.exact(.2), policy_id=result.policy_id),)
    effect = CardLearningEffect("card", ((decision.identity, P.exact(.8)),), policy_id=result.policy_id)
    return (preparedness(readiness, policy_id=result.policy_id),
            marginal_card_value(readiness, effect, policy_id=result.policy_id),
            rank_card_values(readiness, (effect,), policy_id=result.policy_id))



from datetime import datetime, timezone, date
from dataclasses import replace
from tempo_evidence_math import (MemoryProjection, PresentationIdentity, ReadinessSnapshot,
    StudyReviewEvidence, DecisionReadinessBinding, OpeningCardValuationSnapshot,
    ValuationCandidate, OpeningRankingCandidate, FinitePolicyGraphSnapshot, PublicationRevision,
    decision_identity, CardLearningEffect, canonical_json)
AS_OF = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)

def memory(presentation, as_of=AS_OF, *, value=None, reference=None, status=None, reason=None):
    available = value is not None
    return MemoryProjection(presentation, as_of, value, reference or (as_of if available else None),
        status or ("available" if available else "missing"), reason or (None if available else "missing_scheduler_state"),
        "1"*64 if available else None, "fixture-projection", "1", "2"*64)
def fixture(policy_id="italian", moves=ITALIAN, policy_version=1, repertoire_id=None):
    repertoire_id = repertoire_id or policy_id
    policy = replace(policy_for(policy_id, (moves,)), policy_version=policy_version, repertoire_id=repertoire_id)
    routes = (PolicyReachRoute(policy_id, "line", moves, "generation-7"),)
    evidence = evidence_for((moves,))
    reach = propagate_policy_reach(policy, routes, evidence, context=CONTEXT)
    bindings = []
    board = chess.Board()
    for move in moves:
        if board.turn == chess.WHITE:
            presentation = PresentationIdentity("memory-" + str(len(bindings)), 1, repertoire_id, "white")
            snapshot = ReadinessSnapshot(presentation, board.fen(), (move,), memory(presentation),
                reviews=(StudyReviewEvidence(len(bindings), presentation.card_id, AS_OF, "again", card_revision=1),))
            identity = decision_identity(repertoire_id, board.fen(), "white", move, policy_version)
            bindings.append(DecisionReadinessBinding(policy_id, policy_version, identity, snapshot, 0))
        board.push_uci(move)
    early = next(decision.identity for decision in reach.decisions
                 if any(path.route.move_prefix == moves[:4] for path in decision.incoming_paths))
    deep = next(decision.identity for decision in reach.decisions
                if any(path.route.move_prefix == moves[:6] for path in decision.incoming_paths))
    candidates = (
        ValuationCandidate(OpeningRankingCandidate("multi", 1, canonical_json({"state": "new",
            "introduced_at": None, "archived": 0, "pending_validation": 0}), policy_id=policy_id),
            policy_version, 1, (early, deep), (("generation-7", "line"),),
            CardLearningEffect("multi", ((early, P.exact(.6)), (deep, P.exact(.8))), policy_id=policy_id),
            "explicit-hand-fixture-v1"),
        ValuationCandidate(OpeningRankingCandidate("single", 2, canonical_json({"state": "new",
            "introduced_at": None, "archived": 0, "pending_validation": 0}), policy_id=policy_id),
            policy_version, 1, (early,), (("generation-7", "line"),),
            CardLearningEffect("single", ((early, P.exact(.9)),), policy_id=policy_id),
            "explicit-hand-fixture-v1"),
    )
    return OpeningCardValuationSnapshot(policy, CONTEXT, routes, evidence, tuple(bindings),
                                                candidates, AS_OF, date(2026, 10, 11),
        FinitePolicyGraphSnapshot(policy, CONTEXT, (PublicationRevision("generation-7", "7", "3"*64),), routes, len(moves)))
