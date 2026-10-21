"""Foreground, receipt-backed decisions on imported-game findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import GameFindingCurationRequest, GameFindingDecisionRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games import findings as storage
from .services.real_game_feedback_adapter import prioritize_real_game_miss


_locked_finding = storage.locked_finding


def curate_finding(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    finding_id = str(payload["finding_id"])
    request = GameFindingCurationRequest.model_validate(payload["request"])
    finding = _locked_finding(database, finding_id)
    if finding is None or finding["kind"] != "tactical miss":
        raise HTTPException(404, "Pending tactical miss not found")
    if finding["adaptive_excluded"]:
        raise HTTPException(409, "This game is excluded from adaptation")
    now = datetime.now(timezone.utc)
    if request.action == "skip":
        review_after = (now + timedelta(days=1)).isoformat()
        storage.defer_finding(database, finding_id, review_after, now.isoformat())
        return {"id": finding_id, "status": "pending", "review_after": review_after}
    storage.ignore_finding(database, finding_id, now.isoformat())
    return {"id": finding_id, "status": "ignored"}


def decide_finding(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    finding_id = str(payload["finding_id"])
    request = GameFindingDecisionRequest.model_validate(payload["request"])
    finding = _locked_finding(database, finding_id)
    if finding is None:
        raise HTTPException(404, "Gameplay finding not found")
    if request.decision == "accepted" and finding["adaptive_excluded"]:
        raise HTTPException(409, "This game is excluded from adaptation")
    queued = False
    if request.decision == "accepted" and finding["kind"] == "repertoire lapse":
        if not finding["card_id"]:
            raise HTTPException(422, "This repertoire lapse is not linked to a study card")
        linked_event = storage.linked_miss_event(database, finding)
        if linked_event is None:
            raise HTTPException(
                409, "Canonical game decision is unavailable. Reanalyze this game and try again.",
            )
        queued = prioritize_real_game_miss(database, linked_event["id"])
    storage.set_finding_decision(database, finding_id, request.decision, datetime.now(timezone.utc).isoformat())
    return {"id": finding_id, "status": request.decision,
            "scheduling": None, "queued": queued}


register_command("game_findings.curate", curate_finding)
register_command("game_findings.decide", decide_finding)
