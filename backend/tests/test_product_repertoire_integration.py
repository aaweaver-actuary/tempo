"""C audit integration: real public owners with source-only synthetic fixtures."""
import sys
import json
from contextlib import contextmanager
from pathlib import Path

import chess
import pytest

ROOT=Path(__file__).resolve().parents[2]
for path in ("packages/game-review-contracts/src","contracts/game-evidence/src",
             "packages/chess-core/src","packages/evidence-math/src","contracts/analysis-jobs/src"):
    sys.path.insert(0,str(ROOT/path))

from tempo_evidence_math import LearnerPolicy,LearnerPolicyAction,opening_position_key
from app.contracts.product_v02.analysis import ProposalDecision
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.product_composition.repertoire import RepertoireCommands,create_repertoire_review_router
from app.product_composition.repertoire_commands import CurrentSnapshots,DispatchedChanges,command_handlers
from app.product_composition import repertoire_runtime
from app.repertoire.changes.application import RepertoireChanges,conflict_report,manual_resolution,study_eligibility


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
        return self.current


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


def test_c_command_transport_reuses_exact_transaction_and_stable_retry_identity():
    snapshot,_ = source(conflict=True)
    store = ChangesStore(snapshot)
    transaction = object()
    borrowed = []
    def factory(database, repertoire):
        borrowed.append((database,repertoire))
        return RepertoireChanges(store)
    handlers = command_handlers(factory)
    calls = []
    def dispatch(name,payload,*,idempotency_key):
        calls.append((name,payload,idempotency_key))
        return handlers[name](transaction,payload)
    port = DispatchedChanges("rep",dispatch)
    conflict = conflict_report(snapshot).conflicts[0]
    proposal = manual_resolution(snapshot,repertoire_id="rep",position=conflict.position.position,
        selected_uci="e7e5",proposal_id="transport-choice")
    assert port.preview(proposal)["status"] == "preview"
    assert port.preview(proposal)["status"] == "preview"
    assert calls[0] == calls[1] and borrowed == [(transaction,"rep")]*2
    assert not store.publications


def test_c_current_snapshot_read_releases_borrowed_reader_before_returning():
    snapshot,_ = source()
    events = []
    @contextmanager
    def reader():
        events.append("opened")
        try: yield "borrowed-reader"
        finally: events.append("closed")
    def current(database,repertoire):
        assert (database,repertoire)==("borrowed-reader","rep")
        return snapshot
    assert CurrentSnapshots(reader,current).snapshot("rep") == snapshot
    assert events == ["opened","closed"]


def test_c_runtime_refuses_publication_without_actual_study_reconciliation(monkeypatch):
    from fastapi import HTTPException
    snapshot,_=source(conflict=True)
    store=ChangesStore(snapshot)
    monkeypatch.setattr(repertoire_runtime,"retained_changes",lambda database,repertoire:RepertoireChanges(store))
    owner=repertoire_runtime.retained_changes_factory(after_publication=None)(object(),"rep")
    conflict=conflict_report(snapshot).conflicts[0]
    proposal=manual_resolution(snapshot,repertoire_id="rep",position=conflict.position.position,
        selected_uci="e7e5",proposal_id="unavailable-study")
    assert owner.preview(proposal).status=="preview"
    decision=ProposalDecision(proposal_id=proposal.proposal_id,expected_proposal_revision=proposal.proposal_revision,
        expected_source_revision=proposal.source_revision,display_digest=proposal.display_digest,
        action="accept",edited_proposal=None)
    with pytest.raises(HTTPException) as failure:
        owner.decide(decision)
    assert failure.value.status_code==503 and not store.publications
