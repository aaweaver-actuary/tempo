"""Proposal commands go unchanged to the single approved-change owner."""
import hashlib
import json

from fastapi import APIRouter, Header, HTTPException
from tempo_evidence_math import opening_position_key

from app.contracts.product_v02.analysis import (
    Proposal, ProposalDecision, RepertoireChangePort, RepertoireChangeReceipt,
)
from app.contracts.product_v02.repertoire import RepertoireSnapshotPort
from app.repertoire.changes.http import routes
from app.repertoire.changes.application import conflict_report, manual_resolution
from app.repertoire.changes.ports import ChangeError
from app.repertoire import build_index


def create_repertoire_router(changes, snapshots: RepertoireSnapshotPort):
    """Root owner mounts this router during its exact-file reservation."""
    return routes(changes, snapshots)


def audit_projection(snapshot, *, study_block=None, card_definitions=()):
    """Adapt #492 facts/alternatives to #500's read-only display projection.

    A detector result is never evidence that Study actually suspended a target.
    Only an injected owning read capability may report a persisted block.
    """
    report = conflict_report(snapshot)
    if not report.available or not report.source_complete:
        raise ChangeError("repertoire_source_unavailable")
    if any(item.code != "own_response_conflict" for item in report.diagnostics):
        raise ChangeError("repertoire_source_requires_repair")
    if len(report.conflicts) > 8 or any(len(item.responses_uci) > 8 for item in report.conflicts):
        raise ChangeError("audit_display_scope_exceeds_bound")
    source = snapshot.membership
    by_route = {route.route_id:route for route in source.routes}
    index = build_index(snapshot)

    def position(route, history, target):
        return dict(starting_fen=route.starting_fen, history_uci=list(history), target_fen=target,
                    variant=snapshot.variant, learner_color=route.learner_color,
                    position_key=opening_position_key(target))

    conflicts = []
    for conflict in report.conflicts:
        first = conflict.occurrences[0]
        route = by_route[first.route_id]
        key = conflict.position.position
        identity = hashlib.sha256(json.dumps([conflict.position.repertoire_id, key,
            source.revision, source.digest], separators=(",", ":")).encode()).hexdigest()
        choices = [dict(move_uci=move, proposal=manual_resolution(snapshot,
            repertoire_id=conflict.position.repertoire_id, position=key, selected_uci=move,
            proposal_id="resolve:"+hashlib.sha256((identity+move).encode()).hexdigest(),
            card_definitions=card_definitions).model_dump(mode="json"))
            for move in conflict.responses_uci]
        affected = tuple(sorted({item.route_id for item in conflict.occurrences}))
        block = study_block(conflict.position.repertoire_id, affected,
            source_revision=source.source_revision, revision=source.revision,
            digest=source.digest) if study_block else None
        # Callback returns actual owning facts; unknown is a safe default.
        status = "blocked" if block == "blocked" else "unknown"
        conflicts.append(dict(conflict_id=identity, repertoire_id=conflict.position.repertoire_id,
            position=position(route, route.moves[:first.ply], index.routes[
                (first.repertoire_id,first.route_id)][first.ply].full_fen), affected_routes=affected,
            study_block=dict(status=status, reason="own_response_conflict" if status=="blocked"
                             else "study_block_unverified"), choices=choices))
    # These are bounded line previews, not a second complete route inventory.
    lines = [dict(route_id=route.route_id, label=route.route_id,
        position=position(route, (), route.starting_fen)) for route in source.routes[:32]]
    if len(source.scope_repertoire_ids) != 1:
        raise ChangeError("audit_requires_single_repertoire_scope")
    return dict(repertoire_id=source.scope_repertoire_ids[0], source_revision=source.revision,
        source_digest=source.digest, lines=lines, conflicts=conflicts, findings=[])


def create_repertoire_review_router(changes, snapshots: RepertoireSnapshotPort, *, study_block=None,
                                    proposals=lambda _: (), evidence=lambda _rep, _position: (), audit=None,
                                    review_source=None):
    """Unregistered C router; optional E reads are injected, never called by Study."""
    router = APIRouter()
    router.include_router(create_repertoire_router(changes, snapshots))

    @router.get("/api/product/repertoires/{repertoire_id}/review")
    def review(repertoire_id: str):
        try:
            snapshot,definitions=(review_source(repertoire_id) if review_source else
                                  (snapshots.snapshot(repertoire_id),()))
            return audit_projection(snapshot,study_block=study_block,card_definitions=definitions)
        except ChangeError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/api/product/repertoires/{repertoire_id}/proposals")
    def pending(repertoire_id: str):
        return proposals(repertoire_id)

    @router.get("/api/product/repertoires/{repertoire_id}/evidence")
    def probability(repertoire_id: str, position_key: str):
        return evidence(repertoire_id, position_key)

    @router.post("/api/product/repertoires/{repertoire_id}/audit")
    def enforce(repertoire_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        if audit is None:
            raise HTTPException(503, "Current Study conflict enforcement is unavailable")
        return audit(repertoire_id, idempotency_key)

    return router


class RepertoireCommands:
    """Shared C/E approval entry; no content, Study or engine policy lives here."""
    def __init__(self, changes: RepertoireChangePort):
        self.changes = changes

    def preview(self, proposal: Proposal) -> RepertoireChangeReceipt:
        return self.changes.preview(proposal)

    def decide(self, decision: ProposalDecision) -> RepertoireChangeReceipt:
        # The owner binds source/display revisions, reevaluates edits, validates
        # one response, and atomically applies displayed content/card additions.
        return self.changes.decide(decision)
