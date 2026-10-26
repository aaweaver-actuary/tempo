"""#501 private composition proofs; synthetic sources, no product acceptance claim."""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import chess
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in ("packages/game-review-contracts/src", "contracts/game-evidence/src",
             "packages/chess-core/src", "packages/evidence-math/src", "contracts/analysis-jobs/src"):
    sys.path.insert(0, str(ROOT / path))

from tempo_evidence_math import LearnerPolicy, LearnerPolicyAction, opening_position_key
from app.analysis.coverage.ports import AnalysisLimits
from app.analysis.jobs.application import AnalysisRunner
from app.analysis.jobs.ports import AnalysisFence
from app.analysis.probability.explorer import ExplorerClient
from app.contracts.product_v02.analysis import PopulationSelection, ProposalDecision
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.product_composition.analysis import CoverageRequest, coverage_settings_digest, prepare_coverage
from app.product_composition.repertoire import RepertoireCommands
from app.repertoire.changes.application import (
    RepertoireChanges, conflict_report, manual_resolution, study_eligibility,
)


def source(conflict=False):
    routes = [("twenty", ("e2e4", "e7e5")), ("two", ("d2d4", "d7d5"))]
    if conflict:
        routes.append(("conflict", ("e2e4", "c7c5")))
    mappings = []
    actions = {}
    for identity, moves in routes:
        board = chess.Board()
        board.push_uci(moves[0])
        key = opening_position_key(board.fen())
        actions.setdefault(key, moves[1])
        mappings.append(dict(decision_id=identity, repertoire_id="rep", position_key=key,
            selected_response_uci=moves[1], presentation_id="physical:"+identity,
            presentation_revision=1, repertoire_revision=1, introduction="unintroduced",
            introduced_at=None, available=True, introduction_matches_current_content=False))
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(dict(membership=dict(
        workspace_id="synthetic", snapshot_id="snapshot", revision=1, digest="a"*64,
        source_revision="source:1", captured_at="2026-10-26T12:00:00Z",
        basis="current_repertoire", scope_id="synthetic", scope_repertoire_ids=["rep"],
        complete=True, available=True, unavailable_reason=None, routes=[dict(
            repertoire_id="rep", route_id=identity, policy_id="policy:rep", learner_color="black",
            starting_fen=chess.STARTING_FEN, moves=list(moves), reentries=[]) for identity,moves in routes]),
        variant="standard", learner_color="black", boundaries=[dict(route_id=identity,
            terminal_ply=len(moves), deliberate=True) for identity,moves in routes],
        decision_presentations=mappings)))
    policy = LearnerPolicy("policy:rep", "rep", "black", tuple(
        LearnerPolicyAction("policy:rep", key, move) for key,move in sorted(actions.items())))
    return snapshot, policy


def request(*, population=True):
    snapshot, policy = source()
    settings = dict(population=PopulationSelection(rating_min=1600, rating_max=1799,
        time_controls=("rapid",)) if population else None, since="2025-01", until="2026-10",
        policy=policy, root_fen=chess.STARTING_FEN, approved_route_ids=("twenty", "two"),
        limits=AnalysisLimits(10, 10, 100), max_requests=2)
    fence = AnalysisFence(repertoire_id="rep", repertoire_revision=1, source_revision="source:1",
        snapshot_digest="a"*64, settings_revision=1, settings_digest=coverage_settings_digest(**settings),
        personal_revision=1, evidence_revision=1)
    return CoverageRequest(fence=fence, snapshot=snapshot, **settings)


class Publications:
    """Fake semantic owner; real persistent atomic fence remains a later checkpoint."""
    def __init__(self, fence):
        self.fence = fence
        self.accepted = None
        self.writes = 0
    def current(self, repertoire_id):
        assert repertoire_id == self.fence.repertoire_id
        return self.fence
    def publish_if_current(self, result):
        if result.fence != self.fence:
            return False
        if self.accepted != result:
            self.accepted = result
            self.writes += 1
        return True


def explorer(handler=None):
    def response(_):
        return httpx.Response(200, json=dict(white=100, draws=0, black=0, moves=[
            dict(uci="e2e4", white=20, draws=0, black=0),
            dict(uci="d2d4", white=2, draws=0, black=0)]))
    return ExplorerClient(transport_factory=lambda: httpx.MockTransport(handler or response))


def run(request, publications, provider=None):
    runner = AnalysisRunner(publications, timeout_seconds=2)
    return asyncio.run(runner.run_once(request.fence, lambda cancellation: prepare_coverage(request,
        explorer=provider or explorer(), credential="synthetic-token", cancellation=cancellation)))


def test_d_real_explorer_and_coverage_compose_without_study_or_queue_authority():
    job = request()
    store = Publications(job.fence)
    result = run(job, store)
    assert result.status == "published"
    ranking = store.accepted.ranking
    assert [item.decision_id for item in ranking.introductions.ordered] == ["twenty", "two"]
    values = {item.decision_id: item.reach_probability for item in ranking.introductions.based_on.candidates}
    assert values == {"twenty": .2, "two": .02}
    evidence = next(item for detail in ranking.details for item in detail.population_evidence)
    assert evidence.population == job.population
    assert evidence.distribution.unknown_mass == .78
    assert store.writes == 1


