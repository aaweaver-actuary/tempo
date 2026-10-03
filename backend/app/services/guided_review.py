"""Durable correction-first review sessions over actionable game findings."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import chess

from ..database import connection
from ..postgres_store import PostgresConnection


FINDING_PRIORITY = {
    "repertoire lapse": 5,
    "repertoire gap": 5,
    "tactical miss": 4,
    "blunder": 3,
    "major mistake": 2,
    "first big mistake": 1,
}


def _impact(finding) -> tuple[int, int, float]:
    evidence = json.loads(finding["evidence_json"])
    return (
        int(evidence.get("loss_cp", evidence.get("mistake_loss_cp", 0))),
        FINDING_PRIORITY.get(finding["kind"], 0),
        float(finding["confidence"]),
    )


def create_or_resume_session(game_id: str) -> dict:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        game = database.execute(
            "SELECT id,analysis_version FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            raise LookupError("Game not found")
        existing = database.execute(
            """SELECT id FROM guided_review_sessions
                WHERE game_id=? AND analysis_version=? AND status='active'
                ORDER BY updated_at DESC LIMIT 1""",
            (game_id, game["analysis_version"]),
        ).fetchone()
        if existing:
            return read_session_from_database(database, existing["id"])
        findings = database.execute(
            """SELECT * FROM current_game_findings game_findings WHERE game_id=?
                 AND analysis_version=? AND kind!='defensive tactical threat'
                 AND status NOT IN ('ignored','excluded') ORDER BY ply""",
            (game_id, game["analysis_version"]),
        ).fetchall()
        strongest_by_ply = {}
        for finding in findings:
            current = strongest_by_ply.get(finding["ply"])
            if current is None or _impact(finding) > _impact(current):
                strongest_by_ply[finding["ply"]] = finding
        ranked = sorted(strongest_by_ply.values(), key=_impact, reverse=True)
        finding_ids = [finding["id"] for finding in ranked[:5]]
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT INTO guided_review_sessions(
                   id,game_id,analysis_version,finding_ids_json,current_index,status,created_at,updated_at
               ) VALUES(?,?,?,?,0,?,?,?)""",
            (session_id, game_id, game["analysis_version"], json.dumps(finding_ids),
             "active" if finding_ids else "complete", now, now),
        )
    return read_session(session_id)


def _public_item(finding, *, reveal: bool) -> dict:
    evidence = json.loads(finding["evidence_json"])
    item = {
        "finding_id": finding["id"], "kind": finding["kind"],
        "ply": finding["ply"], "fen": evidence.get("fen"),
        "motif": finding["motif"], "confidence": finding["confidence"],
    }
    if reveal:
        item["answer"] = {
            "actual_move_uci": evidence.get("actual") or evidence.get("actual_move_uci"),
            "best_move_uci": evidence.get("best_move_uci"),
            "expected_moves": evidence.get("expected", []),
            "loss_cp": evidence.get("loss_cp", evidence.get("mistake_loss_cp")),
            "eval_before_cp": evidence.get("eval_before_cp"),
            "eval_after_cp": evidence.get("eval_after_cp"),
            "principal_variation": evidence.get("principal_variation", []),
        }
    return item


def read_session(session_id: str) -> dict:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        return read_session_from_database(database, session_id)


