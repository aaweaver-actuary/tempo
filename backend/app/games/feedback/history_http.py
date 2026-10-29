"""Explicit local retained-history operations; no worker or work on reads.

Only the outer composition supplies operations. A preview returns an opaque
manifest digest, never an executable encounter command supplied by the browser.
Run-ready and resume both process the approved history queue, not one named run.
"""
from __future__ import annotations

import json
from typing import Protocol

from fastapi import APIRouter, HTTPException
from pydantic import Field, field_validator

from app.contracts.product_v02 import Digest, Identifier, Value
from .history import HistorySelection

PREFIX = "/api/product/game-history"


class HistoryUnavailable(RuntimeError):
    """A safe public operation failure; details contain no private source data."""

    def __init__(self, reason: str, status: int = 503):
        super().__init__(reason)
        self.reason, self.status = reason, status


class HistoryPreviewRequest(Value):
    run_id: Identifier
    selection: HistorySelection

    @field_validator("selection", mode="before")
    @classmethod
    def parse_selection(cls, value):
        # Immutable tuple contracts must use their JSON parser after FastAPI has
        # decoded the outer request. The shared contract still owns validation.
        return HistorySelection.model_validate_json(json.dumps(value)) if isinstance(value, dict) else value


class HistoryStartRequest(HistoryPreviewRequest):
    manifest_digest: Digest


class HistoryRunRequest(Value):
    maximum_units: int = Field(default=16, ge=1, le=100)


class HistoryOperations(Protocol):
    """The caller owns local authorization and the actual admission capability.

    start commits a server-prepared bounded manifest. run_ready resumes the
    existing ledger's approved history tasks only, yielding between slices.
    status returns the exact stored encounter receipts without replaying them.
    """

    def preview(self, *, selection: HistorySelection, run_id: str) -> dict: ...
    def start(self, *, selection: HistorySelection, run_id: str, manifest_digest: str) -> dict: ...
    def run_ready(self, *, maximum_units: int) -> dict: ...
    def status(self, *, run_id: str) -> dict | None: ...


def create_game_history_router(operations: HistoryOperations) -> APIRouter:
    """Construct routes only; construction performs no reads, writes or work."""
    router = APIRouter(prefix=PREFIX, tags=["game-history"])

    def invoke(operation, **arguments):
        try:
            return operation(**arguments)
        except HistoryUnavailable as error:
            raise HTTPException(error.status, error.reason) from None

    @router.post("/preview")
    def preview(request: HistoryPreviewRequest):
        return invoke(operations.preview, selection=request.selection, run_id=request.run_id)

    @router.post("/start", status_code=202)
    def start(request: HistoryStartRequest):
        return invoke(operations.start, selection=request.selection, run_id=request.run_id,
                      manifest_digest=request.manifest_digest)

    @router.post("/run-ready")
    def run_ready(request: HistoryRunRequest):
        return invoke(operations.run_ready, maximum_units=request.maximum_units)

    @router.post("/resume")
    def resume(request: HistoryRunRequest):
        """Resume bounded ready history work; never reset a saved cursor/key."""
        return invoke(operations.run_ready, maximum_units=request.maximum_units)

    @router.get("/runs/{run_id}")
    def status(run_id: Identifier):
        result = invoke(operations.status, run_id=run_id)
        if result is None:
            raise HTTPException(404, "retained_history_run_not_found")
        # No LearningReceipt response model: preserve actual EncounterReceipt
        # disposition/admission/deferral fields, including future additive facts.
        return result

    return router
