"""Canonical opening prefix reads and idempotent foreground saves."""

import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import postgres_store
from .command_gateway import register_command
from .database import connection, read_connection
from .services.canonical_prefix import parse_prefix, prefix_projection, read_prefix
from .services.canonical_prefix_preview import preview_projection, request_preview
from .services.durable_tasks import enqueue_task_in_transaction

router = APIRouter()


class CanonicalPrefixPreviewRequest(BaseModel):
    movetext: str = Field(max_length=5000)


class CanonicalPrefixSaveRequest(BaseModel):
    preview_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


def preview_command(database, payload: dict) -> dict:
    try:
        return request_preview(database, payload["repertoire_id"], payload["moves"])
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def save_prefix(database, payload: dict) -> dict:
    repertoire_id = payload["repertoire_id"]
    request = CanonicalPrefixSaveRequest.model_validate(payload["request"])
    if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)).fetchone():
        raise HTTPException(404, "Repertoire not found")
    current = read_prefix(database, repertoire_id, lock=True)
    preview = database.execute(
        "SELECT * FROM canonical_prefix_previews WHERE id=? AND repertoire_id=?",
        (request.preview_id, repertoire_id),
    ).fetchone()
    if (not preview or preview["state"] != "ready" or current["revision"] != request.expected_revision
            or preview["expected_revision"] != current["revision"]
            or preview["source_revision"] != current["source_revision"]):
        raise HTTPException(409, "Resolve conflicting lines and check the current prefix again before saving.")
    moves = json.loads(preview["moves_json"])
    if moves == current["moves"] and not moves:
        database.execute("UPDATE repertoires SET canonical_prefix_preview_id=? WHERE id=?",
                         (request.preview_id, repertoire_id))
        return prefix_projection(moves, current["revision"])
    revision = current["revision"] + int(moves != current["moves"])
    database.execute(
        "UPDATE repertoires SET canonical_prefix_moves_json=?,canonical_prefix_revision=?,canonical_prefix_preview_id=? WHERE id=?",
        (json.dumps(moves), revision, request.preview_id, repertoire_id),
    )
    if moves != current["moves"]:
        enqueue_task_in_transaction(database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90)
    enqueue_task_in_transaction(database, "repertoire_opportunity", repertoire_id,
                                {"repertoire_id": repertoire_id, "phase": "summaries", "cursor": ""}, priority=130)
    if not postgres_store.configured():
        database.execute("UPDATE repertoire_coverage_runs SET status='failed',last_error='Opening scope changed; refresh coverage' WHERE repertoire_id=? AND status IN ('queued','running')", (repertoire_id,))
    if postgres_store.configured():
        from .services.postgres_position_inventory import request_inventory_in_transaction
        from .services.postgres_coverage_seed import request_coverage_seed_in_transaction
        from .services.introduction_priorities import enqueue_priority_refresh_in_transaction
        database.execute("INSERT INTO priority_repertoire_source_epochs(repertoire_id,version) VALUES(?,1) "
                         "ON CONFLICT(repertoire_id) DO UPDATE SET version=priority_repertoire_source_epochs.version+1", (repertoire_id,))
        request_coverage_seed_in_transaction(database, repertoire_id, automatic=True, supersede_active=True)
        request_inventory_in_transaction(database, repertoire_id)
        database.execute("DELETE FROM repertoire_priority_publications WHERE repertoire_id=?", (repertoire_id,))
        database.execute("DELETE FROM repertoire_card_introduction_priorities WHERE repertoire_id=?", (repertoire_id,))
        enqueue_priority_refresh_in_transaction(database, repertoire_id)
    return prefix_projection(moves, revision)


register_command("repertoire.canonical_prefix.preview", preview_command)
register_command("repertoire.canonical_prefix.save", save_prefix)


@router.get("/api/repertoires/{identifier}/canonical-prefix")
def canonical_prefix_read(identifier: str):
    with read_connection() as database:
        if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (identifier,)).fetchone():
            raise HTTPException(404, "Repertoire not found")
        prefix = read_prefix(database, identifier)
    return prefix_projection(prefix["moves"], prefix["revision"])


@router.post("/api/repertoires/{identifier}/canonical-prefix/preview")
def canonical_prefix_preview(identifier: str, request: CanonicalPrefixPreviewRequest,
                             idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    try:
        moves = parse_prefix(request.movetext)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    payload = {"repertoire_id": identifier, "moves": moves}
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("repertoire.canonical_prefix.preview", payload, idempotency_key=idempotency_key)
    with connection() as database:
        result = preview_command(database, payload)
    from .main import coordinator
    coordinator.wake()
    return result


@router.get("/api/repertoires/{identifier}/canonical-prefix/preview/{preview_id}")
def canonical_prefix_preview_read(identifier: str, preview_id: str, after: str = ""):
    try:
        with read_connection() as database:
            return preview_projection(database, identifier, preview_id, after)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/api/repertoires/{identifier}/canonical-prefix")
def canonical_prefix_save(identifier: str, request: CanonicalPrefixSaveRequest,
                          idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    payload = {"repertoire_id": identifier, "request": request.model_dump()}
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command("repertoire.canonical_prefix.save", payload, idempotency_key=idempotency_key)
    with connection() as database:
        result = save_prefix(database, payload)
    from .services.repertoire_coverage import enqueue_coverage_refresh
    from .main import coordinator
    enqueue_coverage_refresh(identifier, automatic=True)
    coordinator.wake()
    return result
