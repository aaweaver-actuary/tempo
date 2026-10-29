"""Repertoire-owned retained import values and exact approval binding.

The original operation stays rejected. An accepted preview is delivered as a
new ordinary explicit-import command; its gateway receipt owns replay/rollback.
No connection, command registration or importer starts at this public boundary.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from app.contracts.product_v02 import Digest, Identifier, Revision, Value
from app.contracts.product_v02.analysis import DisplayedCardChange
from .ports import ChangeError


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class RetainedImport(Value):
    operation_id: str = Field(min_length=1, max_length=128)
    command_name: Literal["imports.pgn.admit", "imports.pgn.explicit"]
    request_hash: Digest
    original_payload_json: str = Field(min_length=1, max_length=32 * 1024 * 1024)
    original_error_json: str = Field(min_length=1)


class ImportPreview(Value):
    """A complete display, including original evidence and all proposed cards.

    A null target means a new authored repertoire; no physical repertoire ID is
    invented before admission. Card identities come from the existing importer.
    Editing produces a different digest and requires explicit acceptance again.
    """
    retained: RetainedImport
    target_repertoire_id: Identifier | None
    source_revision: Revision | None
    source_fence: Digest
    approved_payload_json: str = Field(min_length=1, max_length=32 * 1024 * 1024)
    card_changes: tuple[DisplayedCardChange, ...]
    excluded_deleted_card_ids: tuple[str, ...]
    display_digest: Digest
    publication_status: Literal["not_saved"] = "not_saved"

    def content_digest(self) -> str:
        return digest(self.model_dump(mode="json", exclude={"display_digest"}))

    def approval_payload(self, operation_id: str) -> dict:
        """Submit this whole display under one stable *new* idempotency key."""
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ChangeError("invalid_import_operation")
        if operation_id == self.retained.operation_id:
            raise ChangeError("resolution_requires_new_operation")
        if self.display_digest != self.content_digest():
            raise ChangeError("stale_or_unapproved_import_preview")
        payload = json.loads(self.approved_payload_json)
        payload["operation_id"] = operation_id
        payload["retained_import_preview"] = self.model_dump(mode="json")
        return payload


def verify_import_approval(preview: ImportPreview, current: ImportPreview, payload: dict) -> dict:
    """Reject changed evidence, source, displayed moves/cards or delivery body."""
    if preview.display_digest != preview.content_digest() or preview != current:
        raise ChangeError("stale_or_unapproved_import_preview")
    operation_id = payload.get("operation_id")
    expected = preview.approval_payload(operation_id)
    if canonical_json(payload) != canonical_json(expected):
        raise ChangeError("import_approval_payload_mismatch")
    result = json.loads(preview.approved_payload_json)
    result["operation_id"] = operation_id
    return result


def retained_imports(database):
    """Borrow the caller's database; no connection or transaction is opened."""
    from tempo_legacy_database.adapters.authored_repertoires.product_imports import RetainedImports
    return RetainedImports(database)


def _import_command(operation):
    from fastapi import HTTPException
    from pydantic import ValidationError
    try:
        return operation()
    except ChangeError as error:
        raise HTTPException(409, {"code": str(error), "message": str(error)}) from error
    except ValidationError as error:
        raise HTTPException(422, {"code": "invalid_import_approval"}) from error


def validate_approved_import(database, payload: dict) -> dict:
    """Fence and unwrap approval inside the existing decorated import command.

    The command retains its sole admission and publication-refresh invocation.
    This capability locks/reads only; it does not publish or settle a receipt.
    """
    return _import_command(lambda: retained_imports(database).validate(payload))


def apply_approved_import(database, payload: dict):
    """Validate then invoke the sole real importer on the borrowed transaction.

    For direct owner composition. Existing explicit-command wiring calls the
    validation capability instead so its publication wrapper executes once.
    """
    return _import_command(lambda: retained_imports(database).apply(payload))
