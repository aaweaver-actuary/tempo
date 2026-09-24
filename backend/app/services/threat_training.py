"""Manual admission and idempotent study of defensive-move rubrics."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json

import chess

from ..database import connection, read_connection
from .review_service import (
    apply_scheduling_review, ensure_card_queued_after, preserve_daily_queue_order,
)
from .threat_grading import DefenseExercise, grade_defense_move
from .threat_pipeline import _anchor_from_json, _seed_from_json, report_from_json
from .threat_validation import AnalysisReport
from .threat_models import ThreatPolicy


DEFENSE_REPERTOIRE_ID = "__defense__"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate(database, candidate_id: str):
    return database.execute(
        """SELECT c.*,g.analysis_version AS current_analysis_version
           FROM threat_training_candidates c JOIN imported_games g ON g.id=c.game_id
           WHERE c.id=?""", (candidate_id,),
    ).fetchone()


def _require_current(candidate) -> None:
    if not candidate:
        raise KeyError("Defensive candidate not found")
    if (candidate["superseded_at"]
            or candidate["analysis_version"] != candidate["current_analysis_version"]):
        raise ValueError("Defensive candidate belongs to an older game analysis")


def dismiss_defense_candidate(candidate_id: str) -> None:
    with connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if candidate["approved_at"]:
            raise ValueError("Approved exercises cannot be dismissed")
        database.execute(
            """UPDATE threat_training_candidates SET dismissed_at=?,
                  dismissed_evidence_fingerprint=source_fingerprint,updated_at=? WHERE id=?""",
            (_now(), _now(), candidate_id),
        )


def approve_defense_candidate(candidate_id: str) -> str:
    with connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if candidate["validation_state"] != "engine_supported":
            raise ValueError("Only an engine-supported candidate can enter training")
        if candidate["dismissed_at"]:
            raise ValueError("Dismissed candidate must be revisited after new evidence")
        if candidate["card_id"]:
            database.execute(
                "UPDATE cards SET pending_validation=0,revision=? WHERE id=?",
                (candidate["exercise_revision"], candidate["card_id"]),
            )
            database.execute(
                """UPDATE threat_training_candidates SET approved_at=COALESCE(approved_at,?),
                   updated_at=? WHERE id=?""",
                (_now(), _now(), candidate_id),
            )
            return candidate["card_id"]
        evidence = json.loads(candidate["evidence_json"])
        anchor = _anchor_from_json(evidence["anchor"])
        board = chess.Board(anchor.position.start_fen)
        for move_uci in anchor.position.prefix_uci:
            board.push_uci(move_uci)
        if ("white" if board.turn else "black") != anchor.position.learner_color:
            raise ValueError("Candidate is not a learner-turn position")
        card_id = hashlib.sha256(f"defense\0{candidate_id}".encode()).hexdigest()
        today = date.today().isoformat()
        database.execute(
            """INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at)
               VALUES(?,?,?,?)""",
            (DEFENSE_REPERTOIRE_ID, "Defensive tactics", "Your analyzed games", today),
        )
        database.execute(
            """INSERT OR IGNORE INTO cards(
                 id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                 content_type,scheduling_mode,source_ref,source_fen,trained_color,
                 introduced_at,revision)
               VALUES(?,?, 'checkpoint',?,'[]','learning',?,'defense','normal',?,?,?,?,?)""",
            (card_id, DEFENSE_REPERTOIRE_ID, board.fen(), today,
             candidate_id, anchor.position.start_fen, anchor.position.learner_color,
             today, candidate["exercise_revision"]),
        )
        database.execute(
            """UPDATE threat_training_candidates SET approved_at=COALESCE(approved_at,?),
                  card_id=?,updated_at=? WHERE id=?""",
            (_now(), card_id, _now(), candidate_id),
        )
        return card_id


def _exercise_from_rows(database, candidate) -> DefenseExercise:
    evidence = json.loads(candidate["evidence_json"])
    reports = [dict(row) for row in database.execute(
        """SELECT relation.role,request.report_json FROM threat_candidate_requests relation
           JOIN threat_analysis_requests request ON request.id=relation.request_id
           WHERE relation.candidate_id=? AND request.state='complete'""",
        (candidate["id"],),
    )]
    by_role: dict[str, list[AnalysisReport]] = {}
    for row in reports:
        if row["report_json"]:
            by_role.setdefault(row["role"], []).append(
                report_from_json(json.loads(row["report_json"]))
            )
    if not by_role.get("best") or not by_role.get("historical"):
        raise ValueError("Required validation reports are unavailable")
    validation = json.loads(candidate["validation_json"])
    return DefenseExercise(
        candidate_id=candidate["id"], finding_id=candidate["finding_id"],
        exercise_revision=candidate["exercise_revision"],
        anchor=_anchor_from_json(evidence["anchor"]),
        seed=_seed_from_json(evidence["seed"]),
        policy=ThreatPolicy(**json.loads(candidate["policy_json"])),
        best_report=by_role["best"][0],
        evaluated_reports=tuple(by_role["historical"] + by_role.get("attempt", [])),
        refutation_uci=tuple(validation.get("refutation_uci", ())),
    )


def read_defense_exercise(candidate_id: str) -> dict:
    with read_connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if not candidate["approved_at"] or not candidate["card_id"]:
            raise ValueError("Candidate is not approved for training")
        exercise = _exercise_from_rows(database, candidate)
    return {
        "candidate_id": candidate_id,
        "finding_id": exercise.finding_id,
        "exercise_revision": exercise.exercise_revision,
        "card_id": candidate["card_id"],
        "start_fen": exercise.anchor.position.start_fen,
        "prefix_uci": exercise.anchor.position.prefix_uci,
        "learner_color": exercise.anchor.position.learner_color,
        "prompt": "Choose your move.",
    }


def submit_defense_attempt(
    candidate_id: str, *, attempt_id: str, exercise_revision: int,
    queue_entry_id: int, move_uci: str, light_first_interval_days: int,
) -> dict:
    with read_connection() as database:
        prior = database.execute(
            "SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["move_uci"] != move_uci
                    or prior["queue_entry_id"] != queue_entry_id):
                raise ValueError("Attempt ID already belongs to a different submission")
            return {**json.loads(prior["grade_json"]), "idempotent": True}
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if not candidate["approved_at"] or not candidate["card_id"]:
            raise ValueError("Candidate is not approved for training")
        if candidate["exercise_revision"] != exercise_revision:
            raise ValueError("Exercise revision changed; reload before trying again")
        exercise = _exercise_from_rows(database, candidate)
    grade = grade_defense_move(exercise, move_uci)
    if grade.status == "needs_analysis" and grade.analysis_request:
        request = grade.analysis_request
        with connection() as database:
            current = _candidate(database, candidate_id)
            _require_current(current)
            if current["exercise_revision"] != exercise_revision:
                raise ValueError("Exercise revision changed; reload before trying again")
            database.execute(
                """INSERT OR IGNORE INTO threat_analysis_requests(
                     id,request_json,created_at,updated_at) VALUES(?,?,?,?)""",
                (request.request_id, json.dumps(asdict(request)), _now(), _now()),
            )
            database.execute(
                """INSERT OR IGNORE INTO threat_candidate_requests(candidate_id,request_id,role)
                   VALUES(?,?,'attempt')""", (candidate_id, request.request_id),
            )
        return {"status": "needs_analysis", "diagnostic": grade.diagnostic,
                "analysis_request_id": request.request_id}
    response = {"status": grade.status, "diagnostic": grade.diagnostic,
                "loss_cp": grade.loss_cp,
                "allows_target_fork": grade.allows_target_fork}
    if grade.status not in {"correct", "incorrect"}:
        return response
    evidence = json.loads(candidate["evidence_json"])
    response["feedback"] = {
        "knight_route": evidence["knight_route"] if grade.allows_target_fork
                        or grade.status == "correct" else [],
        "fork_geometry": evidence["seed"]["geometry"] if grade.allows_target_fork
                         or grade.status == "correct" else None,
        "sound_moves": [line.root_move_uci for line in exercise.best_report.lines],
        "refutation_uci": list(exercise.refutation_uci) if grade.allows_target_fork else [],
    }
    now = datetime.now(timezone.utc)
    today = date.today()
    with connection() as database:
        existing = database.execute(
            "SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if existing:
            if existing["candidate_id"] != candidate_id or existing["move_uci"] != move_uci:
                raise ValueError("Attempt ID already belongs to a different submission")
            return {**json.loads(existing["grade_json"]), "idempotent": True}
        current = _candidate(database, candidate_id)
        _require_current(current)
        if current["exercise_revision"] != exercise_revision:
            raise ValueError("Exercise revision changed; reload before trying again")
        entry = database.execute(
            """SELECT * FROM daily_queue WHERE id=? AND card_id=? AND status='queued'""",
            (queue_entry_id, current["card_id"]),
        ).fetchone()
        if not entry:
            raise ValueError("This defensive exercise is no longer the active queue entry")
        scheduling = apply_scheduling_review(
            database, current["card_id"],
            "correct" if grade.status == "correct" else "again",
            guided=False, source_kind="study", source_ref=f"defense:{attempt_id}",
            light_first_interval_days=light_first_interval_days,
            reviewed_at=now, review_day=today,
        )
        database.execute(
            """UPDATE daily_queue SET status='complete',attempt_state=?,review_result_json=?
               WHERE id=?""",
            ("clean" if grade.status == "correct" else "again",
             json.dumps(scheduling), queue_entry_id),
        )
        if scheduling["requeue_today"]:
            if scheduling["requeue_after_cards"] is None:
                next_cycle = database.execute(
                    "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
                    (today.isoformat(), current["card_id"]),
                ).fetchone()[0]
                final_position = database.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                    (today.isoformat(),),
                ).fetchone()[0]
                database.execute(
                    """INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,
                         card_bucket,admission_kind) VALUES(?,?,?,?,?,'defense','review')""",
                    (today.isoformat(), current["card_id"], next_cycle, final_position,
                     "reinforcement"),
                )
                preserve_daily_queue_order(database, today.isoformat())
            else:
                ensure_card_queued_after(
                    database, current["card_id"], scheduling["requeue_after_cards"],
                    "reinforcement" if grade.status == "correct" else "again",
                )
        database.execute(
            """INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,
                  queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (attempt_id, candidate_id, current["card_id"], queue_entry_id,
             exercise_revision, move_uci, json.dumps(response), now.isoformat()),
        )
    return {**response, "idempotent": False}
