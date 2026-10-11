"""Pure selected-policy valuation, exporting the existing #155 shadow contract.

Inputs are caller-selected evidence, not a live-data capture or an admission plan.
Scores are expected correctly recalled unique decisions, not survival probabilities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date, datetime, timezone
import json

from .decision_readiness import READINESS_VERSION, ReadinessSnapshot, decision_readiness
from .opening_policy_reach import (
    REACH_VERSION, LearnerPolicy, PolicyReachContext, PolicyReachRoute,
    SelectedOpponentEvidence, propagate_policy_reach,
)
from .opening_preparedness import (
    MODEL_VERSION, CardLearningEffect, DecisionReadiness, ProbabilityEvidence,
    marginal_card_value, preparedness,
)
from .opening_ranking_evaluation import (
    SCHEMA_VERSION, OpeningRankingCandidate, OpeningRankingContext, ProductionOrderScorer, ScoreResult,
    canonical_json, compare, load_snapshot, rank, snapshot_document,
)
from .opening_segmentation import decision_identity, opening_position_key, presentation_occurrences

VALUATION_VERSION = "selected-policy-card-valuation-v1"
SCORE_KIND = "marginal-expected-correct-decisions"


def _plain(value):
    """Detach diagnostic evidence; never stringify an opaque object or use a clock."""
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _unique(entries, identity):
    entries_by_identity = {}
    for entry in entries:
        entry_identity = identity(entry)
        if entry_identity in entries_by_identity and entries_by_identity[entry_identity] != entry:
            raise ValueError(f"Conflicting valuation evidence: {entry_identity}")
        entries_by_identity[entry_identity] = entry
    return tuple(entries_by_identity[key] for key in sorted(entries_by_identity))


def _ordered_evidence(entries):
    return tuple(sorted(entries, key=lambda entry: canonical_json(_plain(entry))))


@dataclass(frozen=True)
class DecisionReadinessBinding:
    policy_id: str
    policy_version: int
    decision_id: str
    snapshot: ReadinessSnapshot
    decision_index: int

    def __post_init__(self):
        # Existing readiness dataclasses are frozen but accept list-valued inputs.
        # Detach and canonicalize all nested collections at this boundary.
        current = self.snapshot
        observations = tuple(replace(observation,
            assistance_before_response=tuple(observation.assistance_before_response))
            for observation in current.observations)
        object.__setattr__(self, "snapshot", replace(current, moves=tuple(current.moves),
            reviews=_ordered_evidence(current.reviews), observations=_ordered_evidence(observations),
            game_events=_ordered_evidence(current.game_events)))


@dataclass(frozen=True)
class ValuationCandidate:
    candidate: OpeningRankingCandidate
    policy_version: int
    card_revision: int
    affected_decision_ids: tuple[str, ...]
    route_publications: tuple[tuple[str, str], ...]
    effect: CardLearningEffect | None = None
    effect_reference: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "affected_decision_ids", tuple(sorted(set(self.affected_decision_ids))))
        object.__setattr__(self, "route_publications", tuple(sorted(set(tuple(reference)
            for reference in self.route_publications))))


@dataclass(frozen=True)
class OpeningCardValuationSnapshot:
    policy: LearnerPolicy
    context: PolicyReachContext
    routes: tuple[PolicyReachRoute, ...]
    opponent_evidence: tuple[SelectedOpponentEvidence, ...]
    readiness: tuple[DecisionReadinessBinding, ...]
    candidates: tuple[ValuationCandidate, ...]
    as_of: datetime
    study_day: date
    version: str = VALUATION_VERSION

    def __post_init__(self):
        if self.version != VALUATION_VERSION:
            raise ValueError("Unsupported valuation input version")
        if type(self.study_day) is not date:
            raise ValueError("Valuation study_day requires an explicit date")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("Valuation as_of requires an explicit timezone")
        object.__setattr__(self, "as_of", self.as_of.astimezone(timezone.utc))
        for field_name, identity in (
            ("routes", lambda route: (route.publication_id, route.line_id)),
            ("opponent_evidence", lambda evidence: evidence.evidence_id),
            ("readiness", lambda binding: binding.decision_id),
            ("candidates", lambda candidate: candidate.candidate.card_id),
        ):
            object.__setattr__(self, field_name, _unique(getattr(self, field_name), identity))


def _validate_inputs(snapshot):
    """Fence every identity before readiness projection or preparedness scoring."""
    policy = snapshot.policy
    actions = {action.position_key: action.move_uci for action in policy.actions}
    known_decisions = {decision_identity(policy.repertoire_id, position, policy.trained_color,
        move, policy.policy_version) for position, move in actions.items()}
    if any(route.policy_id != policy.policy_id for route in snapshot.routes):
        raise ValueError("Foreign policy route")
    if any(evidence.context_id != snapshot.context.context_id for evidence in snapshot.opponent_evidence):
        raise ValueError("Opponent context mismatch")
    for evidence in snapshot.opponent_evidence:
        selected = evidence.snapshot()
        if evidence.kind == "FusedMoveDistribution" and selected.target_context != json.loads(snapshot.context.target_context_json):
            raise ValueError("Opponent target context mismatch")
    for binding in snapshot.readiness:
        if type(binding.policy_version) is not int or (binding.policy_id, binding.policy_version) != (policy.policy_id, policy.policy_version):
            raise ValueError("Foreign policy readiness")
        current = binding.snapshot
        presentation = current.presentation
        if (presentation.repertoire_id, presentation.trained_color) != (policy.repertoire_id, policy.trained_color):
            raise ValueError("Foreign readiness presentation")
        if type(presentation.card_revision) is not int or presentation.card_revision < 1:
            raise ValueError("Invalid readiness presentation revision")
        if any(review.card_id != presentation.card_id for review in current.reviews):
            raise ValueError("Foreign readiness review")
        if any(observation.presentation != presentation for observation in current.observations):
            raise ValueError("Foreign readiness observation")
        if any((event.card_id, event.repertoire_id, event.trained_color) != (
                presentation.card_id, presentation.repertoire_id, presentation.trained_color)
                for event in current.game_events):
            raise ValueError("Foreign readiness game evidence")
        occurrences = presentation_occurrences(policy.repertoire_id, {
            "id": presentation.card_id, "revision": presentation.card_revision,
            "trained_color": presentation.trained_color, "start_fen": current.start_fen,
            "moves_json": canonical_json(current.moves),
        })
        if type(binding.decision_index) is not int or not 0 <= binding.decision_index < len(occurrences):
            raise ValueError("Invalid readiness decision index")
        for observation in current.observations:
            if type(observation.decision_index) is not int or not 0 <= observation.decision_index < len(occurrences):
                raise ValueError("Invalid observation decision index")
            observed_decision = occurrences[observation.decision_index]
            if (observation.decision_id, observation.expected_uci) != (
                    observed_decision["decision_id"], observed_decision["expected_uci"]):
                raise ValueError("Foreign readiness observation decision")
        presentation_decisions = {occurrence["decision_id"] for occurrence in occurrences}
        for event in current.game_events:
            if decision_identity(event.repertoire_id, event.fen_key, event.trained_color,
                                 event.expected_uci) not in presentation_decisions:
                raise ValueError("Foreign readiness game decision")
        occurrence = occurrences[binding.decision_index]
        position = opening_position_key(occurrence["decision_fen"])
        if position in actions and actions[position] != occurrence["expected_uci"]:
            raise ValueError("Readiness contradicts selected policy action")
        expected_identity = decision_identity(policy.repertoire_id, occurrence["decision_fen"],
            policy.trained_color, occurrence["expected_uci"], policy.policy_version)
        if binding.decision_id != expected_identity:
            raise ValueError("Readiness decision identity mismatch")
        known_decisions.add(expected_identity)
    publications = {(route.publication_id, route.line_id) for route in snapshot.routes}
    production_ranks = []
    for entry in snapshot.candidates:
        candidate = entry.candidate
        if type(entry.policy_version) is not int or (candidate.policy_id, entry.policy_version) != (policy.policy_id, policy.policy_version):
            raise ValueError("Foreign policy candidate")
        if type(entry.card_revision) is not int or entry.card_revision < 1:
            raise ValueError("Invalid candidate revision")
        if not entry.affected_decision_ids or not set(entry.affected_decision_ids) <= known_decisions:
            raise ValueError("Candidate requires explicit selected-policy decisions")
        if not entry.route_publications or not set(entry.route_publications) <= publications:
            raise ValueError("Candidate route/publication mismatch")
        metadata = candidate.metadata()
        if "valuation" in metadata:
            raise ValueError("Input metadata cannot supply computed valuation")
        for membership in metadata.get("memberships", []):
            if (membership.get("repertoire_id") != policy.repertoire_id
                    or membership.get("policy_id", policy.policy_id) != policy.policy_id):
                raise ValueError("Foreign candidate membership")
        for route in metadata.get("routes", []):
            if (route.get("repertoire_id") != policy.repertoire_id
                    or route.get("policy_id", policy.policy_id) != policy.policy_id):
                raise ValueError("Foreign candidate route metadata")
        if entry.effect is not None:
            if (entry.effect.policy_id, entry.effect.card_id) != (policy.policy_id, candidate.card_id):
                raise ValueError("Foreign policy/card learning effect")
            if not isinstance(entry.effect_reference, str) or not entry.effect_reference.strip():
                raise ValueError("Learning effects require explicit provenance")
            if not {identity for identity, _ in entry.effect.projected_readiness} <= set(entry.affected_decision_ids):
                raise ValueError("Effect references undeclared affected decision")
        production_ranks.append(candidate.production_rank)
    if sorted(production_ranks) != list(range(1, len(production_ranks) + 1)):
        raise ValueError("Production ranks must cover the candidate set exactly once")


@dataclass(frozen=True)
class PrecomputedValuationScorer:
    """Agent 5 seam: score bundles in digest-bound #155 candidate metadata."""
    name: str = SCORE_KIND
    version: str = VALUATION_VERSION

    def score(self, candidate, context):
        valuation = candidate.metadata().get("valuation")
        versions = json.loads(context.source_versions_json)
        if (context.evaluation_scope != "selected_policy" or candidate.policy_id != context.policy_id
                or not isinstance(valuation, dict) or valuation.get("version") != self.version
                or valuation.get("policy_id") != context.policy_id
                or valuation.get("card_id") != candidate.card_id
                or valuation.get("policy_version") != versions.get("policy_version")
                or versions.get("valuation") != self.version
                or versions.get("preparedness") != MODEL_VERSION):
            return ScoreResult(None, "missing_or_incompatible_valuation_bundle")
        return ScoreResult(valuation["value"], "; ".join(valuation["diagnostics"]) or None)


