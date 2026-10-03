"""Foreground, receipt-backed decisions on imported-game findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import GameFindingCurationRequest, GameFindingDecisionRequest
from .postgres_store import PostgresConnection
from .services.real_game_feedback import prioritize_real_game_miss


def _locked_finding(database: PostgresConnection, finding_id: str):
    return database.execute_native(
        "SELECT f.*,g.adaptive_excluded FROM current_game_findings f "
        "JOIN imported_games g ON g.id=f.game_id WHERE f.id=%s FOR UPDATE OF f,g",
        (finding_id,),
    ).fetchone()


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
        database.execute(
            "UPDATE game_findings SET review_after=?,updated_at=? WHERE id=?",
            (review_after, now.isoformat(), finding_id),
        )
        return {"id": finding_id, "status": "pending", "review_after": review_after}
    database.execute(
        "UPDATE game_findings SET status='ignored',review_after=NULL,updated_at=? WHERE id=?",
        (now.isoformat(), finding_id),
    )
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
        linked_event = database.execute(
            """SELECT id FROM current_repertoire_decision_events repertoire_decision_events
               WHERE game_id=? AND repertoire_id=? AND ply=? AND card_id=? AND outcome='miss'""",
            (finding["game_id"], finding["repertoire_id"],
             finding["ply"], finding["card_id"]),
        ).fetchone()
        if linked_event is None:
            raise HTTPException(
                409, "Canonical game decision is unavailable. Reanalyze this game and try again.",
            )
        queued = prioritize_real_game_miss(database, linked_event["id"])
    database.execute(
        "UPDATE game_findings SET status=?,updated_at=? WHERE id=?",
        (request.decision, datetime.now(timezone.utc).isoformat(), finding_id),
    )
    return {"id": finding_id, "status": request.decision,
            "scheduling": None, "queued": queued}


register_command("game_findings.curate", curate_finding)
register_command("game_findings.decide", decide_finding)
