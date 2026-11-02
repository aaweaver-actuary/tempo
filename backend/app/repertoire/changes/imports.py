"""Repertoire-owned retained import values and exact approval binding.

The original operation stays rejected. An accepted preview is delivered as a
new ordinary explicit-import command; its gateway receipt owns replay/rollback.
No connection, command registration or importer starts at this public boundary.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from app.contracts.product_v02 import Digest, Identifier, Revision, Value
from app.contracts.product_v02.analysis import DisplayedCardChange
from .ports import ChangeError

if TYPE_CHECKING:
    from app.chess.pgn import ParsedLine
    from tempo_legacy_database.adapters.authored_repertoires.product_imports import RetainedImportStorage


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


class RetainedImportApplication:
    """Retained evidence and approval policy over the caller's borrowed database."""

    def __init__(self, database, raw: RetainedImportStorage):
        self.database = database
        self.raw = raw

    def read(self, operation_id: str) -> RetainedImport:
        from app.contracts.command_identity import request_digest
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ChangeError("invalid_import_operation")
        row = self.raw.read_operation(operation_id)
        if row is None:
            raise ChangeError("import_operation_missing")
        command, request_hash, state, error_json = row[0], row[1], row[2], row[4]
        payload_json = row[9]
        if command not in {"imports.pgn.admit", "imports.pgn.explicit"} or state != "failed" or not payload_json:
            raise ChangeError("retained_conflicted_import_required")
        if len(payload_json.encode()) > 32 * 1024 * 1024:
            raise ChangeError("source_exceeds_bound")
        payload = json.loads(payload_json)
        error = json.loads(error_json)
        if (error.get("status_code") != 409 or not isinstance(error.get("detail"), dict)
                or error["detail"].get("code") != "repertoire_conflict"
                or payload.get("retained_import_preview") is not None):
            raise ChangeError("retained_conflicted_import_required")
        if request_hash != request_digest(command, payload):
            raise ChangeError("retained_import_evidence_changed")
        return RetainedImport(operation_id=operation_id, command_name=command,
            request_hash=request_hash, original_payload_json=payload_json, original_error_json=error_json)

    def preview(self, operation_id: str, lines: tuple[ParsedLine, ...]) -> ImportPreview:
        from app.pgn_import_commands import ImportPayload, prepare_import_payload
        from app.pgn_import_validation import validate_sources
        from app.services.opening_graph import decision_segments
        from tempo_legacy_database.adapters.authored_repertoires.pgn_import import storage
        from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import current_snapshot
        retained = self.read(operation_id)
        original = ImportPayload.model_validate_json(retained.original_payload_json)
        if (len(lines) > 10000 or any(len(line.moves) > 1000 for line in lines)
                or sum(len(line.moves) for line in lines) > 100000):
            raise ChangeError("source_exceeds_bound")
        # Recompute derived segment/count identities using the sole importer.
        prepared = prepare_import_payload(original.source_name, original.trained_color,
            original.depth, original.games_found, list(lines))
        payload = ImportPayload.model_validate(prepared)
        if not payload.lines:
            raise ChangeError("resolved_import_requires_lines")
        match = storage.matching_repertoire(self.database, payload.source_name, payload.trained_color)
        repertoire_id = match[0] if match else None
        source = current_snapshot(self.database, repertoire_id) if repertoire_id else None
        proposed = [dict(id=f"import:{number}", start_fen=line.starting_fen,
            moves_json=json.dumps(line.moves), trained_color=payload.trained_color)
            for number, line in enumerate(payload.lines)]
        current_sources = list(storage.current_sources(self.database, repertoire_id, payload.trained_color)) if repertoire_id else []
        validate_sources(repertoire_id or "pending-import", [dict(row) for row in current_sources] + proposed)
        ids = payload.segment_ids
        cards = self.raw.card_rows(ids)
        deleted = tuple(sorted(row[0] for row in storage.deleted_card_ids(self.database, ids))) if ids else ()
        existing = {row["id"]: row for row in cards}
        segments = {segment.card_id: segment for line in lines for segment in decision_segments(
            line.starting_fen, line.moves, payload.trained_color, payload.depth)}
        changes = []
        for identifier, segment in sorted(segments.items()):
            if identifier in deleted:
                continue
            card = existing.get(identifier)
            changes.append(DisplayedCardChange(action="reuse" if card else "add",
                presentation_id=identifier, expected_revision=int(card["revision"] or 1) if card else None,
                starting_fen=segment.starting_fen, moves=tuple(segment.moves), learner_color=payload.trained_color))
        fence = digest(dict(target_repertoire_id=repertoire_id,
            source_digest=source.membership.digest if source else None,
            source_revision=source.membership.source_revision if source else None,
            cards=[dict(card) for card in cards], deleted=deleted))
        preview = ImportPreview(retained=retained, target_repertoire_id=repertoire_id,
            source_revision=source.membership.revision if source else None, source_fence=fence,
            approved_payload_json=canonical_json(prepared), card_changes=tuple(changes),
            excluded_deleted_card_ids=deleted, display_digest="0" * 64)
        return preview.model_copy(update={"display_digest": preview.content_digest()})

    def validate(self, payload: dict) -> dict:
        """Fence the approved display without invoking any content writer."""
        from app.chess.pgn import ParsedAnnotation, ParsedLine
        from app.pgn_import_commands import ImportPayload
        from app.repertoire.changes.retained import lock_retained_sources
        from tempo_legacy_database.adapters.authored_repertoires.pgn_import import storage
        from tempo_legacy_database.adapters.authored_repertoires.product_changes import lock_sources
        if not self.raw.in_transaction():
            raise ChangeError("borrowed_transaction_required")
        preview = ImportPreview.model_validate_json(json.dumps(payload.get("retained_import_preview")))
        # Same operation lock as discard, then the import's existing target lock.
        self.raw.lock_original(preview.retained.operation_id)
        storage.lock_main_repertoire(self.database)
        approved = ImportPayload.model_validate_json(preview.approved_payload_json)
        match = storage.matching_repertoire(self.database, approved.source_name, approved.trained_color)
        if (match[0] if match else None) != preview.target_repertoire_id:
            raise ChangeError("stale_import_target")
        if match:
            lock_retained_sources(self.database, match[0])
            lock_sources(self.database, match[0])
        lines = tuple(ParsedLine(line.starting_fen, line.moves,
            [ParsedAnnotation(note.fen_key, note.comment, note.arrows, note.squares) for note in line.annotations])
            for line in approved.lines)
        current = self.preview(preview.retained.operation_id, lines)
        return verify_import_approval(preview, current, payload)

    def apply(self, payload: dict):
        from app.pgn_import_commands import explicit_pgn_import
        validated = self.validate(payload)
        # The surrounding gateway savepoint rolls back importer writes and its
        # receipt together. Never update or clear the rejected source receipt.
        return explicit_pgn_import(self.database, validated)


def retained_imports(database) -> RetainedImportApplication:
    """Borrow the caller's database; no connection or transaction is opened."""
    from tempo_legacy_database.adapters.authored_repertoires.product_imports import RetainedImportStorage
    return RetainedImportApplication(database, RetainedImportStorage(database))


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
