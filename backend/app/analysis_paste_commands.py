"""Foreground PostgreSQL admission for pasted repertoire analysis."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import AnalysisPasteCommitRequest
from .postgres_store import PostgresConnection
from .services.analysis_paste import (
    PastedLine, PasteInputError, StalePastePreview, commit_pasted_lines,
)
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction


def commit_analysis_paste(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, Any]:
    request = AnalysisPasteCommitRequest.model_validate(payload["request"])
    prepared_preview = payload["prepared_preview"]
    parsed_lines = [
        PastedLine(str(line["starting_fen"]), tuple(line["moves"]), str(line["san"]))
        for line in prepared_preview["lines"]
    ]
    try:
        result = commit_pasted_lines(
            database, request.text, request.starting_fen, request.source_gap_id,
            request.preview_token,
            [selection.model_dump() for selection in request.selections],
            prepared_preview, parsed_lines,
        )
    except StalePastePreview as error:
        raise HTTPException(409, str(error)) from error
    except PasteInputError as error:
        raise HTTPException(422, str(error)) from error
    for repertoire_id in result["affected_repertoire_ids"]:
        invalidate_integrity_in_transaction(database, repertoire_id)
        request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat())
    return result


register_command("analysis.paste.commit", commit_analysis_paste)
