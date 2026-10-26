"""Concrete Explorer/index/coverage bindings; no policy or queue writes here."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from tempo_evidence_math import LearnerPolicy, move_universe

from app.analysis.coverage.personal_evidence import PersonalGame
from app.analysis.coverage.ports import AnalysisLimits
from app.analysis.coverage.ranking import rank_candidates, rank_coverage
from app.analysis.jobs.ports import AnalysisFence, AnalysisPublications, Cancellation, PreparedAnalysis
from app.analysis.jobs.stockfish import EngineBridgeUnavailable
from app.analysis.proposals.coordinator import ProposalCoordinator
from app.analysis.proposals.ports import CapabilityUnavailable
from app.analysis.probability.ports import ExplorerProbabilityProvider, ExplorerRequest
from app.analysis.similarity.retrieval import RetrievalRequest, retrieve
from app.contracts.product_v02.analysis import PopulationSelection, RankingInput
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire import build_index
from app.study.introductions.application import Introductions
from app.study.introductions.policy import RankingContext


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


def introduction_context(fences: tuple[AnalysisFence, ...]) -> RankingContext:
    """Outer current-facts adapter uses this same binding for the Study receiver.

    No Study module imports this function. The composition-owned facts adapter
    must read the current owner revisions inside the borrowed Study transaction.
    A global controls edit increments settings_revision for affected scopes.
    """
    ordered = sorted(fences, key=lambda fence: fence.repertoire_id)
    if not ordered or len({item.repertoire_id for item in ordered}) != len(ordered):
        raise ValueError("unique_nonempty_ranking_scopes_required")
    generations = [(item.repertoire_id, item.source_revision, item.settings_revision,
                    item.settings_digest, item.personal_revision, item.evidence_revision)
                   for item in ordered]
    binding = hashlib.sha256(json.dumps(generations, separators=(",", ":")).encode()).hexdigest()
    return RankingContext(tuple((item.repertoire_id, item.repertoire_revision, item.snapshot_digest)
                                for item in ordered), binding)


def accept_published_introductions(*, day: str, results: tuple[PreparedAnalysis, ...],
                                  publications: AnalysisPublications,
                                  introductions: Introductions) -> str:
    """Submit current advisory facts through #495; never call its queue writer.

    Analysis reads release before the caller enters Study acceptance. #495's
    accept_ranking rechecks its current exact context. Owning transactions and
    current-facts generation reads are injected by product composition.
    """
    if not results or any(item.status != "ready" or item.ranking is None for item in results):
        return "unavailable"
    fences = tuple(item.fence for item in results)
    context = introduction_context(fences)
    if any(publications.current(item.repertoire_id) != item for item in fences):
        return "obsolete"
    for result in results:
        ranking, fence = result.ranking, result.fence
        if (ranking.source_revision != fence.source_revision or ranking.snapshot_digest != fence.snapshot_digest
                or ranking.introductions.based_on.repertoire_revisions != (
                    (fence.repertoire_id, fence.repertoire_revision),)):
            return "obsolete"
    facts = RankingInput(repertoire_revisions=tuple((name, revision) for name, revision, _ in context.repertoires),
        candidates=tuple(candidate for item in results for candidate in item.ranking.introductions.based_on.candidates),
        evidence_ids=tuple(sorted({identity for item in results
                                  for identity in item.ranking.introductions.based_on.evidence_ids})))
    return introductions.accept_ranking(day, context, rank_candidates(facts))


def create_current_introduction_receiver(database, *, sources, settings):
    """Opt-in outer binding: #495 receives its own factual sources/settings.

    The caller owns the existing borrowed writer transaction. This function
    adds no eligibility callback, SQL, admission writer or automatic activation.
    """
    from app.services.queue_admission_adapter import current_owner_introductions
    return current_owner_introductions(database,sources=sources,settings=settings)


def accept_current_owner_rankings(*, day: str, results: tuple[PreparedAnalysis, ...],
                                 publications: AnalysisPublications, introductions) -> str:
    """Use #495's actual current context rather than invent its authority digest.

    Current-owner contexts bind retained authority fingerprints, which are the
    snapshot source_revision, rather than the snapshot's content digest. The
    Analysis adapter must hold its current publication/settings fence during
    this short delivery transaction; ordinary Study reads only its own cache.
    """
    if not results or any(item.status!="ready" or item.ranking is None for item in results):
        return "unavailable"
    fences=tuple(item.fence for item in results)
    if len({item.repertoire_id for item in fences})!=len(fences):
        raise ValueError("unique_nonempty_ranking_scopes_required")
    if any(publications.current(item.repertoire_id)!=item for item in fences):
        return "obsolete"
    context=introductions.context(day)
    expected=tuple(sorted((item.repertoire_id,item.repertoire_revision,item.source_revision) for item in fences))
    if context.repertoires!=expected:
        return "obsolete"
    for result in results:
        if (result.ranking.source_revision!=result.fence.source_revision
                or result.ranking.snapshot_digest!=result.fence.snapshot_digest):
            return "obsolete"
    facts=RankingInput(repertoire_revisions=tuple((name,revision) for name,revision,_ in expected),
        candidates=tuple(candidate for result in results for candidate in result.ranking.introductions.based_on.candidates),
        evidence_ids=tuple(sorted({identity for result in results
            for identity in result.ranking.introductions.based_on.evidence_ids})))
    return introductions.accept_ranking(day,context,rank_candidates(facts))


def create_proposal_coordinator(snapshot: RepertoireSnapshot, engine, *, cancellation=None):
    """Wire unchanged #496 retrieval and #497 evidence to #499's pure policy."""
    index = build_index(snapshot)

    class SimilarityAdapter:
        def retrieve(self, **request):
            return retrieve(index, RetrievalRequest(**request))

    class EngineAdapter:
        def evaluate(self, context, candidates):
            try:
                return engine.evaluate(context, candidates, cancellation=cancellation)
            except EngineBridgeUnavailable:
                raise CapabilityUnavailable("engine_unavailable") from None

    return ProposalCoordinator(SimilarityAdapter(), EngineAdapter())


def preview_repair(target, *, coordinator: ProposalCoordinator, changes):
    """Optional preparation may save an owner-validated preview; never accepts it."""
    result = coordinator.propose(target)
    receipt = changes.preview(result.proposal) if result.proposal is not None else None
    return result, receipt
