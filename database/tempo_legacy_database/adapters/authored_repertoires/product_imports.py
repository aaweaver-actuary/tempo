"""Retained import owner over existing receipt, snapshot and importer adapters.

No new tables, credentials, connections, commit, rollback, registration or Study
writes. Publication must run in the caller's explicit command transaction.
"""
from __future__ import annotations

import json

from app.chess.pgn import ParsedAnnotation, ParsedLine
from app.contracts.product_v02.analysis import DisplayedCardChange
from app.repertoire.changes.imports import (
    ImportPreview, RetainedImport, canonical_json, digest, verify_import_approval,
)
from app.repertoire.changes.ports import ChangeError
from .pgn_import import storage
from .product_snapshot import current_snapshot
from ..commands.receipts import storage as receipts


class RetainedImports:
    def __init__(self, database):
        self.database = database

    def read(self, operation_id: str) -> RetainedImport:
        from app.command_gateway import request_digest
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ChangeError("invalid_import_operation")
        row = receipts.operation_status(self.database, operation_id)
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
        cards = self.database.execute_native(
            "SELECT id,revision,start_fen,moves_json,trained_color,archived,superseded_by "
            "FROM cards WHERE id=ANY(%s::text[]) ORDER BY id", (ids,)).fetchall() if ids else []
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

    def apply(self, payload: dict):
        from psycopg.pq import TransactionStatus
        from app.pgn_import_commands import ImportPayload, explicit_pgn_import
        from app.repertoire.changes.retained import lock_retained_sources
        from .product_changes import lock_sources
        if self.database.raw.info.transaction_status != TransactionStatus.INTRANS:
            raise ChangeError("borrowed_transaction_required")
        preview = ImportPreview.model_validate_json(json.dumps(payload.get("retained_import_preview")))
        # Same operation lock as discard, then the import's existing target lock.
        receipts.lock_operation(self.database.raw, preview.retained.operation_id)
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
        validated = verify_import_approval(preview, current, payload)
        # The surrounding gateway savepoint rolls back importer writes and its
        # receipt together. Never update or clear the rejected source receipt.
        return explicit_pgn_import(self.database, validated)