def test_d_setup_required_does_not_start_provider_or_replace_last_ranking():
    job = request(population=False)
    store = Publications(job.fence)
    marker = object()
    store.accepted = marker
    result = run(job, store, explorer(lambda _: pytest.fail("provider started before D04")))
    assert result.status == "setup_required"
    assert store.accepted is marker and store.writes == 0


@pytest.mark.parametrize("field,value", [("settings_revision",2), ("personal_revision",2),
    ("evidence_revision",2), ("source_revision","source:2"), ("snapshot_digest","b"*64)])
def test_d_inflight_context_change_does_not_publish(field, value):
    job = request()
    store = Publications(job.fence)
    def changed(_):
        store.fence = store.fence.model_copy(update={field:value})
        return httpx.Response(200, json=dict(white=100,draws=0,black=0,moves=[
            dict(uci="e2e4",white=20,draws=0,black=0),dict(uci="d2d4",white=2,draws=0,black=0)]))
    assert run(job, store, explorer(changed)).status == "obsolete"
    assert store.accepted is None and store.writes == 0


def test_d_provider_outage_retains_persisted_fallback():
    job = request()
    store = Publications(job.fence)
    assert run(job, store).status == "published"
    previous = store.accepted
    result = run(job, store, explorer(lambda _: httpx.Response(503)))
    assert result.status == "unavailable" and result.reason == "provider_unavailable"
    assert store.accepted is previous and store.writes == 1


def test_d_timeout_cancels_owned_work_and_leaves_publication_untouched():
    async def scenario():
        job = request()
        store = Publications(job.fence)
        cleaned = asyncio.Event()
        async def prepare(_):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        result = await AnalysisRunner(store, timeout_seconds=.01).run_once(job.fence, prepare)
        assert result.reason == "analysis_timeout" and cleaned.is_set()
        assert store.writes == 0
    asyncio.run(scenario())


def test_d_stop_and_concurrent_call_are_bounded_without_closing_borrowed_owner():
    async def scenario():
        job = request()
        store = Publications(job.fence)
        runner = AnalysisRunner(store, timeout_seconds=2)
        started = asyncio.Event()
        cleaned = asyncio.Event()
        async def prepare(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        task = asyncio.create_task(runner.run_once(job.fence, prepare))
        await started.wait()
        assert (await runner.run_once(job.fence, prepare)).status == "busy"
        runner.request_stop()
        assert (await task).status == "stopped" and cleaned.is_set()
        assert (await runner.run_once(job.fence, prepare)).status == "stopped"
        assert store.writes == 0
    asyncio.run(scenario())


class ChangesStore:
    """Task-only publication fake; production content/Study callbacks are not claimed."""
    def __init__(self, snapshot):
        self.current = snapshot
        self.previews = {}
        self.receipts = {}
        self.publications = []
    @contextmanager
    def transaction(self, repertoire_id):
        assert repertoire_id in {"rep", ""}
        yield self
    def snapshot(self): return self.current
    def preview(self, identity): return self.previews.get(identity)
    def save_preview(self, proposal): self.previews[proposal.proposal_id] = proposal
    def receipt(self, identity): return self.receipts.get(identity)
    def save_receipt(self, receipt): self.receipts[receipt.proposal_id] = receipt
    def publish(self, change):
        self.current = change.after
        self.publications.append(change)


def test_c_real_owner_manual_preview_approval_and_replay_preserve_unrelated_route():
    snapshot,_ = source(conflict=True)
    store = ChangesStore(snapshot)
    owner = RepertoireCommands(RepertoireChanges(store))
    conflict = conflict_report(snapshot).conflicts[0]
    proposal = manual_resolution(snapshot, repertoire_id="rep", position=conflict.position.position,
        selected_uci="e7e5", proposal_id="manual-choice")
    preview = owner.preview(proposal)
    assert preview.status == "preview" and not store.publications
    decision = ProposalDecision(proposal_id=proposal.proposal_id,
        expected_proposal_revision=proposal.proposal_revision,
        expected_source_revision=proposal.source_revision, display_digest=proposal.display_digest,
        action="accept", edited_proposal=None)
    receipt = owner.decide(decision)
    assert receipt.status == "accepted"
    assert owner.decide(decision) == receipt and len(store.publications) == 1
    assert not conflict_report(store.current).conflicts
    before = next(route for route in snapshot.membership.routes if route.route_id == "two")
    after = next(route for route in store.current.membership.routes if route.route_id == "two")
    assert after == before


def test_changed_product_boundary_reuses_contract_guard():
    from test_product_contracts import boundary_errors, has_import_cycle
    modules = {}
    sources = []
    for folder in ("backend/app/analysis/jobs",):
        for file in (ROOT/folder).glob("*.py"):
            module = ".".join(file.relative_to(ROOT/"backend").with_suffix("").parts)
            modules[module] = file.read_text()
            sources.append(("analysis", file.read_text(), module))
    assert not boundary_errors(sources) and not has_import_cycle(modules)
