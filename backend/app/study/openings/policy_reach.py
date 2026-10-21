"""Read-only reach under one deterministic learner policy and common scenario root.

No policy selection/fusion, recall estimation, eligibility or admission occurs here.
Complete legal prefixes establish mutually exclusive opponent continuations; an
unresolved learner action prevents exporting downstream events to the kernel.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import InitVar, dataclass, field, replace
import json
from math import fsum
from typing import Iterable, Literal

import chess

from app.contracts.opening_move_probability import (
    FusedMoveDistribution, OpeningMoveEvidence, serialize_contract,
)
from .preparedness import ProbabilityEvidence, RouteReach, decision_reach, route_probability
from app.chess.opening_segments import decision_identity, opening_position_key

REACH_VERSION = "explicit-policy-reach-v1"


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identity must be a nonempty string")


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique(entries, key) -> tuple:
    entries_by_key = {}
    for entry in entries:
        identity = key(entry)
        if identity in entries_by_key and entries_by_key[identity] != entry:
            raise ValueError(f"Conflicting evidence: {identity}")
        entries_by_key[identity] = entry
    return tuple(entries_by_key[identity] for identity in sorted(entries_by_key))


@dataclass(frozen=True, slots=True)
class LearnerPolicyAction:
    policy_id: str
    position_key: str
    move_uci: str

    def __post_init__(self):
        _identifier(self.policy_id)
        if opening_position_key(self.position_key) != self.position_key:
            raise ValueError("Action requires canonical position key")
        board = chess.Board(self.position_key)
        if chess.Move.from_uci(self.move_uci) not in board.legal_moves:
            raise ValueError("Illegal policy action")


@dataclass(frozen=True, slots=True)
class LearnerPolicy:
    policy_id: str
    repertoire_id: str
    trained_color: Literal["white", "black"]
    actions: tuple[LearnerPolicyAction, ...]
    policy_version: int = 1

    def __post_init__(self):
        _identifier(self.policy_id)
        _identifier(self.repertoire_id)
        if self.trained_color not in ("white", "black"):
            raise ValueError("Invalid trained color")
        if type(self.policy_version) is not int or self.policy_version < 1:
            raise ValueError("Invalid policy version")
        actions = tuple(self.actions)
        if any(action.policy_id != self.policy_id for action in actions):
            raise ValueError("Foreign policy action")
        if any(chess.Board(action.position_key).turn != (self.trained_color == "white") for action in actions):
            raise ValueError("Policy action must be at a learner position")
        object.__setattr__(self, "actions", _unique(actions, lambda action: action.position_key))


@dataclass(frozen=True, slots=True)
class PolicyReachRoute:
    policy_id: str
    line_id: str
    moves: tuple[str, ...]
    publication_id: str = "explicit-snapshot"

    def __post_init__(self):
        for identifier in (self.policy_id, self.line_id, self.publication_id):
            _identifier(identifier)
        object.__setattr__(self, "moves", tuple(self.moves))


@dataclass(frozen=True, slots=True)
class PolicyReachContext:
    context_id: str
    root_fen: str
    root_probability: ProbabilityEvidence = ProbabilityEvidence.exact(1)
    target_context_json: str = "{}"

    def __post_init__(self):
        _identifier(self.context_id)
        if not chess.Board(self.root_fen).is_valid():
            raise ValueError("Invalid root position")
        if not isinstance(self.root_probability, ProbabilityEvidence):
            raise ValueError("Root probability requires #149 evidence")
        target = json.loads(self.target_context_json)
        if not isinstance(target, dict):
            raise ValueError("Target context must be a JSON object")
        object.__setattr__(self, "target_context_json", _canonical_json(target))


@dataclass(frozen=True, slots=True)
class SelectedOpponentEvidence:
    """An explicitly selected #154 snapshot; bounds constrain unknown allocations.

    evidence_id is the source ID for OpeningMoveEvidence, or an explicit caller
    identity for a fused result. JSON detaches nested Pydantic metadata immediately.
    """
    context_id: str
    evidence_id: str
    evidence: InitVar[OpeningMoveEvidence | FusedMoveDistribution]
    sensitivity_bounds: tuple[tuple[str, ProbabilityEvidence], ...] = ()
    snapshot_json: str = field(init=False)
    kind: str = field(init=False)

    def __post_init__(self, evidence):
        _identifier(self.context_id)
        _identifier(self.evidence_id)
        if not isinstance(evidence, (OpeningMoveEvidence, FusedMoveDistribution)):
            raise ValueError("Expected #154 selected opponent evidence")
        if isinstance(evidence, OpeningMoveEvidence) and evidence.evidence_id != self.evidence_id:
            raise ValueError("Evidence identity mismatch")
        object.__setattr__(self, "kind", type(evidence).__name__)
        object.__setattr__(self, "snapshot_json", serialize_contract(evidence))
        object.__setattr__(self, "sensitivity_bounds", _unique(
            tuple((move, bound) for move, bound in self.sensitivity_bounds), lambda entry: entry[0]))
        # Validate a detached snapshot, not mutable caller metadata.
        _reply_probabilities(self)

    def snapshot(self):
        model = OpeningMoveEvidence if self.kind == "OpeningMoveEvidence" else FusedMoveDistribution
        return model.model_validate_json(self.snapshot_json)


def _reply_probabilities(selection: SelectedOpponentEvidence):
    evidence = selection.snapshot()
    distribution = evidence.distribution
    bounds = dict(selection.sensitivity_bounds)
    if distribution is None:
        if bounds:
            raise ValueError("Cannot bound unavailable evidence")
        return {}, None
    moves = {move.move_uci: move.probability for move in distribution.moves}
    if not bounds.keys() <= moves.keys():
        raise ValueError("Sensitivity bound references illegal reply")
    probabilities = {}
    for move, probability in moves.items():
        if probability is not None:
            # #154 permits tiny >1 values as mass tolerance; strict #149 cannot.
            if probability > 1:
                raise ValueError("Assigned probability exceeds kernel range")
            if move in bounds:
                raise ValueError("Sensitivity bounds cannot overwrite exact evidence")
            probabilities[move] = ProbabilityEvidence.exact(probability)
        else:
            reason = f"unknown_reply:{selection.evidence_id}:{move}"
            bound = bounds.get(move, ProbabilityEvidence.unknown(reason))
            if bound.status == "exact":
                raise ValueError("Unknown allocation cannot become exact sensitivity evidence")
            upper = min(bound.upper, distribution.unknown_mass)
            if bound.lower > upper:
                raise ValueError("Sensitivity bounds exceed unknown mass")
            probabilities[move] = (ProbabilityEvidence.unknown(reason) if bound.status == "unknown" and upper == 1
                                   else ProbabilityEvidence.bounded(bound.lower, upper, reason + "; " + bound.reason))
    unknown = tuple(probabilities[move] for move, probability in moves.items() if probability is None)
    if unknown and (fsum(item.lower for item in unknown) > distribution.unknown_mass + 1e-12
                    or fsum(item.upper for item in unknown) < distribution.unknown_mass - 1e-12):
        raise ValueError("Sensitivity bounds cannot allocate total unknown mass")
    return probabilities, distribution


@dataclass(frozen=True, slots=True)
class OpponentReachDiagnostic:
    position_key: str
    evidence_id: str | None
    snapshot_json: str | None
    replies: tuple[tuple[str, ProbabilityEvidence], ...]
    covered_moves: tuple[str, ...]
    known_covered_mass: float
    known_outside_mass: float
    unassigned_mass: ProbabilityEvidence
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PathReach:
    position_key: str
    route: RouteReach
    decision_id: str | None
    prescribed_action: str | None
    opponent_factors: tuple[tuple[str, str, str | None, ProbabilityEvidence], ...]
    unresolved_policy_positions: tuple[str, ...]
    missing_current_action: bool
    representations: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class UniqueReach:
    identity: str
    probability: ProbabilityEvidence
    incoming_paths: tuple[PathReach, ...]
    unresolved: bool

    def kernel_routes(self) -> tuple[RouteReach, ...]:
        if self.unresolved:
            raise ValueError("Unscorable reach: missing learner policy action")
        return tuple(path.route for path in self.incoming_paths)


@dataclass(frozen=True, slots=True)
class PolicyReachResult:
    policy_id: str
    context_id: str
    root_key: str
    target_context_json: str
    paths: tuple[PathReach, ...]
    positions: tuple[UniqueReach, ...]
    decisions: tuple[UniqueReach, ...]
    opponent_diagnostics: tuple[OpponentReachDiagnostic, ...]
    model_version: str = REACH_VERSION


def _aggregate(identity, paths, policy_id):
    # Prefix absorption is the event union rule, not a numerical maximum.
    first_visits = []
    for path in sorted(paths, key=lambda item: (len(item.route.move_prefix), item.route.move_prefix)):
        if any(path.route.move_prefix[:len(earlier.route.move_prefix)] == earlier.route.move_prefix
               for earlier in first_visits):
            continue
        first_visits.append(path)
    first_visits.sort(key=lambda item: item.route.identity)
    unresolved = any(path.unresolved_policy_positions for path in first_visits)
    probability = (ProbabilityEvidence.unknown("unresolved_policy_union:" + identity) if unresolved
                   else decision_reach((path.route for path in first_visits), policy_id=policy_id))
    return UniqueReach(identity, probability, tuple(first_visits), unresolved)


def propagate_policy_reach(policy: LearnerPolicy, routes: Iterable[PolicyReachRoute],
                           opponent_evidence: Iterable[SelectedOpponentEvidence], *,
                           context: PolicyReachContext) -> PolicyReachResult:
    """Propagate finite complete prefixes; never traverse a persistent graph.

    Full prefixes share one root. Once every learner action is verified, their
    first differing move necessarily belongs to the opponent and events are
    exclusive. Missing learner actions leave downstream unions unscorable.
    """
    routes = tuple(routes)
    if any(route.policy_id != policy.policy_id for route in routes):
        raise ValueError("Foreign policy route")
    routes = _unique(routes, lambda route: (route.publication_id, route.line_id))
    selections = tuple(opponent_evidence)
    if any(selection.context_id != context.context_id for selection in selections):
        raise ValueError("Opponent context mismatch")
    selections_by_position = {}
    selections_by_id = {}
    reply_tables = {}
    for selection in selections:
        if selection.evidence_id in selections_by_id and selections_by_id[selection.evidence_id] != selection:
            raise ValueError("Conflicting opponent evidence identity")
        selections_by_id[selection.evidence_id] = selection
        snapshot = selection.snapshot()
        position = snapshot.distribution.position if isinstance(snapshot, OpeningMoveEvidence) else snapshot.position
        if isinstance(snapshot, FusedMoveDistribution) and _canonical_json(snapshot.target_context) != context.target_context_json:
            raise ValueError("Opponent target context mismatch")
        previous = selections_by_position.get(position.fen_key)
        if previous is not None and previous != selection:
            raise ValueError("Conflicting selected opponent evidence")
        selections_by_position[position.fen_key] = selection
        reply_tables[position.fen_key] = _reply_probabilities(selection)

    actions = {action.position_key: action.move_uci for action in policy.actions}
    learner_turn = policy.trained_color == "white"
    root_key = opening_position_key(context.root_fen)
    paths_by_prefix = {}
    representations = defaultdict(set)
    covered_replies = defaultdict(set)
    for authored_route in sorted(routes, key=lambda route: (route.moves, route.publication_id, route.line_id)):
        board = chess.Board(context.root_fen)
        factors = []
        unresolved = set()
        for offset in range(len(authored_route.moves) + 1):
            prefix = authored_route.moves[:offset]
            position_key = opening_position_key(board.fen())
            next_move = authored_route.moves[offset] if offset < len(authored_route.moves) else None
            requires_learner_action = board.turn == learner_turn and bool(board.legal_moves)
            prescribed = actions.get(position_key) if requires_learner_action else None
            decision_id = None
            if board.turn == learner_turn and prescribed is not None:
                if next_move is not None and next_move != prescribed:
                    raise ValueError("Route contradicts selected learner action")
                decision_id = decision_identity(policy.repertoire_id, board.fen(), policy.trained_color,
                                                prescribed, policy.policy_version)
            probability = (ProbabilityEvidence.unknown("missing_policy_action:" + ",".join(sorted(unresolved)))
                           if unresolved else route_probability(context.root_probability, (item[3] for item in factors)))
            path = PathReach(position_key, RouteReach(context.context_id, root_key, prefix, probability,
                             policy_id=policy.policy_id), decision_id, prescribed, tuple(factors), tuple(sorted(unresolved)),
                             requires_learner_action and prescribed is None, ())
            if prefix in paths_by_prefix and paths_by_prefix[prefix] != path:
                raise ValueError("Conflicting prefix evidence")
            paths_by_prefix[prefix] = path
            representations[prefix].add((authored_route.publication_id, authored_route.line_id))
            if next_move is None:
                break
            move = chess.Move.from_uci(next_move)
            if move not in board.legal_moves:
                raise ValueError("Illegal route move")
            if board.turn == learner_turn and prescribed is None:
                unresolved.add(position_key)
            if board.turn != learner_turn:
                covered_replies[position_key].add(next_move)
                selection = selections_by_position.get(position_key)
                probabilities, distribution = reply_tables.get(position_key, ({}, None))
                factor = probabilities.get(next_move, ProbabilityEvidence.unknown("missing_opponent_evidence:" + position_key))
                factors.append((position_key, next_move, selection.evidence_id if selection else None, factor))
            board.push(move)

    paths = tuple(replace(paths_by_prefix[prefix], representations=tuple(sorted(representations[prefix])))
                  for prefix in sorted(paths_by_prefix))
    position_paths = defaultdict(list)
    decision_paths = defaultdict(list)
    for path in paths:
        position_paths[path.position_key].append(path)
        if path.decision_id is not None:
            decision_paths[path.decision_id].append(path)
    diagnostics = []
    for position_key in sorted(covered_replies):
        selection = selections_by_position.get(position_key)
        probabilities, distribution = reply_tables.get(position_key, ({}, None))
        covered = covered_replies[position_key]
        known_covered = known_outside = 0.0
        if distribution is not None:
            known_covered = fsum(move.probability for move in distribution.moves
                                 if move.probability is not None and move.move_uci in covered)
            known_outside = fsum(move.probability for move in distribution.moves
                                 if move.probability is not None and move.move_uci not in covered)
        snapshot = selection.snapshot() if selection else None
        reason = (() if distribution is not None else (
            "missing_opponent_evidence" if selection is None else "unavailable_opponent_evidence:" + snapshot.unavailable_reason,))
        diagnostics.append(OpponentReachDiagnostic(position_key, selection.evidence_id if selection else None,
            selection.snapshot_json if selection else None, tuple(sorted(probabilities.items())), tuple(sorted(covered)),
            known_covered, known_outside, ProbabilityEvidence.exact(distribution.unknown_mass) if distribution is not None
            else ProbabilityEvidence.unknown(reason[0]), reason))
    return PolicyReachResult(policy.policy_id, context.context_id, root_key, context.target_context_json, paths,
        tuple(_aggregate(identity, position_paths[identity], policy.policy_id) for identity in sorted(position_paths)),
        tuple(_aggregate(identity, decision_paths[identity], policy.policy_id) for identity in sorted(decision_paths)),
        tuple(diagnostics))
