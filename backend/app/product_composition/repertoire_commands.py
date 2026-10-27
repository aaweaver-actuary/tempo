"""C command transport composition; canonical owners supply all state effects."""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from pydantic import ValidationError

from app.contracts.product_v02.analysis import Proposal, ProposalDecision
from app.repertoire.changes.ports import ChangeError


def command_handlers(changes_for_transaction, *, audit_for_transaction=None):
    """Factories borrow the gateway's existing writer transaction.

    #492 supplies the current-source/approved publication factory. Study supplies
    the actual scoped block/suspension operation used by audit_for_transaction.
    No connection, commit, schema query or cross-owner write is made here.
    """
    def translate(operation):
        try:
            result = operation()
            return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        except ChangeError as error:
            raise HTTPException(409, str(error)) from error

    def preview(database, payload):
        try:
            proposal = Proposal.model_validate_json(json.dumps(payload["proposal"]))
        except (ValidationError, KeyError, TypeError) as error:
            raise HTTPException(422, "invalid_proposal") from error
        if payload["repertoire_id"] != proposal.repertoire_id:
            raise HTTPException(422, "repertoire_scope_mismatch")
        return translate(lambda: changes_for_transaction(database, proposal.repertoire_id).preview(proposal))

    def decide(database, payload):
        try:
            decision = ProposalDecision.model_validate_json(json.dumps(payload["decision"]))
        except (ValidationError, KeyError, TypeError) as error:
            raise HTTPException(422, "invalid_decision") from error
        return translate(lambda: changes_for_transaction(database, payload["repertoire_id"]).decide(decision))

    def audit(database, payload):
        if audit_for_transaction is None:
            raise HTTPException(503, "Current Study conflict enforcement is unavailable")
        return translate(lambda: audit_for_transaction(database, payload["repertoire_id"]))

    return {"product.repertoire.preview":preview, "product.repertoire.decide":decide,
            "product.repertoire.audit":audit}


def register_repertoire_commands(changes_for_transaction, *, audit_for_transaction):
    from app.command_gateway import register_command
    for name, handler in command_handlers(changes_for_transaction,
            audit_for_transaction=audit_for_transaction).items():
        register_command(name, handler)


class CurrentSnapshots:
    """Each read releases its owning read connection before returning a value."""
    def __init__(self, reader, current_snapshot):
        self.reader, self.current_snapshot = reader, current_snapshot

    def snapshot(self, repertoire_id):
        with self.reader() as database:
            return self.current_snapshot(database, repertoire_id)


class DispatchedChanges:
    """The existing durable foreground gateway owns retry/receipt/transactions."""
    def __init__(self, repertoire_id, dispatch):
        self.repertoire_id, self.dispatch = repertoire_id, dispatch

    def _command(self, name, value):
        payload = {"repertoire_id":self.repertoire_id, name:value.model_dump(mode="json")}
        command = "product.repertoire.preview" if name=="proposal" else "product.repertoire.decide"
        # Stable logical identity also survives a remount/lost HTTP acknowledgment.
        key = hashlib.sha256(json.dumps([command,payload], sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
        return self.dispatch(command, payload, idempotency_key="repertoire:"+key)

    def preview(self, proposal):
        return self._command("proposal", proposal)

    def decide(self, decision):
        return self._command("decision", decision)


def create_current_repertoire_http(*, reader, current_snapshot, dispatch, current_study_block):
    """Complete outer HTTP wiring; supply actual owning read capabilities.

    The command worker separately calls register_repertoire_commands with the
    actual #492 changes factory and current Study audit operation. No read or
    request startup creates a writer, worker, cache or background loop.
    """
    from app.product_composition.repertoire import create_repertoire_review_router

    snapshots = CurrentSnapshots(reader,current_snapshot)

    def block(repertoire_id,routes,**source_fence):
        with reader() as database:
            return current_study_block(database,repertoire_id,routes,**source_fence)

    return create_repertoire_review_router(
        lambda repertoire_id:DispatchedChanges(repertoire_id,dispatch), snapshots,
        study_block=block, audit=lambda repertoire_id,key:dispatch("product.repertoire.audit",
            {"repertoire_id":repertoire_id},idempotency_key=key),
    )
