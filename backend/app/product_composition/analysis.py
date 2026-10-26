"""Concrete Explorer/index/coverage bindings; no policy or queue writes here."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from tempo_evidence_math import LearnerPolicy, move_universe

from app.analysis.coverage.personal_evidence import PersonalGame
from app.analysis.coverage.ports import AnalysisLimits
from app.analysis.coverage.ranking import rank_coverage
from app.analysis.jobs.ports import AnalysisFence, Cancellation, PreparedAnalysis
from app.analysis.probability.ports import ExplorerProbabilityProvider, ExplorerRequest
from app.contracts.product_v02.analysis import PopulationSelection
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire import build_index


def coverage_settings_digest(*, population, since, until, policy, root_fen,
                             approved_route_ids, limits, max_requests) -> str:
    """Settings owner persists this binding; source/data generations are separate."""
    payload = dict(population=population.model_dump(mode="json") if population else None,
        since=since, until=until, policy=asdict(policy), root_fen=root_fen,
        approved_route_ids=sorted(approved_route_ids), limits=asdict(limits),
        max_requests=max_requests, provider="lichess-explorer", model=None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CoverageRequest:
    fence: AnalysisFence
    snapshot: RepertoireSnapshot
    policy: LearnerPolicy
    root_fen: str
    approved_route_ids: tuple[str, ...]
    population: PopulationSelection | None
    since: str
    until: str
    limits: AnalysisLimits
    max_requests: int
    personal_games: tuple[PersonalGame, ...] = ()

    def __post_init__(self):
        if type(self.max_requests) is not int or not 1 <= self.max_requests <= 32:
            raise ValueError("analysis_request_budget_required")
        source = self.snapshot.membership
        if (self.fence.repertoire_id != self.policy.repertoire_id
                or self.fence.repertoire_revision != source.revision
                or self.fence.source_revision != source.source_revision
                or self.fence.snapshot_digest != source.digest):
            raise ValueError("analysis_request_source_mismatch")
        if self.fence.settings_digest != self.settings_digest():
            raise ValueError("analysis_request_settings_mismatch")

    def settings_digest(self) -> str:
        return coverage_settings_digest(population=self.population, since=self.since,
            until=self.until, policy=self.policy, root_fen=self.root_fen,
            approved_route_ids=self.approved_route_ids, limits=self.limits,
            max_requests=self.max_requests)


async def prepare_coverage(request: CoverageRequest, *, explorer: ExplorerProbabilityProvider,
                           credential: str | None, cancellation: Cancellation) -> PreparedAnalysis:
    """Acquire finite evidence, then invoke unchanged #483/#493/#494 policies.

    Missing D04 configuration starts no provider. Any unavailable acquisition
    leaves publication untouched, retaining the last accepted ranking/safe order.
    Credentials are per invocation and never enter result, fence or cache key.
    """
    def unavailable(reason, status="unavailable"):
        return PreparedAnalysis(request.fence, status, reason=reason)

    if request.population is None:
        return unavailable("population_time_controls_required", "setup_required")
    if cancellation.is_set():
        return unavailable("cancelled", "cancelled")
    index = build_index(request.snapshot)
    positions = {}
    approved = set(request.approved_route_ids)
    for (repertoire_id, route_id), occurrences in index.routes.items():
        if repertoire_id != request.fence.repertoire_id or route_id not in approved:
            continue
        for occurrence in occurrences:
            if (not occurrence.learner_turn and not occurrence.terminal
                    and occurrence.ply < request.limits.max_plies):
                positions.setdefault(occurrence.key.position, occurrence)
                if len(positions) > request.max_requests:
                    return unavailable("probability_request_budget_exceeded")
    evidence = []
    for key in sorted(positions):
        if cancellation.is_set():
            return unavailable("cancelled", "cancelled")
        occurrence = positions[key]
        acquisition = await explorer.fetch(ExplorerRequest(
            position=move_universe(occurrence.full_fen), starting_fen=occurrence.starting_fen,
            history_uci=occurrence.history, variant=request.snapshot.variant,
            population=request.population, since=request.since, until=request.until,
            move_limit=256, cache_max_age_seconds=0), credential=credential, cancellation=cancellation)
        if acquisition.diagnostic is not None:
            return unavailable(acquisition.diagnostic.code,
                "cancelled" if acquisition.diagnostic.code == "cancelled" else "unavailable")
        evidence.append(acquisition.evidence)
    if cancellation.is_set():
        return unavailable("cancelled", "cancelled")
    ranking = rank_coverage(snapshot=request.snapshot, index=index, policy=request.policy,
        root_fen=request.root_fen, approved_route_ids=request.approved_route_ids,
        population=request.population, provider="lichess-explorer", model=None,
        evidence=tuple(evidence), limits=request.limits, personal_games=request.personal_games)
    if ranking.introductions.unavailable_reason is not None:
        return unavailable(ranking.introductions.unavailable_reason)
    return PreparedAnalysis(request.fence, "ready", ranking)
