"""Injectable HTTP translation; integration owner registers the router.

No app startup, store construction, engine or database access at import time.
"""
from collections.abc import Callable
from dataclasses import asdict
import json

from pydantic import ValidationError

from fastapi import APIRouter, HTTPException

from app.contracts.product_v02.analysis import Proposal, ProposalDecision, RepertoireChangePort
from app.contracts.product_v02.repertoire import RepertoireSnapshotPort
from .application import conflict_report
from .ports import ChangeError


def routes(changes: Callable[[str], RepertoireChangePort], snapshots: RepertoireSnapshotPort) -> APIRouter:
    router = APIRouter(prefix="/api/product/repertoires", tags=["repertoire-changes"])

    def invoke(operation):
        try:
            return operation()
        except ChangeError as error:
            raise HTTPException(409, str(error)) from error

    @router.get("/{repertoire_id}/conflicts")
    def conflicts(repertoire_id: str):
        def report():
            value = conflict_report(snapshots.snapshot(repertoire_id))
            return asdict(value) | {"clean": value.clean}
        return invoke(report)

    @router.post("/{repertoire_id}/changes/preview")
    def preview(repertoire_id: str, payload: dict):
        try:
            proposal = Proposal.model_validate_json(json.dumps(payload))
        except ValidationError as error:
            raise HTTPException(422, "invalid_proposal") from error
        if proposal.repertoire_id != repertoire_id:
            raise HTTPException(422, "repertoire_scope_mismatch")
        return invoke(lambda: changes(repertoire_id).preview(proposal))

    @router.post("/{repertoire_id}/changes/decide")
    def decide(repertoire_id: str, payload: dict):
        try:
            decision = ProposalDecision.model_validate_json(json.dumps(payload))
        except ValidationError as error:
            raise HTTPException(422, "invalid_decision") from error
        return invoke(lambda: changes(repertoire_id).decide(decision))

    return router
