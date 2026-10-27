"""D/E preparation proofs; synthetic sources, no product acceptance claim."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
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
from app.analysis.jobs.stockfish import EngineBridgeUnavailable, StockfishBridge
from app.analysis.probability.explorer import ExplorerClient
from app.contracts.product_v02.analysis import (CandidateEvaluation, EngineContext, EngineEvidence,
    PopulationSelection, ProposalDecision)
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.product_composition.analysis import (CoverageRequest, accept_published_introductions,
    coverage_settings_digest, create_proposal_coordinator, introduction_context,
    prepare_coverage, preview_repair)
from app.analysis.proposals.ports import ProposalTarget
from app.product_composition.repertoire import RepertoireCommands, create_repertoire_review_router
from app.product_composition.repertoire_commands import CurrentSnapshots, DispatchedChanges, command_handlers
from app.repertoire.changes.application import (
    RepertoireChanges, conflict_report, manual_resolution, study_eligibility,
)
from app.study.introductions.application import Introductions
from app.study.introductions.policy import Candidate
from app.study.introductions.ports import AllocationState, OwnerFacts


from test_product_repertoire_integration import source, ChangesStore
from app.product_composition.analysis import accept_current_owner_rankings
from app.product_composition.analysis import current_introduction_sources
from app.study.introductions.policy import RankingContext

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


class IntroductionFacts:
    def __init__(self, fence):
        self.current = OwnerFacts(introduction_context((fence,)), tuple(Candidate(
            identity, "physical:"+identity, "rep", "definition:1", order,
            approved=True, legal=True, conflicted=False, mapped=True, playable_context=True)
            for order,identity in enumerate(("two", "twenty"))), 1, (("rep",1.),), enabled=True)
        self.effects = []
        self.active_attempt = object()
    def facts(self, day): return self.current
    def introduce(self, day, candidate):
        self.effects.append(candidate.presentation_id)
        self.current = replace(self.current, introduced=self.current.introduced | {candidate.presentation_id})
        return "introduced"


class IntroductionLedger:
    def __init__(self):
        self.rankings = {}
        self.charges = {}
    def lock_allocation(self): pass
    def saved_ranking(self, context): return self.rankings.get(context)
    def save_ranking(self, context, ranking): self.rankings[context] = ranking
    def state(self, day): return AllocationState(charges=tuple(self.charges.values()))
    def receipt(self, identity): return self.charges.get(identity)
    def record_introduction(self, receipt, *, period_rotation_after): self.charges[receipt.presentation_id]=receipt


def test_d_published_rankings_use_actual_study_receiver_budget_and_idempotent_charge():
    job = request()
    publications = Publications(job.fence)
    assert run(job, publications).status == "published"
    owner, ledger = IntroductionFacts(job.fence), IntroductionLedger()
    receiver = Introductions(ledger, owner)
    attempt = owner.active_attempt
    assert accept_published_introductions(day="2026-10-26", results=(publications.accepted,),
        publications=publications, introductions=receiver) == "accepted"
    assert not owner.effects and not ledger.charges
    plan = receiver.preview("2026-10-26")
    assert [item.decision_id for item in plan.introductions] == ["twenty"]
    choice = plan.introductions[0]
    assert receiver.commit("2026-10-26", choice, owner.current.context).status == "introduced"
    assert receiver.commit("2026-10-26", choice, owner.current.context).status == "replayed"
    assert owner.effects == ["physical:twenty"] and len(ledger.charges) == 1
    assert owner.active_attempt is attempt


def test_d_study_rejects_a_context_change_after_analysis_fence_check():
    job = request()
    publications = Publications(job.fence)
    assert run(job, publications).status == "published"
    owner, ledger = IntroductionFacts(job.fence), IntroductionLedger()
    owner.current = replace(owner.current, context=introduction_context((
        job.fence.model_copy(update={"settings_revision":2}),)))
    assert accept_published_introductions(day="2026-10-26", results=(publications.accepted,),
        publications=publications, introductions=Introductions(ledger, owner)) == "rejected"
    assert not ledger.rankings and not owner.effects


def test_d_concrete_owner_handoff_uses_its_authority_context_without_recomputing_policy():
    job=request()
    publications=Publications(job.fence)
    assert run(job,publications).status=="published"
    owner,ledger=IntroductionFacts(job.fence),IntroductionLedger()
    actual=RankingContext((("rep",job.fence.repertoire_revision,job.fence.source_revision),),"actual-owner-controls-binding")
    owner.current=replace(owner.current,context=actual)
    class CurrentReceiver(Introductions):
        def context(self,day):return self.owner.facts(day).context
    receiver=CurrentReceiver(ledger,owner)
    assert accept_current_owner_rankings(day="2026-10-26",results=(publications.accepted,),
        publications=publications,introductions=receiver)=="accepted"
    assert tuple(ledger.rankings)==(actual,)
    assert receiver.preview("2026-10-26").introductions[0].decision_id=="twenty"
    owner.current=replace(owner.current,context=RankingContext((("rep",2,"changed-authority"),),actual.settings_revision))
    assert accept_current_owner_rankings(day="2026-10-26",results=(publications.accepted,),
        publications=publications,introductions=receiver)=="obsolete"


def test_d_game_free_source_uses_public_study_binding_without_a_capture_token():
    from app.study.introductions.application import repertoire_introduction_binding
    job=request()
    member=job.snapshot.membership.model_copy(update={
        'scope_id':'repertoire:rep','source_revision':job.snapshot.membership.digest})
    snapshot=job.snapshot.model_copy(update={'membership':member})
    job=replace(job,snapshot=snapshot,fence=job.fence.model_copy(update={'source_revision':member.source_revision}))
    class Snapshots:
        def snapshot(self,identity):
            assert identity=='rep'
            return snapshot
    sources=current_introduction_sources(Snapshots(),('rep',))
    assert sources[0].snapshot==snapshot and not hasattr(sources[0],'authority')
    publications=Publications(job.fence)
    assert run(job,publications).status=='published'
    owner,ledger=IntroductionFacts(job.fence),IntroductionLedger()
    actual=RankingContext((('rep',member.revision,repertoire_introduction_binding(snapshot)),),'actual-generic-owner')
    owner.current=replace(owner.current,context=actual)
    class Receiver(Introductions):
        def context(self,day):return self.owner.facts(day).context
    receiver=Receiver(ledger,owner)
    assert accept_current_owner_rankings(day='2026-10-26',results=(publications.accepted,),
        publications=publications,introductions=receiver,sources=sources)=='accepted'
    assert tuple(ledger.rankings)==(actual,)
    changed=snapshot.model_copy(update={'membership':member.model_copy(update={'revision':2}),
        'decision_presentations':tuple(item.model_copy(update={'repertoire_revision':2})
                                      for item in snapshot.decision_presentations)})
    class ChangedSnapshots:
        def snapshot(self,identity):return changed
    assert accept_current_owner_rankings(day='2026-10-26',results=(publications.accepted,),
        publications=publications,introductions=receiver,
        sources=current_introduction_sources(ChangedSnapshots(),('rep',)))=='obsolete'


def engine_fixture():
    return EngineEvidence.model_validate_json(json.dumps(json.loads(
        (ROOT/"contracts/product-v02/fixtures/engine-explicit-candidate.json").read_text())["payload"]))


def bridge(tmp_path, code, **kwargs):
    script = tmp_path/"engine-fixture.mjs"
    script.write_text(code)
    return StockfishBridge(node=shutil.which("node"), script=str(script),
                           executable=sys.executable, timeout_seconds=1, **kwargs)


def test_e_python_bridge_preserves_actual_protocol_evidence_and_correlation(tmp_path):
    evidence = engine_fixture()
    code = "import fs from 'node:fs'; const q=JSON.parse(fs.readFileSync(0,'utf8')); const e=" + evidence.model_dump_json() + "; e.evidence_id=q.request_id; console.log(JSON.stringify({request_id:q.request_id,status:'ok',evidence:e}));"
    result = bridge(tmp_path, code).evaluate(evidence.context, evidence.requested_candidates)
    assert result.context == evidence.context and result.candidates == evidence.candidates
    assert result.evidence_id.startswith("repair:")


def test_e_bridge_rejects_uncorrelated_response_without_interpreting_scores(tmp_path):
    evidence = engine_fixture()
    engine = bridge(tmp_path, "console.log(JSON.stringify({request_id:'wrong',status:'ok'}));")
    with pytest.raises(EngineBridgeUnavailable, match="engine_response_identity_mismatch"):
        engine.evaluate(evidence.context, evidence.requested_candidates)


def test_e_bridge_timeout_reaps_only_owned_process(tmp_path):
    evidence = engine_fixture()
    pid = tmp_path/"pid"
    engine = bridge(tmp_path, "import fs from 'node:fs'; fs.writeFileSync(" + json.dumps(str(pid)) + ",String(process.pid)); setInterval(()=>{},1000);")
    engine.timeout_seconds = .15
    with pytest.raises(EngineBridgeUnavailable, match="engine_timeout"):
        engine.evaluate(evidence.context, evidence.requested_candidates)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_e_real_similarity_and_proposal_policy_preview_through_approval_owner():
    snapshot,_ = source()
    raw = snapshot.model_dump(mode="json")
    raw["membership"]["routes"][0]["moves"] = ["e2e4"]
    raw["boundaries"][0]["terminal_ply"] = 1
    raw["decision_presentations"] = raw["decision_presentations"][1:]
    snapshot = RepertoireSnapshot.model_validate_json(json.dumps(raw))
    board = chess.Board()
    board.push_uci("e2e4")
    context = EngineContext(starting_fen=chess.STARTING_FEN, history_uci=("e2e4",),
        target_fen=board.fen(en_passant="fen"), variant="standard", learner_color="black", engine_version="synthetic-engine",
        max_depth=4, max_nodes=2000, max_time_ms=100, threads=1, hash_mb=16)
    class EvidenceEngine:
        def __init__(self): self.requests = []
        def evaluate(self, actual, candidates, *, cancellation=None):
            self.requests.append((actual,candidates))
            return EngineEvidence(evidence_id="synthetic-exact-target", context=actual,
                requested_candidates=candidates, best=CandidateEvaluation(move_uci="e7e5",
                    score={"kind":"centipawns","value":50}, depth=4, nodes=100, unavailable_reason=None),
                candidates=tuple(CandidateEvaluation(move_uci=move, score={"kind":"centipawns","value":20},
                    depth=4, nodes=100, unavailable_reason=None) for move in candidates), unavailable_reason=None)
    engine = EvidenceEngine()
    target = ProposalTarget("missing-reply", "rep", 1, snapshot.membership.digest, "twenty", context)
    store = ChangesStore(snapshot)
    result, receipt = preview_repair(target, coordinator=create_proposal_coordinator(snapshot, engine),
        changes=RepertoireCommands(RepertoireChanges(store)))
    assert result.status == "proposal" and receipt.status == "preview"
    assert result.proposal.max_loss_cp == 30 and result.selected_move_uci == "d7d5"
    assert engine.requests == [(context,("d7d5",))]
    assert result.proposal.card_changes[0].action == "add"
    assert not store.publications  # Engine evidence and preview never publish content.


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
