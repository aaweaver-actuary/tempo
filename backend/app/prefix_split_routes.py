"""HTTP transport for the retained manual prefix split commands."""

from fastapi import APIRouter, Header, HTTPException

from tempo_storage.connections import read_connection
from tempo_storage.foreground_controls import prefix_splits as storage

from .contracts.transport.content import PrefixSplitRequest, PrefixSplitResponse
from .services.prefix_split import preview_prefix_split

router = APIRouter()


@router.get("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_preview(identifier: str):
    with read_connection() as database:
        try:
            result = preview_prefix_split(database, identifier)
            usage = storage.published_usage(database, identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return {
        **result,
        "shared_line_count": max(1, int(usage["line_count"])),
        "shared_repertoire_count": max(1, int(usage["repertoire_count"])),
    }


@router.post("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_accept(
    identifier: str,
    request: PrefixSplitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.prefix_split.accept",
        {"card_id": identifier, "request": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )


@router.post("/api/cards/{identifier}/prefix-split/reject")
def prefix_split_reject(
    identifier: str,
    request: PrefixSplitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.prefix_split.reject",
        {"card_id": identifier, "request": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )
