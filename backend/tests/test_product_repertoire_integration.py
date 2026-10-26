"""#501 private composition proofs; synthetic sources, no product acceptance claim."""
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
from app.repertoire.changes.application import (
    RepertoireChanges, conflict_report, manual_resolution, study_eligibility,
)
from app.study.introductions.application import Introductions
from app.study.introductions.policy import Candidate
from app.study.introductions.ports import AllocationState, OwnerFacts


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


def test_c_conflict_eligibility_is_route_local_and_stale_decision_rejected():
    snapshot,_ = source(conflict=True)
    store = ChangesStore(snapshot)
    commands = RepertoireCommands(RepertoireChanges(store))
    fence = dict(source_revision=snapshot.membership.source_revision,
                 revision=snapshot.membership.revision, digest=snapshot.membership.digest)
    affected = study_eligibility(snapshot, repertoire_id="rep", route_id="twenty", ply=1, **fence)
    unrelated = study_eligibility(snapshot, repertoire_id="rep", route_id="two", ply=1, **fence)
    assert not affected.eligible and affected.suspend_without_lapse
    assert unrelated.eligible and not unrelated.suspend_without_lapse
    conflict = conflict_report(snapshot).conflicts[0]
    proposal = manual_resolution(snapshot, repertoire_id="rep", position=conflict.position.position,
        selected_uci="e7e5", proposal_id="stale-choice")
    commands.preview(proposal)
    decision = ProposalDecision(proposal_id=proposal.proposal_id,
        expected_proposal_revision=2, expected_source_revision=proposal.source_revision,
        display_digest=proposal.display_digest, action="accept", edited_proposal=None)
    with pytest.raises(ValueError, match="stale_or_unapproved_preview"):
        commands.decide(decision)
    assert not store.publications


def test_c_unregistered_router_uses_real_owner_http_and_exact_approval(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    snapshot,_ = source(conflict=True)
    store = ChangesStore(snapshot)
    class Snapshots:
        def snapshot(self, identity):
            assert identity == "rep"
            return store.current
    application = FastAPI()
    application.include_router(create_repertoire_review_router(lambda _: RepertoireChanges(store), Snapshots()))
    with TestClient(application) as client:
        projection = client.get("/api/product/repertoires/rep/review")
        assert projection.status_code == 200
        display = projection.json()
        assert display["conflicts"][0]["study_block"]["status"] == "unknown"
        assert len(display["conflicts"][0]["choices"]) == 2
        assert len(display["conflicts"][0]["position"]["target_fen"].split()) == 6
        assert not store.publications and not store.previews
        report = client.get("/api/product/repertoires/rep/conflicts")
        assert report.status_code == 200 and len(report.json()["conflicts"]) == 1
        assert not store.publications
        conflict = conflict_report(snapshot).conflicts[0]
        proposal = manual_resolution(snapshot, repertoire_id="rep", position=conflict.position.position,
            selected_uci="e7e5", proposal_id="http-choice")
        preview = client.post("/api/product/repertoires/rep/changes/preview", json=proposal.model_dump(mode="json"))
        assert preview.status_code == 200 and preview.json()["status"] == "preview"
        decision = dict(proposal_id=proposal.proposal_id, expected_proposal_revision=2,
            expected_source_revision=proposal.source_revision, display_digest=proposal.display_digest,
            action="accept", edited_proposal=None)
        stale = client.post("/api/product/repertoires/rep/changes/decide", json=decision)
        assert stale.status_code == 409 and not store.publications
        decision["expected_proposal_revision"] = 1
        accepted = client.post("/api/product/repertoires/rep/changes/decide", json=decision)
        assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
        assert len(store.publications) == 1


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
