"""Pure reach ranking. No provider, persistence, engine or Study calls."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json

import chess
from tempo_evidence_math import (
    LearnerPolicy, ProbabilityEvidence as ProbabilityBounds, RouteReach,
    coverage_mass_summary, decision_reach, opening_position_key,
    route_probability, stable_key,
)

from app.contracts.product_v02.analysis import (
    PopulationSelection, ProbabilityEvidence, RankedDecision, RankingCandidate,
    RankingInput, RankingOutput,
)
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot

from .personal_evidence import PersonalEncounter, PersonalGame, deduplicate_games, encounter_sources
from .ports import AnalysisLimits, RepertoireIndex

POLICY_VERSION = "population-reach-personal-tie-v1"


def rank_candidates(facts: RankingInput) -> RankingOutput:
    """Population reach first; personal games break ties without fusing estimates."""
    identities = [(item.repertoire_id, item.decision_id) for item in facts.candidates]
    if len(set(identities)) != len(identities) or len({item.decision_id for item in facts.candidates}) != len(identities):
        raise ValueError("ranking_requires_unambiguous_decision_ids")
    ordered = sorted(facts.candidates, key=lambda item: (
        item.reach_probability is None, -(item.reach_probability or 0),
        -(item.personal_encounters or 0), item.uncertainty is None,
        item.uncertainty or 0, item.evidence_support is None,
        -(item.evidence_support or 0), item.repertoire_id, item.decision_id))
    return RankingOutput(based_on=facts, ordered=tuple(
        RankedDecision(decision_id=item.decision_id, rank=rank,
                       reason=POLICY_VERSION if item.reach_probability is not None else "population_reach_unknown")
        for rank, item in enumerate(ordered, 1)),
        unavailable_reason="population_reach_unknown" if ordered and all(
            item.reach_probability is None for item in ordered) else None)


@dataclass(frozen=True)
class RouteSupport:
    route_id: str
    ply: int
    prefix: tuple[str, ...]
    position_reach: ProbabilityBounds
    conditional_reply: ProbabilityBounds | None
    encounter_reach: ProbabilityBounds
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, order=True)
class Diagnostic:
    route_id: str
    ply: int
    code: str
    unknown_mass: float | None = None


@dataclass(frozen=True)
class CandidateDetail:
    candidate_id: str
    kind: str
    repertoire_id: str
    position_key: str
    move_uci: str
    target_fen: str
    covered: bool
    position_reach: ProbabilityBounds
    encounter_reach: ProbabilityBounds
    supports: tuple[RouteSupport, ...]
    presentations: tuple[DecisionPresentation, ...]
    population_evidence: tuple[ProbabilityEvidence, ...]
    personal_evidence: tuple[ProbabilityEvidence, ...]
    personal_sources: tuple[tuple[str, str, str, int], ...]
    dependency_digest: str


@dataclass(frozen=True)
class CoverageRanking:
    """One repertoire/policy/root scope; no global budget or queue admission."""
    gaps: RankingOutput
    introductions: RankingOutput
    details: tuple[CandidateDetail, ...]
    diagnostics: tuple[Diagnostic, ...]
    policy_version: str
    source_revision: str
    snapshot_digest: str
    settings_digest: str
    dependency_digest: str


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _reply(evidence: ProbabilityEvidence | None, move: str) -> ProbabilityBounds:
    if evidence is None or evidence.distribution is None:
        return ProbabilityBounds.unknown("probability_unavailable")
    value = next(item.probability for item in evidence.distribution.moves if item.move_uci == move)
    if value is None:
        return ProbabilityBounds.bounded(0, evidence.distribution.unknown_mass, "unassigned_reply_mass")
    # Public distributions tolerate roundoff at one; arithmetic requires [0,1].
    return ProbabilityBounds.exact(min(1.0, value))


def rank_coverage(*, snapshot: RepertoireSnapshot, index: RepertoireIndex,
                  policy: LearnerPolicy, root_fen: str,
                  approved_route_ids: tuple[str, ...], population: PopulationSelection,
                  provider: str, model: str | None,
                  evidence: tuple[ProbabilityEvidence, ...], limits: AnalysisLimits,
                  personal_games: tuple[PersonalGame, ...] = ()) -> CoverageRanking:
    """Rank finite approved routes under one selected deterministic own policy.

    Outer composition supplies #483's public immutable index and approved-route
    authority. D04 population is specific to the selected provider/model. Each
    source-game observation set is complete for its revision, not an append delta.
    """
    if not isinstance(population, PopulationSelection) or not provider:
        raise ValueError("explicit_provider_population_settings_required")
    membership = snapshot.membership
    if not membership.available or not membership.complete:
        raise ValueError("complete_available_snapshot_required")
    if (index.snapshot_id, index.source_revision, index.revision, index.digest) != (
            membership.snapshot_id, membership.source_revision, membership.revision, membership.digest):
        raise ValueError("stale_repertoire_index")
    if snapshot.variant != "standard" or policy.trained_color != snapshot.learner_color:
        raise ValueError("unsupported_policy_scope")
    if policy.repertoire_id not in membership.scope_repertoire_ids:
        raise ValueError("policy_outside_snapshot_scope")
    if len(set(approved_route_ids)) != len(approved_route_ids):
        raise ValueError("duplicate_approved_route")
    routes = {route.route_id: route for route in membership.routes
              if route.repertoire_id == policy.repertoire_id}
    if sum(len(route.moves) for route in routes.values()) > 100000:
        raise ValueError("approved_topology_exceeds_index_bound")
    if not set(approved_route_ids) <= routes.keys():
        raise ValueError("approved_route_outside_repertoire")
    root = opening_position_key(root_fen)
    actions = {action.position_key: action.move_uci for action in policy.actions}
    selected = [routes[key] for key in sorted(approved_route_ids)
                if routes[key].policy_id == policy.policy_id]
    if any(opening_position_key(route.starting_fen) != root or
           route.learner_color != policy.trained_color for route in selected):
        raise ValueError("routes_require_one_common_root_and_learner")
    games = deduplicate_games(tuple(personal_games))
    by_id, selected_evidence = {}, {}
    for item in evidence:
        if item.evidence_id in by_id and by_id[item.evidence_id] != item:
            raise ValueError("conflicting_probability_evidence_id")
        by_id[item.evidence_id] = item
        if item.basis == "population" and (item.provider, item.model) == (provider, model):
            if item.population != population:
                raise ValueError("probability_population_settings_mismatch")
            key = item.position.fen_key
            if key in selected_evidence and selected_evidence[key] != item:
                raise ValueError("select_one_probability_snapshot_per_position")
            selected_evidence[key] = item

    settings = dict(policy_version=POLICY_VERSION, provider=provider, model=model,
                    population=population.model_dump(mode="json"),
                    policy_id=policy.policy_id, repertoire_id=policy.repertoire_id,
                    root=root, approved_route_ids=sorted(approved_route_ids),
                    actions=sorted(actions.items()), limits=vars(limits))
    settings_digest = _digest(settings)
    diagnostics = []
    if len(selected) > limits.max_routes:
        diagnostics.extend(Diagnostic(route.route_id, 0, "route_limit")
                           for route in selected[limits.max_routes:])
    selected = selected[:limits.max_routes]
    # All approved selected routes define coverage, including routes beyond the
    # analysis budget. Truncating computation cannot manufacture missing content.
    covered_edges = defaultdict(set)
    for route_id in approved_route_ids:
        route = routes[route_id]
        if route.policy_id != policy.policy_id:
            continue
        board = chess.Board(route.starting_fen)
        for move in route.moves:
            key = opening_position_key(board.fen())
            if chess.Move.from_uci(move) not in board.legal_moves:
                raise ValueError("illegal_approved_route")
            if board.turn == (policy.trained_color == "white"):
                node = index.lookup(policy.repertoire_id, board.fen(), snapshot.variant)
                if node is None or node.conflict or actions.get(key) != move:
                    break
            else:
                covered_edges[key].add(move)
            board.push_uci(move)

    supports = defaultdict(list)
    attributes = {}
    bindings = defaultdict(dict)
    visits = 0
    for route in selected:
        board = chess.Board(route.starting_fen)
        factors, source_ids = [], set()
        for ply, move in enumerate(route.moves):
            if ply >= limits.max_plies or visits >= limits.max_positions:
                diagnostics.append(Diagnostic(route.route_id, ply,
                    "horizon_limit" if ply >= limits.max_plies else "position_limit"))
                break
            visits += 1
            key = opening_position_key(board.fen())
            node = index.lookup(policy.repertoire_id, board.fen(), snapshot.variant)
            if node is None:
                raise ValueError("index_missing_approved_position")
            prefix = tuple(route.moves[:ply])
            position_reach = route_probability(ProbabilityBounds.exact(1), factors)
            own_turn = board.turn == (policy.trained_color == "white")
            if own_turn:
                if node.conflict:
                    diagnostics.append(Diagnostic(route.route_id, ply, "own_response_conflict"))
                    break
                if actions.get(key) != move:
                    diagnostics.append(Diagnostic(route.route_id, ply, "outside_selected_own_policy"))
                    break
                occurrences = [item for item in node.occurrences
                               if item.route_id == route.route_id and item.ply == ply]
                occurrence = occurrences[0] if len(occurrences) == 1 else None
                mapping = occurrence.bindings[0] if occurrence and occurrence.route_complete and len(occurrence.bindings) == 1 else None
                if (mapping is not None and mapping.introduction == "unintroduced" and mapping.available
                        and mapping.repertoire_revision == membership.revision
                        and mapping.position_key == key and mapping.selected_response_uci == move
                        and mapping.repertoire_id == policy.repertoire_id):
                    candidate_key = "introduction", key, move
                    bindings[candidate_key][mapping.model_dump_json()] = mapping
                    attributes[candidate_key] = (board.fen(), True)
                    supports[candidate_key].append(RouteSupport(route.route_id, ply, prefix,
                        position_reach, None, position_reach, tuple(sorted(source_ids))))
                else:
                    diagnostics.append(Diagnostic(route.route_id, ply, "introduction_ineligible_or_mapping_unknown"))
            else:
                source = selected_evidence.get(key)
                if source is not None:
                    source_ids.add(source.evidence_id)
                if source is None or source.distribution is None:
                    diagnostics.append(Diagnostic(route.route_id, ply, "probability_unavailable", 1.0))
                else:
                    mass = coverage_mass_summary(source.distribution, covered_edges[key])
                    if mass.unknown_mass:
                        diagnostics.append(Diagnostic(route.route_id, ply, "unassigned_reply_mass", mass.unknown_mass))
                for reply in sorted(candidate.uci() for candidate in board.legal_moves):
                    conditional = _reply(source, reply)
                    encounter = route_probability(position_reach, (conditional,))
                    candidate_key = "opponent_reply", key, reply
                    target = board.copy()
                    target.push_uci(reply)
                    attributes[candidate_key] = (target.fen(), reply in covered_edges[key])
                    supports[candidate_key].append(RouteSupport(route.route_id, ply, prefix,
                        position_reach, conditional, encounter, tuple(sorted(source_ids))))
                factors.append(_reply(source, move))
            board.push_uci(move)
        else:
            boundary = next(item for item in snapshot.boundaries if item.route_id == route.route_id)
            if not boundary.deliberate:
                diagnostics.append(Diagnostic(route.route_id, len(route.moves), "non_deliberate_boundary"))

    details, gap_facts, introduction_facts = [], [], []
    for candidate_key in sorted(supports):
        kind, key, move = candidate_key
        route_supports = tuple(sorted(set(supports[candidate_key]), key=lambda item: (item.route_id, item.ply)))
        def union(encounter):
            return decision_reach(tuple(RouteReach(policy.policy_id, root,
                item.prefix + ((move,) if encounter and kind == "opponent_reply" else ()),
                item.encounter_reach if encounter else item.position_reach,
                policy_id=policy.policy_id) for item in route_supports), policy_id=policy.policy_id)
        position_reach, reach = union(False), union(True)
        presentations = tuple(bindings[candidate_key][binding_key] for binding_key in sorted(bindings[candidate_key]))
        if kind == "introduction":
            if len({item.decision_id for item in presentations}) != 1:
                raise ValueError("ambiguous_transposed_decision_identity")
            identity = presentations[0].decision_id
        else:
            identity = stable_key("opponent-edge", policy.repertoire_id, snapshot.variant, key, move)
        used_ids = sorted({source_id for item in route_supports for source_id in item.evidence_ids})
        population_evidence = tuple(by_id[source_id] for source_id in used_ids)
        personal_evidence = tuple(sorted((item for item in by_id.values()
            if item.basis == "personal" and item.position.fen_key == key), key=lambda item: item.evidence_id))
        personal_sources = encounter_sources(games, PersonalEncounter(policy.repertoire_id, key, move),
                                             match_move=kind == "opponent_reply")
        target_fen, covered = attributes[candidate_key]
        dependency_digest = _digest(dict(settings=settings, source_revision=membership.source_revision,
            repertoire_revision=membership.revision, snapshot_digest=membership.digest,
            candidate=candidate_key, routes=[(item.route_id, item.prefix) for item in route_supports],
            presentations=[item.model_dump(mode="json") for item in presentations],
            population=[item.model_dump(mode="json") for item in population_evidence],
            personal=[item.model_dump(mode="json") for item in personal_evidence], games=personal_sources))
        detail = CandidateDetail(identity, kind, policy.repertoire_id, key, move, target_fen, covered,
            position_reach, reach, route_supports, presentations, population_evidence,
            personal_evidence, personal_sources, dependency_digest)
        details.append(detail)
        totals = [item.counts.total_count for item in population_evidence if item.counts is not None]
        fact = RankingCandidate(decision_id=identity, repertoire_id=policy.repertoire_id,
            reach_probability=reach.lower if reach.status == "exact" else None,
            evidence_support=min(totals) if totals and len(totals) == len(population_evidence) else None,
            uncertainty=reach.upper - reach.lower, personal_encounters=len(personal_sources))
        if kind == "introduction":
            introduction_facts.append(fact)
        elif not covered:
            gap_facts.append(fact)
    def output(candidates):
        candidate_ids = {item.decision_id for item in candidates}
        evidence_ids = tuple(sorted({item.evidence_id for detail in details
            if detail.candidate_id in candidate_ids for item in (*detail.population_evidence, *detail.personal_evidence)}))
        return rank_candidates(RankingInput(repertoire_revisions=((policy.repertoire_id, membership.revision),),
            candidates=tuple(candidates), evidence_ids=evidence_ids))
    details = tuple(details)
    return CoverageRanking(output(gap_facts), output(introduction_facts), details,
        tuple(sorted(set(diagnostics))), POLICY_VERSION, membership.source_revision, membership.digest,
        settings_digest, _digest(dict(settings_digest=settings_digest,
            source_revision=membership.source_revision, snapshot_digest=membership.digest,
            details=[item.dependency_digest for item in details],
            diagnostics=[vars(item) for item in sorted(set(diagnostics))])))
