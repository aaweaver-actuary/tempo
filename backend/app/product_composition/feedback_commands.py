"""Retained-product command wiring; all state writes stay in their owners."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re

from tempo_game_evidence_contracts.legacy import fingerprint
from tempo_legacy_database.adapters.games.authority import acceptance as source_owner
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    CurrentDecisionUnavailable, current_owner_game_feedback,
)

from app.contracts.product_v02 import Identifier, Value
from pydantic import Field
from app.legacy_game_observation import capture_game
from app.repertoire import project_capture
from .feedback import PreparedFeedback, prepare_game_feedback
from .retained_game import retained_canonical_game


class ImportedFeedbackSelection(Value):
    repertoire_id: Identifier
    game_id: Identifier
    occurrence_ply: int = Field(ge=1, le=1000)


@dataclass(frozen=True)
class PreparedImportedFeedback:
    capture: dict
    snapshot: object
    feedback: PreparedFeedback


def retained_time_precision(game) -> str:
    """Conservative projection of the retained import normalizer's source fact.

    Its date-only fallback is exactly UTC midnight; preserve that ambiguity.
    Non-midnight explicit HH:MM:SS cannot come from that fallback. Partial or
    absent time is never upgraded to precise chronology.
    """
    value = game.get("played_at", "")
    if not isinstance(value, str) or not re.search(r"T\d{2}:\d{2}:\d{2}", value):
        return "unknown"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            return "unknown"
        moment = moment.astimezone(timezone.utc)
        return "second" if (moment.hour, moment.minute, moment.second, moment.microsecond) != (0, 0, 0, 0) else "unknown"
    except ValueError:
        return "unknown"


def prepare_captured_feedback(capture, *, detected_at: str, occurrence_ply: int) -> PreparedImportedFeedback:
    """Compose one authentic owner capture, without reconstructing original PGN."""
    capture = json.loads(json.dumps(capture))
    raw = capture["game"]
    provider = {"lichess": "lichess", "chess.com": "chesscom", "chesscom": "chesscom"}.get(raw["provider"])
    if provider is None:
        raise ValueError("unsupported_retained_provider")
    game = retained_canonical_game(capture)
    snapshot = project_capture(capture, comparison_game=game)
    feedback = prepare_game_feedback(game, provider=provider, time_precision=retained_time_precision(raw),
        snapshot=snapshot, detected_at=detected_at, occurrence_ply=occurrence_ply)
    return PreparedImportedFeedback(capture, snapshot, feedback)


def prepare_imported_feedback(payload) -> PreparedImportedFeedback:
    selection = ImportedFeedbackSelection.model_validate(payload)
    captured = capture_game(selection.repertoire_id, selection.game_id, (selection.occurrence_ply-1,))
    return prepare_captured_feedback(captured, detected_at=captured["captured_at"],
                                     occurrence_ply=selection.occurrence_ply)


def receive_imported_feedback(database, prepared: PreparedImportedFeedback, *, clock=None):
    """Fence canonical Games facts first, then borrow Study's atomic receiver.

    Existing owner adapters own every SQL statement/lock. Prepared data is never
    a write grant. The worker command transaction commits the returned receipt.
    """
    raw = prepared.capture["game"]
    source_owner.lock_imported_source(database, raw["provider"], raw["provider_game_id"])
    current = source_owner.imported_game(database, raw["id"])
    lapse = prepared.feedback.lapse
    if (current is None or dict(current) != raw or lapse.finding.source_digest != fingerprint(raw)
            or lapse.key.game_id != raw["id"]):
        raise CurrentDecisionUnavailable("canonical_imported_source_changed")
    owner = current_owner_game_feedback(database, retained_owner_capture=prepared.capture,
        snapshot=prepared.snapshot, clock=clock or (lambda: datetime.now(timezone.utc)))
    return owner.accept_lapse(lapse).model_dump(mode="json")


def register_commands():
    from app.command_gateway import register_command
    register_command("game_evidence.product_accept", _receive_command,
                     prepare=_prepare_command)


def _prepare_command(payload):
    from fastapi import HTTPException
    from tempo_learning_remediation import RemediationError
    try:
        return prepare_imported_feedback(payload)
    except (ValueError, RemediationError) as error:
        reason = str(error) if isinstance(error, RemediationError) else "one_current_confirmed_miss_required"
        raise HTTPException(409, reason) from error


def _receive_command(database, prepared):
    from fastapi import HTTPException
    try:
        return receive_imported_feedback(database, prepared)
    except CurrentDecisionUnavailable as error:
        raise HTTPException(409, error.reason) from error
