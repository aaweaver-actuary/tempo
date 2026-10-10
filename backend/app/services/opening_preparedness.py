"""Pure expected-correct-decision math; deliberately disconnected from scheduling.

Reach is conditioned on one explicitly selected learner policy. Learner moves
are policy actions; opponent moves retain their modeled probabilities. Recall
is independent input evidence, not an FSRS state or another factor in reach. See
docs/opening-preparedness-contract.md for the event-partition assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import fsum, isfinite, prod
from typing import Iterable, Literal

MODEL_VERSION = "expected-correct-decisions-v1"
_MASS_TOLERANCE = 1e-12


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identifiers and evidence reasons must be nonempty strings")


@dataclass(frozen=True, slots=True)
class ProbabilityEvidence:
    """An exact supplied number is not a claim of statistical calibration."""

    lower: float
    upper: float
    status: Literal["exact", "bounded", "unknown"] = "exact"
    reason: str = ""

    def __post_init__(self) -> None:
        for bound in (self.lower, self.upper):
            if isinstance(bound, bool) or not isinstance(bound, (int, float)) or not isfinite(bound):
                raise ValueError("Probability bounds must be finite numbers")
        if not 0 <= self.lower <= self.upper <= 1:
            raise ValueError("Probability bounds must satisfy 0 <= lower <= upper <= 1")
        if self.status not in {"exact", "bounded", "unknown"}:
            raise ValueError("Unknown evidence status")
        if self.status == "exact" and self.lower != self.upper:
            raise ValueError("Exact evidence needs equal bounds")
        if self.status == "unknown" and (self.lower, self.upper) != (0, 1):
            raise ValueError("Unknown evidence spans [0, 1]")
        if self.status != "exact":
            _identifier(self.reason)
        elif self.reason:
            raise ValueError("Exact evidence has no missing-evidence reason")

    @classmethod
    def exact(cls, value: float) -> ProbabilityEvidence:
        return cls(value, value)

    @classmethod
    def bounded(cls, lower: float, upper: float, reason: str) -> ProbabilityEvidence:
        return cls(lower, upper, "bounded", reason)

    @classmethod
    def unknown(cls, reason: str) -> ProbabilityEvidence:
        return cls(0, 1, "unknown", reason)


def _unique(entries: Iterable, key) -> tuple:
    by_identity = {}
    for entry in entries:
        identity = key(entry)
        if identity in by_identity and by_identity[identity] != entry:
            raise ValueError(f"Conflicting duplicate evidence: {identity}")
        by_identity[identity] = entry
    return tuple(by_identity[identity] for identity in sorted(by_identity))


def _for_policy(entries: Iterable, policy_id: str) -> tuple:
    """Validate all supplied identities before deduplication or calculation."""
    _identifier(policy_id)
    policy_entries = tuple(entries)
    for entry in policy_entries:
        if entry.policy_id != policy_id:
            raise ValueError(f"Policy mismatch: selected {policy_id!r}, received {entry.policy_id!r}")
    return policy_entries


def _derived_probability(lower: float, upper: float,
                         inputs: tuple[ProbabilityEvidence, ...]) -> ProbabilityEvidence:
    reasons = sorted({evidence.reason for evidence in inputs if evidence.status != "exact"})
    # A union upper bound can exceed one when its unknown components are coupled.
    if lower > 1 + _MASS_TOLERANCE:
        raise ValueError("Disjoint probability mass exceeds one")
    lower, upper = min(1.0, lower), min(1.0, upper)
    if reasons:
        return ProbabilityEvidence.bounded(lower, upper, "; ".join(reasons))
    return ProbabilityEvidence.exact(lower)


def _sum_probability(inputs: tuple[ProbabilityEvidence, ...]) -> ProbabilityEvidence:
    return _derived_probability(fsum(item.lower for item in inputs),
                                fsum(item.upper for item in inputs), inputs)


@dataclass(frozen=True, slots=True)
class OpponentReply:
    move_uci: str
    probability: ProbabilityEvidence
    in_repertoire: bool

    def __post_init__(self) -> None:
        _identifier(self.move_uci)
        if not isinstance(self.in_repertoire, bool):
            raise ValueError("Reply repertoire membership must be explicit")


@dataclass(frozen=True, slots=True)
class OpponentReplyDistribution:
    """All listed replies plus an unassigned bucket partition conditional mass.

    Unassigned mass has an explicit size/bounds, but its allocation is unknown.
    It is neither an authored reply nor evidence of being outside the repertoire.
    """

    replies: tuple[OpponentReply, ...]
    unassigned_mass: ProbabilityEvidence

    def __post_init__(self) -> None:
        replies = _unique(self.replies, lambda reply: reply.move_uci)
        object.__setattr__(self, "replies", replies)
        probabilities = tuple(reply.probability for reply in replies) + (self.unassigned_mass,)
        if (fsum(item.lower for item in probabilities) > 1 + _MASS_TOLERANCE or
                fsum(item.upper for item in probabilities) < 1 - _MASS_TOLERANCE):
            raise ValueError("Reply and unassigned bounds must permit total mass one")

    @property
    def authored_mass(self) -> ProbabilityEvidence:
        return _sum_probability(tuple(reply.probability for reply in self.replies if reply.in_repertoire))

    @property
    def outside_mass(self) -> ProbabilityEvidence:
        return _sum_probability(tuple(reply.probability for reply in self.replies if not reply.in_repertoire))


def route_probability(root_probability: ProbabilityEvidence,
                      opponent_replies: Iterable[ProbabilityEvidence]) -> ProbabilityEvidence:
    """Multiply policy-conditioned root mass and conditional opponent evidence.

    Learner policy moves supply no stochastic factor. Root/context mass must
    exclude any implicit policy-selection weight or learner PGN frequency.
    Selecting a policy never conditions away opponent deviations, outside mass,
    or unassigned mass. No normalization or path floor is applied.
    """
    probabilities = (root_probability, *opponent_replies)
    return _derived_probability(prod(item.lower for item in probabilities),
                                prod(item.upper for item in probabilities), probabilities)


@dataclass(frozen=True, slots=True)
class RouteReach:
    """Incoming prefix mass conditional on following the explicit learner policy.

    Within a policy, context/root pairs must denote disjoint scenarios. Learner
    moves follow that policy; divergent incoming prefixes must be mutually exclusive
    opponent continuations. Only opponent branches are stochastic. Context
    weights cannot encode inferred repertoire-selection probabilities.
    Positions and moves are canonicalized/validated by the existing caller.
    """

    context_id: str
    root_key: str
    move_prefix: tuple[str, ...]
    probability: ProbabilityEvidence
    policy_id: str = field(kw_only=True)

    def __post_init__(self) -> None:
        _identifier(self.policy_id)
        _identifier(self.context_id)
        _identifier(self.root_key)
        object.__setattr__(self, "move_prefix", tuple(self.move_prefix))
        for move in self.move_prefix:
            _identifier(move)

    @property
    def identity(self) -> tuple[str, str, str, tuple[str, ...]]:
        return self.policy_id, self.context_id, self.root_key, self.move_prefix


def decision_reach(routes: Iterable[RouteReach], *, policy_id: str) -> ProbabilityEvidence:
    """Union events within one selected policy, absorbing later revisits."""
    unique_routes = _unique(_for_policy(routes, policy_id), lambda route: route.identity)
    selected_routes: list[RouteReach] = []
    for route in sorted(unique_routes, key=lambda item: (len(item.move_prefix), item.identity)):
        ancestor = next((selected for selected in selected_routes
                         if selected.identity[:3] == route.identity[:3] and
                         route.move_prefix[:len(selected.move_prefix)] == selected.move_prefix), None)
        if ancestor is not None:
            if route.probability.lower > ancestor.probability.upper + _MASS_TOLERANCE:
                raise ValueError("A later revisit cannot exceed its ancestor's reach")
            continue
        selected_routes.append(route)
    return _sum_probability(tuple(route.probability for route in sorted(selected_routes, key=lambda item: item.identity)))


@dataclass(frozen=True, slots=True)
class DecisionReadiness:
    """Recall evidence paired with incoming events for exactly one policy."""

    decision_id: str
    routes: tuple[RouteReach, ...]
    recall: ProbabilityEvidence
    policy_id: str = field(kw_only=True)

    def __post_init__(self) -> None:
        _identifier(self.decision_id)
        object.__setattr__(self, "routes", _unique(_for_policy(self.routes, self.policy_id), lambda route: route.identity))
        if not self.routes:
            raise ValueError("A decision needs reach evidence; use unknown rather than an absent route")


@dataclass(frozen=True, slots=True)
class CardLearningEffect:
    """One hypothetical intervention evaluated within an explicit policy."""

    card_id: str
    projected_readiness: tuple[tuple[str, ProbabilityEvidence], ...]
    policy_id: str = field(kw_only=True)

    def __post_init__(self) -> None:
        _identifier(self.policy_id)
        _identifier(self.card_id)
        projections = tuple((decision_id, probability) for decision_id, probability in self.projected_readiness)
        for decision_id, _ in projections:
            _identifier(decision_id)
        object.__setattr__(self, "projected_readiness", _unique(projections, lambda item: item[0]))


@dataclass(frozen=True, slots=True)
class DecisionContribution:
    decision_id: str
    reach: ProbabilityEvidence
    current_recall: ProbabilityEvidence
    projected_recall: ProbabilityEvidence | None
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class PreparednessResult:
    lower: float
    upper: float
    contributions: tuple[DecisionContribution, ...]
    diagnostics: tuple[str, ...]
    model_version: str = MODEL_VERSION
    policy_id: str = field(kw_only=True)

    @property
    def value(self) -> float | None:
        return self.lower if not self.diagnostics else None


@dataclass(frozen=True, slots=True)
class CardValueResult(PreparednessResult):
    card_id: str = ""


@dataclass(frozen=True, slots=True)
class CardValueRanking:
    ranked: tuple[CardValueResult, ...]
    incomplete: tuple[CardValueResult, ...]
    policy_id: str = field(kw_only=True)


def _decisions(readiness: Iterable[DecisionReadiness], *, policy_id: str) -> tuple[DecisionReadiness, ...]:
    decisions = _unique(_for_policy(readiness, policy_id), lambda decision: decision.decision_id)
    responses_by_event = {}
    for decision in decisions:
        for route in decision.routes:
            previous = responses_by_event.setdefault(route.identity, decision.decision_id)
            if previous != decision.decision_id:
                raise ValueError("One incoming event cannot select multiple learner responses")
    return decisions


def _diagnostics(contributions: tuple[DecisionContribution, ...]) -> tuple[str, ...]:
    diagnostics = set()
    for contribution in contributions:
        for role, evidence in (("reach", contribution.reach), ("current recall", contribution.current_recall),
                               ("projected recall", contribution.projected_recall)):
            if evidence is not None and evidence.status != "exact":
                diagnostics.add(f"{contribution.decision_id}: {role}: {evidence.reason}")
    return tuple(sorted(diagnostics))


def preparedness(readiness: Iterable[DecisionReadiness], *, policy_id: str) -> PreparednessResult:
    """Expected correctly recalled unique learner decisions within one policy.

    Counts can exceed one; this is not whole-horizon survival probability.
    """
    contributions = tuple(DecisionContribution(
        decision.decision_id, reach, decision.recall, None,
        reach.lower * decision.recall.lower, reach.upper * decision.recall.upper,
    ) for decision in _decisions(readiness, policy_id=policy_id)
      for reach in (decision_reach(decision.routes, policy_id=policy_id),))
    return PreparednessResult(fsum(item.lower for item in contributions),
                              fsum(item.upper for item in contributions), contributions,
                              _diagnostics(contributions), policy_id=policy_id)


def marginal_card_value(readiness: Iterable[DecisionReadiness], effect: CardLearningEffect,
                        *, policy_id: str) -> CardValueResult:
    """Bound an explicitly non-regressive projected change in preparedness."""
    _for_policy((effect,), policy_id)
    decisions = {decision.decision_id: decision for decision in _decisions(readiness, policy_id=policy_id)}
    contributions = []
    for decision_id, projected_recall in effect.projected_readiness:
        if decision_id not in decisions:
            raise ValueError(f"Projected decision is absent from readiness: {decision_id}")
        decision = decisions[decision_id]
        if projected_recall.upper < decision.recall.lower:
            raise ValueError("Projected recall cannot permit only a decrease")
        reach = decision_reach(decision.routes, policy_id=policy_id)
        improvement_lower = max(0.0, projected_recall.lower - decision.recall.upper)
        improvement_upper = projected_recall.upper - decision.recall.lower
        contributions.append(DecisionContribution(
            decision_id, reach, decision.recall, projected_recall,
            reach.lower * improvement_lower, reach.upper * improvement_upper,
        ))
    contributions = tuple(contributions)
    return CardValueResult(fsum(item.lower for item in contributions),
                           fsum(item.upper for item in contributions), contributions,
                           _diagnostics(contributions), card_id=effect.card_id, policy_id=policy_id)


def rank_card_values(readiness: Iterable[DecisionReadiness],
                     effects: Iterable[CardLearningEffect], *, policy_id: str) -> CardValueRanking:
    """Hypothetical ranks only; incomplete evidence has no numeric fallback."""
    decisions = _decisions(readiness, policy_id=policy_id)
    values = tuple(marginal_card_value(decisions, effect, policy_id=policy_id)
                   for effect in _unique(_for_policy(effects, policy_id), lambda effect: effect.card_id))
    return CardValueRanking(
        tuple(sorted((value for value in values if value.value is not None),
                     key=lambda value: (-value.value, value.card_id))),
        tuple(value for value in values if value.value is None),
        policy_id=policy_id,
    )
