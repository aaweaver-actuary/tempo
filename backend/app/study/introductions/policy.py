"""Pure global allocation. No queue, clock, environment or provider access."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from app.contracts.product_v02.analysis import RankingOutput


@dataclass(frozen=True)
class RankingContext:
    """Study's exact content/settings binding, beyond the factual shared DTO."""
    repertoires: tuple[tuple[str, int, str], ...]
    settings_revision: str

    def __post_init__(self):
        if not self.settings_revision or len({r[0] for r in self.repertoires}) != len(self.repertoires):
            raise ValueError("unique repertoire bindings and explicit settings revision required")
        if any(not name or revision < 1 or not digest for name, revision, digest in self.repertoires):
            raise ValueError("explicit source revision/digest required")
        object.__setattr__(self, "repertoires", tuple(sorted(self.repertoires)))


@dataclass(frozen=True)
class Candidate:
    decision_id: str
    presentation_id: str
    repertoire_id: str
    definition_revision: str
    fallback_order: int
    approved: bool = False
    legal: bool = False
    conflicted: bool = True
    mapped: bool = False
    playable_context: bool = False
    introduced: bool = False

    @property
    def eligible(self):
        # No ancestor stability/maturity gate: legality and an owner-verified
        # playable route remain required. Identity is supplied, never FEN-derived.
        return (bool(self.decision_id and self.presentation_id and self.repertoire_id and self.definition_revision)
                and self.approved and self.legal and not self.conflicted and self.mapped
                and self.playable_context and not self.introduced)


@dataclass(frozen=True)
class RequiredWork:
    presentation_id: str
    kind: str  # due or obligation; order supplied by the existing Study owner

    def __post_init__(self):
        if not self.presentation_id or self.kind not in {"due", "obligation"}:
            raise ValueError("required work must be a due review or admitted obligation")


@dataclass(frozen=True)
class Selection:
    mandatory: tuple[RequiredWork, ...]
    introductions: tuple[Candidate, ...]

    @property
    def ordered_presentations(self):
        return tuple(item.presentation_id for item in (*self.mandatory, *self.introductions))


def compatible_ranking(ranking: RankingOutput, context: RankingContext) -> bool:
    revisions = tuple(sorted(ranking.based_on.repertoire_revisions))
    expected = tuple((name, revision) for name, revision, _ in context.repertoires)
    identifiers = [item.decision_id for item in ranking.ordered]
    ranks = [item.rank for item in ranking.ordered]
    scopes = {name for name, _ in expected}
    facts = ranking.based_on.candidates
    return (ranking.unavailable_reason is None and revisions == expected
            and len(revisions) == len(set(name for name, _ in revisions))
            and len(identifiers) == len(set(identifiers)) and len(ranks) == len(set(ranks))
            and set(identifiers).issubset(item.decision_id for item in facts)
            and all(item.repertoire_id in scopes for item in facts))


def validated_weights(weights: tuple[tuple[str, float], ...]) -> dict[str, Fraction]:
    result = {}
    for repertoire, weight in weights:
        if (not repertoire or repertoire in result or isinstance(weight, bool)
                or not isinstance(weight, (int, float)) or not isfinite(weight) or weight < 0):
            raise ValueError("weights must be unique finite nonnegative relative values")
        result[repertoire] = Fraction(str(weight))
    return result


def _rotation(names, after):
    names = sorted(names)
    return [name for name in names if after is not None and name > after] + [
        name for name in names if after is None or name <= after]


def _allocate(slots: int, weights: dict[str, Fraction], after: str | None) -> dict[str, int]:
    positive = {name: weight for name, weight in weights.items() if weight > 0}
    if not positive:
        return {}
    total = sum(positive.values())
    shares = {name: slots * weight / total for name, weight in positive.items()}
    result = {name: share.numerator // share.denominator for name, share in shares.items()}
    ties = {name: rank for rank, name in enumerate(_rotation(positive, after))}
    order = sorted(positive, key=lambda name: (-(shares[name] - result[name]), ties[name]))
    for name in order[:slots - sum(result.values())]:
        result[name] += 1
    return result


def select(*, candidates: tuple[Candidate, ...], ranking: RankingOutput | None,
           global_budget: int, weights: tuple[tuple[str, float], ...],
           spent: tuple[tuple[str, str], ...] = (), introduced: frozenset[str] = frozenset(),
           mandatory: tuple[RequiredWork, ...] = (), rotation_after: str | None = None,
           enabled: bool = False, paused: bool = False, study_only: bool = False,
           priority: tuple[str, str] | None = None) -> Selection:
    """Largest remainder daily quotas, then weighted spillover; never mutates facts.

    spent maps unique presentations to their credited membership. rotation_after
    is the persisted *start of period* cursor, fixed after the first committed
    introduction. Previewing never changes it. Zero weight explicitly pauses that
    repertoire. A shared presentation is credited to its selected membership.
    """
    if isinstance(global_budget, bool) or not isinstance(global_budget, int) or global_budget < 0:
        raise ValueError("global budget must be a nonnegative integer")
    relative = validated_weights(weights)
    spent_by_id = dict(spent)
    if len(spent_by_id) != len(spent):
        raise ValueError("spent presentations must have exactly one allocation credit")
    required_by_id = {}
    for item in mandatory:
        required_by_id.setdefault(item.presentation_id, item)
    required = tuple(required_by_id.values())
    remaining = max(0, global_budget - len(spent_by_id))
    if not enabled or paused or study_only or not remaining:
        return Selection(required, ())
    rank = {item.decision_id: item.rank for item in ranking.ordered} if ranking is not None else {}
    rank_memberships = {(item.decision_id, item.repertoire_id) for item in ranking.based_on.candidates} if ranking else set()
    order = {name: index for index, name in enumerate(_rotation(relative, rotation_after))}

    def preference(candidate):
        known = (candidate.decision_id, candidate.repertoire_id) in rank_memberships and candidate.decision_id in rank
        return (0 if priority == (candidate.repertoire_id, candidate.decision_id) else 1,
                0 if known else 1, rank.get(candidate.decision_id, 0) if known else candidate.fallback_order,
                order.get(candidate.repertoire_id, len(order)), candidate.presentation_id, candidate.decision_id)

    excluded = introduced | spent_by_id.keys() | {item.presentation_id for item in required}
    pool = sorted((item for item in candidates if item.eligible and item.presentation_id not in excluded
                   and relative.get(item.repertoire_id, 0) > 0), key=preference)
    quotas = _allocate(global_budget, relative, rotation_after)
    for repertoire in spent_by_id.values():
        quotas[repertoire] = max(0, quotas.get(repertoire, 0) - 1)
    choices = []
    while pool and len(choices) < remaining:
        owed = [item for item in pool if quotas.get(item.repertoire_id, 0) > 0]
        if not owed:
            available = {item.repertoire_id for item in pool}
            quotas = _allocate(remaining - len(choices), {name: relative[name] for name in available}, rotation_after)
            owed = [item for item in pool if quotas.get(item.repertoire_id, 0) > 0]
        choice = min(owed, key=preference)
        choices.append(choice)
        quotas[choice.repertoire_id] -= 1
        pool = [item for item in pool if item.presentation_id != choice.presentation_id]
    return Selection(required, tuple(choices))