def replay_opening_card_valuation(document: dict, top_k=(1, 5, 10)) -> dict:
    """Replay precomputed scores, preserving the shared shadow comparison schema."""
    candidates, context = load_snapshot(document)
    ranking = rank(candidates, context, PrecomputedValuationScorer())
    result = compare(candidates, (ranking,), top_k)
    result.update(snapshot_id=context.snapshot_id, as_of=context.as_of, study_day=context.study_day,
        source_versions=json.loads(context.source_versions_json),
        scorer_configuration=json.loads(context.scorer_configuration_json))
    return result


def evaluate_opening_card_valuation(snapshot: OpeningCardValuationSnapshot, top_k=(1, 5, 10)) -> dict:
    """Propagate once, project real readiness, value explicit effects, then rank.

    The returned #155 comparison embeds its #155 snapshot for offline score replay.
    Bounds and unresolved paths remain metadata, never fallback numeric ranks.
    """
    _validate_inputs(snapshot)
    policy_id = snapshot.policy.policy_id
    reach = propagate_policy_reach(snapshot.policy, snapshot.routes, snapshot.opponent_evidence,
                                   context=snapshot.context)
    reach_by_decision = {decision.identity: decision for decision in reach.decisions}
    bindings = {binding.decision_id: binding for binding in snapshot.readiness}
    readiness_provenance = {}
    kernel_readiness = []
    for decision in reach.decisions:
        binding = bindings.get(decision.identity)
        estimate = (decision_readiness(binding.snapshot, snapshot.as_of, decision_index=binding.decision_index)
                    if binding is not None else None)
        recall = (ProbabilityEvidence.exact(estimate.readiness_score)
                  if estimate is not None and estimate.readiness_score is not None else
                  ProbabilityEvidence.unknown("missing_decision_readiness:" + decision.identity if estimate is None
                      else "decision_readiness:" + "; ".join(estimate.reasons)))
        readiness_provenance[decision.identity] = {"recall": _plain(recall), "estimate": _plain(estimate)}
        if not decision.unresolved:
            kernel_readiness.append(DecisionReadiness(decision.identity, decision.kernel_routes(), recall,
                                                     policy_id=policy_id))
    baseline = preparedness(kernel_readiness, policy_id=policy_id)
    unresolved = tuple(decision.identity for decision in reach.decisions if decision.unresolved)
    candidates = []
    for entry in snapshot.candidates:
        diagnostics = []
        missing_reach = set(entry.affected_decision_ids) - reach_by_decision.keys()
        diagnostics.extend("missing_decision_reach:" + identity for identity in sorted(missing_reach))
        diagnostics.extend("unresolved_policy_action:" + identity for identity in entry.affected_decision_ids
                           if identity in unresolved)
        effect = entry.effect
        if not diagnostics:
            projections = dict(effect.projected_readiness) if effect is not None else {}
            effect = CardLearningEffect(entry.candidate.card_id, tuple((identity,
                projections.get(identity, ProbabilityEvidence.unknown("missing_learning_effect:" + identity)))
                for identity in entry.affected_decision_ids), policy_id=policy_id)
            value = marginal_card_value(kernel_readiness, effect, policy_id=policy_id)
            diagnostics.extend(value.diagnostics)
        else:
            value = None
        valuation = {"version": VALUATION_VERSION, "score_kind": SCORE_KIND,
            "policy_id": policy_id, "policy_version": snapshot.policy.policy_version,
            "card_id": entry.candidate.card_id, "card_revision": entry.card_revision,
            "value": value.value if value is not None else None,
            "marginal": _plain(value), "diagnostics": sorted(set(diagnostics)),
            "affected_decision_ids": entry.affected_decision_ids,
            "reach": [_plain(reach_by_decision[identity]) for identity in entry.affected_decision_ids
                      if identity in reach_by_decision],
            "readiness": {identity: readiness_provenance.get(identity) for identity in entry.affected_decision_ids},
            "effect": _plain(effect), "effect_reference": entry.effect_reference,
            "route_publications": entry.route_publications}
        candidates.append(replace(entry.candidate,
            metadata_json=canonical_json({**entry.candidate.metadata(), "valuation": valuation})))
    versions = {"valuation": VALUATION_VERSION, "reach": REACH_VERSION, "readiness": READINESS_VERSION,
                "preparedness": MODEL_VERSION, "opponent_probability": 1, "shadow": SCHEMA_VERSION,
                "policy_id": policy_id, "policy_version": snapshot.policy.policy_version}
    configuration = {"score_kind": SCORE_KIND, "conditioning": "learner follows selected policy",
        "inputs": _plain(snapshot), "reach": _plain(reach), "preparedness": _plain(baseline),
        "preparedness_scope": "resolved_unique_decisions", "unresolved_decision_ids": unresolved,
        "complete_preparedness": baseline.value is not None and not unresolved
            and not any(path.missing_current_action for path in reach.paths),
        "baseline_bundle": "production-order@1",
        "precomputed_score_bundles": [{"name": "production-order", "version": "1", "axis": "baseline",
            "cohort_id": snapshot.context.context_id, "assumption": "Caller-supplied complete production order.",
            "provenance": "#155 ProductionOrderScorer; no observed admission delivery",
            "scores": {candidate.card_id: {"score": ProductionOrderScorer().score(candidate, None).score,
                                          "reason": None} for candidate in candidates}},
            {"name": SCORE_KIND, "version": VALUATION_VERSION, "axis": "valuation",
            "cohort_id": snapshot.context.context_id,
            "assumption": "Explicit projected effects; independent interventions against a fixed baseline, not estimated gains.",
            "provenance": "candidate.metadata.valuation and captured foundation inputs",
            "scores": {candidate.card_id: {"score": candidate.metadata()["valuation"]["value"],
                "reason": "; ".join(candidate.metadata()["valuation"]["diagnostics"]) or None}
                for candidate in candidates}}],
        "replay_basis": "precomputed_scores_not_reestimated_learning_gains"}
    context = OpeningRankingContext(snapshot.as_of.isoformat(), snapshot.study_day.isoformat(), "pending",
        canonical_json(versions), canonical_json(configuration), evaluation_scope="selected_policy", policy_id=policy_id)
    document = snapshot_document(tuple(candidates), context, admission_plan_basis="not_evaluated_read_only")
    result = replay_opening_card_valuation(document, top_k)
    result["snapshot"] = document
    return result
