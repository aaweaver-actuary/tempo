"""Canonical opening prefix reads and idempotent foreground saves."""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires import (
    prefix_commands as storage,
)
from .contracts.transport.authored_repertoire import CanonicalPrefixSaveRequest
from tempo_legacy_database.connections import connection, read_connection
from .services.canonical_prefix import (
    parse_prefix,
    prefix_projection,
    read_prefix,
    preview_command,
    save_prefix,
)
from .services.canonical_prefix_preview import preview_projection


class CanonicalPrefixPreviewRequest(BaseModel):
    movetext: str = Field(max_length=5000)


def create_canonical_prefix_router(wake: Callable[[], None]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/repertoires/{identifier}/canonical-prefix")
    def canonical_prefix_read(identifier: str):
        with read_connection() as database:
            if not storage.repertoire_presence(database, identifier):
                raise HTTPException(404, "Repertoire not found")
            prefix = read_prefix(database, identifier)
        return prefix_projection(prefix["moves"], prefix["revision"])

    @router.post("/api/repertoires/{identifier}/canonical-prefix/preview")
    def canonical_prefix_preview(
        identifier: str,
        request: CanonicalPrefixPreviewRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        try:
            moves = parse_prefix(request.movetext)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        payload = {"repertoire_id": identifier, "moves": moves}
        if storage.postgres_is_configured():
            from .command_dispatch import dispatch_command

            return dispatch_command(
                "repertoire.canonical_prefix.preview",
                payload,
                idempotency_key=idempotency_key,
            )
        with connection() as database:
            result = preview_command(database, payload)
        wake()
        return result

    @router.get("/api/repertoires/{identifier}/canonical-prefix/preview/{preview_id}")
    def canonical_prefix_preview_read(
        identifier: str, preview_id: str, after: str = ""
    ):
        try:
            with read_connection() as database:
                return preview_projection(database, identifier, preview_id, after)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.put("/api/repertoires/{identifier}/canonical-prefix")
    def canonical_prefix_save(
        identifier: str,
        request: CanonicalPrefixSaveRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        payload = {"repertoire_id": identifier, "request": request.model_dump()}
        if storage.postgres_is_configured():
            from .command_dispatch import dispatch_command

            return dispatch_command(
                "repertoire.canonical_prefix.save",
                payload,
                idempotency_key=idempotency_key,
            )
        with connection() as database:
            result = save_prefix(database, payload)
        wake()
        return result

    return router