def reconcile_guided_review_session(database, session) -> dict:
    """Remove unpublished findings while retaining the completed surviving prefix."""
    reconciled = dict(session)
    original_ids = json.loads(session["finding_ids_json"])
    original_index = int(session["current_index"])
    current_findings = database.execute(
        "SELECT * FROM current_game_findings WHERE id IN ("
        + ",".join("?" for _ in original_ids) + ")",
        tuple(original_ids),
    ).fetchall() if original_ids else []
    findings_by_id = {finding["id"]: finding for finding in current_findings}
    retained_ids = [finding_id for finding_id in original_ids if finding_id in findings_by_id]
    completed_survivors = sum(finding_id in findings_by_id for finding_id in original_ids[:original_index])
    # Reuse this one publication snapshot for display and grading. A second
    # filtered lookup could compress the list again during scope invalidation.
    reconciled["current_findings"] = [findings_by_id[finding_id] for finding_id in retained_ids]
    status = "complete" if session["status"] == "complete" or completed_survivors >= len(retained_ids) else "active"
    if retained_ids != original_ids or completed_survivors != original_index or status != session["status"]:
        serialized_ids = json.dumps(retained_ids)
        updated_at = datetime.now(timezone.utc).isoformat()
        database.execute(
            "UPDATE guided_review_sessions SET finding_ids_json=?,current_index=?,status=?,updated_at=? WHERE id=?",
            (serialized_ids, completed_survivors, status, updated_at, session["id"]),
        )
        reconciled.update(finding_ids_json=serialized_ids, current_index=completed_survivors,
                          status=status, updated_at=updated_at)
    return reconciled


def guided_review_error(status_code: int, detail: str) -> dict:
    """Commit reconciliation before the HTTP boundary rejects an obsolete attempt."""
    return {"guided_review_error": {"status_code": status_code, "detail": detail}}


def read_session_from_database(database, session_id: str) -> dict:
    """Build the usual session response inside a command's uncommitted transaction."""

    session = database.execute(
        "SELECT * FROM guided_review_sessions WHERE id=?"
        + (" FOR UPDATE" if isinstance(database, PostgresConnection) else ""), (session_id,),
    ).fetchone()
    if not session:
        raise LookupError("Guided review not found")
    session = reconcile_guided_review_session(database, session)
    findings = session["current_findings"]
    current_index = int(session["current_index"])
    current = findings[current_index] if current_index < len(findings) else None
    attempts = database.execute(
        "SELECT finding_id,move_uci,correct,attempted_at FROM guided_review_attempts WHERE session_id=?",
        (session_id,),
    ).fetchall()
    return {
        "id": session["id"], "game_id": session["game_id"], "status": session["status"],
        "current_index": current_index, "total": len(findings),
        "current": _public_item(current, reveal=False) if current else None,
        "attempts": [dict(attempt) for attempt in attempts],
    }


def submit_attempt(session_id: str, move_uci: str, finding_id: str) -> dict:
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        session = database.execute(
            "SELECT * FROM guided_review_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not session:
            raise LookupError("Active guided review not found")
        session = reconcile_guided_review_session(database, session)
        if session["status"] != "active":
            return guided_review_error(404, "Guided review is complete")
        finding_ids = json.loads(session["finding_ids_json"])
        current_index = int(session["current_index"])
        if current_index >= len(finding_ids):
            raise LookupError("Guided review is complete")
        if finding_ids[current_index] != finding_id:
            return guided_review_error(409, "Guided review changed. Reload the session before trying again")
        finding = session["current_findings"][current_index]
        evidence = json.loads(finding["evidence_json"])
        fen = evidence.get("fen")
        try:
            board = chess.Board(fen)
            move = chess.Move.from_uci(move_uci)
        except (ValueError, TypeError) as error:
            raise ValueError("Correction position or move is invalid") from error
        if move not in board.legal_moves:
            raise ValueError("Correction move is not legal from this position")
        expected_moves = set(evidence.get("expected", []))
        if evidence.get("best_move_uci"):
            expected_moves.add(evidence["best_move_uci"])
        correct = move_uci in expected_moves
        now = datetime.now(timezone.utc).isoformat()
        database.execute(
            """INSERT OR IGNORE INTO guided_review_attempts(
                   session_id,finding_id,move_uci,correct,attempted_at
               ) VALUES(?,?,?,?,?)""",
            (session_id, finding["id"], move_uci, int(correct), now),
        )
        next_index = current_index + 1
        next_status = "complete" if next_index >= len(finding_ids) else "active"
        database.execute(
            "UPDATE guided_review_sessions SET current_index=?,status=?,updated_at=? WHERE id=?",
            (next_index, next_status, now, session_id),
        )
    return {
        "correct": correct, "revealed": _public_item(finding, reveal=True),
        "session": read_session(session_id),
    }
