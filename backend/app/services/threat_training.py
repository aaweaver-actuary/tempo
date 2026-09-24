"""Manual admission and idempotent study of defensive-move rubrics."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import chess

from ..database import connection, read_connection
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task
from .review_service import (
    apply_scheduling_review, ensure_card_queued_after, preserve_daily_queue_order,
    rebuild_defense_schedule,
)
from .threat_grading import DefenseExercise, grade_defense_move
from .threat_pipeline import (
    _anchor_from_json, _request_from_json, _seed_from_json,
    report_from_json, validate_analysis_report,
)
from .threat_validation import (
    AnalysisReport, make_validation_plan, recognition_preview, validate_threat_anchor,
)
from .threat_models import ThreatPolicy


DEFENSE_REPERTOIRE_ID = "__defense__"
RECOGNITION_RUBRIC_VERSION = 3


def execute_defense_rubric_audit_slice(task: dict) -> bool:
    """Recheck one saved candidate and its old reviews without holding SQLite during replay."""
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        row = database.execute(
            """SELECT candidate.* FROM threat_training_candidates candidate
               JOIN imported_games game ON game.id=candidate.game_id
               WHERE candidate.id>? AND candidate.superseded_at IS NULL
                 AND candidate.analysis_version=game.analysis_version
                 AND (candidate.validation_state IN ('engine_supported','validated_control')
                      OR candidate.approved_at IS NOT NULL)
               ORDER BY candidate.id LIMIT 1""", (cursor,),
        ).fetchone()
        candidate = dict(row) if row else None
        reports = [dict(item) for item in database.execute(
            """SELECT relation.role,request.request_json,request.report_json
               FROM threat_candidate_requests relation JOIN threat_analysis_requests request
                 ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""",
            (candidate["id"],),
        )] if candidate else []
    if not candidate:
        return False
    evidence = json.loads(candidate["evidence_json"])
    anchor = _anchor_from_json(evidence["anchor"])
    seed = _seed_from_json(evidence["seed"])
    policy = ThreatPolicy(**json.loads(candidate["policy_json"]))
    plan = make_validation_plan(
        anchor, engine_version=next((json.loads(item["request_json"])["engine_version"]
                                     for item in reports if item["role"] == "best"), ""),
        network_version=next((json.loads(item["request_json"])["network_version"]
                                      for item in reports if item["role"] == "best"), ""),
        policy=policy,
    )
    by_role: dict[str, AnalysisReport] = {}
    for item in reports:
        if not item["report_json"]:
            continue
        try:
            request = _request_from_json(json.loads(item["request_json"]))
            report = report_from_json(json.loads(item["report_json"]))
            validate_analysis_report(request, report)
            by_role[item["role"]] = report
        except (KeyError, TypeError, ValueError):
            continue
    result = validate_threat_anchor(
        anchor, seed, plan, by_role.get("best"), by_role.get("historical"), policy,
    )
    root_board = chess.Board(anchor.position.start_fen)
    for move_uci in anchor.position.prefix_uci:
        root_board.push_uci(move_uci)
    geometry = seed.geometry
    target_on_root = root_board.piece_at(chess.parse_square(geometry.major.square))
    rubric_was_misleading = (
        result.state in {"lesson_only", "rejected"}
        or target_on_root != chess.Piece(
            chess.QUEEN if geometry.major.piece == "queen" else chess.ROOK,
            anchor.position.learner_color == "white",
        )
    )
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        lease = database.execute(
            "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
        current = database.execute(
            "SELECT source_fingerprint,exercise_revision,card_id FROM threat_training_candidates WHERE id=?",
            (candidate["id"],),
        ).fetchone()
        if (not lease or lease["generation"] != task["generation"]
                or lease["lease_token"] != task["lease_token"]):
            return True
        if (current and current["source_fingerprint"] == candidate["source_fingerprint"]
                and current["exercise_revision"] == candidate["exercise_revision"]):
            eligible = result.state in {"engine_supported", "validated_control"}
            database.execute(
                """UPDATE threat_training_candidates SET validation_state=?,diagnostic=?,
                   validation_json=?,approved_at=CASE WHEN ? THEN approved_at ELSE NULL END,
                   exercise_revision=exercise_revision+1,updated_at=? WHERE id=?""",
                (result.state, result.diagnostic, json.dumps(asdict(result)), int(eligible),
                 _now(), candidate["id"]),
            )
            if current["card_id"]:
                database.execute(
                    "UPDATE cards SET pending_validation=?,revision=revision+1 WHERE id=?",
                    (0 if eligible else 1, current["card_id"]),
                )
                if not eligible:
                    database.execute(
                        "UPDATE daily_queue SET status='blocked' WHERE card_id=? AND status='queued'",
                        (current["card_id"],),
                    )
                if rubric_was_misleading:
                    database.execute(
                        """UPDATE reviews SET invalidated_at=?,invalidation_reason=?
                           WHERE card_id=? AND invalidated_at IS NULL AND source_kind='study'
                             AND source_ref IN (
                               SELECT 'defense:' || attempt_id FROM defense_attempts
                               WHERE candidate_id=? AND exercise_revision<=?)""",
                        (_now(), "Defensive recognition rubric showed the wrong board",
                         current["card_id"], candidate["id"], candidate["exercise_revision"]),
                    )
                    light_interval = database.execute(
                        "SELECT light_first_interval_days FROM settings WHERE id=1",
                    ).fetchone()[0]
                    rebuild_defense_schedule(database, current["card_id"], light_interval)
                    if eligible and database.execute(
                        "SELECT 1 FROM reviews WHERE card_id=? AND invalidated_at IS NOT NULL LIMIT 1",
                        (current["card_id"],),
                    ).fetchone():
                        ensure_card_queued_after(database, current["card_id"], after_cards=1,
                                                 attempt_state="guided")
        database.execute(
            """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (json.dumps({"cursor": candidate["id"]}), _now(), _now(), task["id"],
             task["generation"], task["lease_token"]),
        )
    if result.state in {"engine_supported", "validated_control"}:
        enqueue_defense_admission(background=True)
    if candidate["card_id"]:
        enqueue_task("daily_queue", "current", {"queue_date": date.today().isoformat()},
                     priority=10, foreground=False)
    return True


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


def _approve_in_transaction(database, candidate, *, reports_verified: bool = False,
                            prepared_position_fen: str | None = None,
                            admission_mode: str = "manual") -> str:
    _require_current(candidate)
    candidate_id = candidate["id"]
    if candidate["validation_state"] not in {"engine_supported", "validated_control"}:
        raise ValueError("Only a validated candidate can enter training")
    if not reports_verified:
        exercise = _exercise_from_rows(database, candidate)
        if recognition_preview(exercise.anchor, exercise.seed, exercise.evaluated_reports[0].lines[0]) is None:
            raise ValueError("This defensive exercise needs a validated board preview")
    if candidate["dismissed_at"]:
        raise ValueError("Dismissed candidate must be revisited after new evidence")
    if candidate["card_id"]:
        database.execute(
            "UPDATE cards SET pending_validation=0,revision=? WHERE id=?",
            (candidate["exercise_revision"], candidate["card_id"]),
        )
        database.execute(
            """UPDATE threat_training_candidates SET approved_at=COALESCE(approved_at,?),
               admission_mode=COALESCE(admission_mode,?),paused_at=NULL,
               updated_at=? WHERE id=?""",
            (_now(), admission_mode, _now(), candidate_id),
        )
        return candidate["card_id"]
    evidence = json.loads(candidate["evidence_json"])
    anchor = _anchor_from_json(evidence["anchor"])
    board = chess.Board(prepared_position_fen or anchor.position.start_fen)
    if prepared_position_fen is None:
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
              card_id=?,admission_mode=COALESCE(admission_mode,?),paused_at=NULL,
              updated_at=? WHERE id=?""",
        (_now(), card_id, admission_mode, _now(), candidate_id),
    )
    return card_id


def approve_defense_candidate(candidate_id: str) -> str:
    with connection() as database:
        return _approve_in_transaction(database, _candidate(database, candidate_id))


def pause_defense_candidate(candidate_id: str, paused: bool) -> None:
    with connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if candidate["approved_at"]:
            raise ValueError("A card already in training cannot be paused here")
        database.execute(
            "UPDATE threat_training_candidates SET paused_at=?,updated_at=? WHERE id=?",
            (_now() if paused else None, _now(), candidate_id),
        )


def train_defense_candidate_now(candidate_id: str) -> str:
    with connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        card_id = _approve_in_transaction(database, candidate, admission_mode="explicit")
        ensure_card_queued_after(database, card_id, after_cards=0,
                                 attempt_state="guided", priority_reason="Discovery · defensive recognition")
        database.execute(
            """UPDATE daily_queue SET admission_kind='explicit',admission_source=?
               WHERE queue_date=? AND card_id=? AND status='queued'""",
            (f"defense:{candidate_id}", date.today().isoformat(), card_id),
        )
    return card_id


def enqueue_defense_admission(*, background: bool) -> None:
    enqueue_task(
        "defensive_admission", date.today().isoformat(),
        {"queue_date": date.today().isoformat(), "phase": "threat",
         "cursor": "", "control_exhausted": False},
        priority=150, foreground=not background,
    )


def execute_defense_admission_slice(task: dict) -> bool:
    """Consider one validated candidate, then yield to foreground work."""
    day = task["payload"]["queue_date"]
    if day != date.today().isoformat():
        return False
    previous_phase = task["payload"].get("phase", "threat")
    phase = previous_phase
    cursor = int(task["payload"].get("cursor", 0)) if str(task["payload"].get("cursor", 0)).isdigit() else 0
    control_exhausted = bool(task["payload"].get("control_exhausted", False))
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        limit = database.execute(
            "SELECT defense_new_cards_per_day FROM settings WHERE id=1",
        ).fetchone()[0]
        admitted_today = database.execute(
            """SELECT COUNT(*) FROM threat_training_candidates admitted
               JOIN cards card ON card.id=admitted.card_id
               WHERE admitted.admission_mode='automatic' AND card.introduced_at=?""",
            (day,),
        ).fetchone()[0]
        if admitted_today >= limit:
            return False
        admitted_controls_today = database.execute(
            """SELECT COUNT(*) FROM threat_training_candidates admitted
               JOIN cards card ON card.id=admitted.card_id
               WHERE admitted.admission_mode='automatic' AND card.introduced_at=?
                 AND admitted.validation_state='validated_control'""",
            (day,),
        ).fetchone()[0]
        control_slot = int.from_bytes(
            hashlib.sha256(day.encode()).digest()[:2], "big",
        ) % min(5, limit)
        if not admitted_controls_today and not control_exhausted and admitted_today >= control_slot:
            phase = "control"
        else:
            phase = "threat"
        if phase != previous_phase:
            cursor = 0
        selected_state = "validated_control" if phase == "control" else "engine_supported"
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        selection_sql = """SELECT c.*,game.analysis_version current_analysis_version,
                      COALESCE((SELECT COUNT(DISTINCT recurring.game_id)
                          FROM game_position_occurrences anchor
                          JOIN game_position_occurrences recurring ON recurring.fen_key=anchor.fen_key
                          JOIN imported_games recent_game ON recent_game.id=recurring.game_id
                          WHERE anchor.game_id=c.game_id AND anchor.ply=c.player_ply
                            AND recent_game.played_at>=? AND recent_game.adaptive_excluded=0),0)
                        recent_position_games
               FROM threat_training_candidates c JOIN imported_games game ON game.id=c.game_id
               WHERE c.validation_state=?
                 AND c.approved_at IS NULL AND c.dismissed_at IS NULL AND c.paused_at IS NULL
                 AND c.superseded_at IS NULL AND c.analysis_version=game.analysis_version
                 AND NOT EXISTS(SELECT 1 FROM threat_training_candidates earlier
                     WHERE earlier.incident_id=c.incident_id AND earlier.player_ply<c.player_ply
                       AND earlier.superseded_at IS NULL AND earlier.dismissed_at IS NULL
                       AND earlier.validation_state IN ('needs_analysis','engine_supported','validated_control'))
               ORDER BY recent_position_games DESC,game.played_at DESC,c.id LIMIT 1 OFFSET ?"""
        row = database.execute(selection_sql, (recent_cutoff, selected_state, cursor)).fetchone()
        if row is None and phase == "control":
            phase, cursor = "threat", 0
            control_exhausted = True
            row = database.execute(selection_sql, (recent_cutoff, "engine_supported", cursor)).fetchone()
        candidate = dict(row) if row else None
        report_rows = [dict(report_row) for report_row in database.execute(
            """SELECT relation.role,request.request_json,request.report_json
               FROM threat_candidate_requests relation
               JOIN threat_analysis_requests request ON request.id=relation.request_id
               WHERE relation.candidate_id=? AND relation.role IN ('best','historical')""",
            (candidate["id"],),
        )] if candidate else []
    if not candidate:
        return False
    evidence = json.loads(candidate["evidence_json"])
    anchor = _anchor_from_json(evidence["anchor"])
    board = chess.Board(anchor.position.start_fen)
    for move_uci in anchor.position.prefix_uci:
        board.push_uci(move_uci)
    reports_verified = False
    try:
        roles = set()
        historical_line = None
        for report_row in report_rows:
            request = _request_from_json(json.loads(report_row["request_json"]))
            report = report_from_json(json.loads(report_row["report_json"]))
            validate_analysis_report(request, report)
            roles.add(report_row["role"])
            if report_row["role"] == "historical" and report.lines:
                historical_line = report.lines[0]
        reports_verified = ({"best", "historical"}.issubset(roles)
                            and historical_line is not None
                            and recognition_preview(anchor, _seed_from_json(evidence["seed"]),
                                                    historical_line) is not None)
    except (TypeError, KeyError, ValueError):
        reports_verified = False
    candidate_position = " ".join(board.fen().split()[:4])
    introduced = False
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        lease = database.execute(
            "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
        if not lease or lease["generation"] != task["generation"] or lease["lease_token"] != task["lease_token"]:
            return True
        limit = database.execute(
            "SELECT defense_new_cards_per_day FROM settings WHERE id=1"
        ).fetchone()[0]
        admitted_today = database.execute(
            """SELECT COUNT(*) FROM threat_training_candidates admitted
               JOIN cards card ON card.id=admitted.card_id
               WHERE admitted.admission_mode='automatic' AND card.introduced_at=?""",
            (day,),
        ).fetchone()[0]
        same_incident = database.execute(
            """SELECT 1 FROM threat_training_candidates
               WHERE incident_id=? AND approved_at IS NOT NULL LIMIT 1""",
            (candidate["incident_id"],),
        ).fetchone()
        same_position = database.execute(
            """SELECT 1 FROM cards WHERE content_type='defense' AND archived=0
               AND substr(start_fen,1,length(?))=? LIMIT 1""",
            (candidate_position, candidate_position),
        ).fetchone()
        refreshed = _candidate(database, candidate["id"])
        if (reports_verified and (candidate["card_id"] or not same_incident and not same_position)
                and refreshed
                and refreshed["validation_state"] in {"engine_supported", "validated_control"}
                and not refreshed["approved_at"]
                and not refreshed["paused_at"]
                and (candidate["card_id"] or admitted_today < limit)):
            _approve_in_transaction(database, refreshed, reports_verified=True,
                                    prepared_position_fen=board.fen(),
                                    admission_mode="automatic")
            introduced = True
        database.execute(
            """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (json.dumps({"queue_date": day,
                         "phase": "threat" if introduced and phase == "control" else phase,
                         "cursor": 0 if introduced else cursor + 1,
                         "control_exhausted": control_exhausted}),
             _now(), _now(),
             task["id"], task["generation"], task["lease_token"]),
        )
    if introduced:
        enqueue_task("daily_queue", "current", {"queue_date": day}, priority=10, foreground=False)
    return True


def _exercise_from_rows(database, candidate) -> DefenseExercise:
    evidence = json.loads(candidate["evidence_json"])
    reports = [dict(row) for row in database.execute(
        """SELECT relation.role,request.request_json,request.report_json FROM threat_candidate_requests relation
           JOIN threat_analysis_requests request ON request.id=relation.request_id
           WHERE relation.candidate_id=? AND request.state='complete'""",
        (candidate["id"],),
    )]
    by_role: dict[str, list[AnalysisReport]] = {}
    for row in reports:
        if row["report_json"]:
            request = _request_from_json(json.loads(row["request_json"]))
            report = report_from_json(json.loads(row["report_json"]))
            validate_analysis_report(request, report)
            by_role.setdefault(row["role"], []).append(report)
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
        preview = recognition_preview(exercise.anchor, exercise.seed, exercise.evaluated_reports[0].lines[0])
        if preview is None:
            raise ValueError("This defensive exercise needs a validated board preview")
    return {
        "candidate_id": candidate_id,
        "finding_id": exercise.finding_id,
        "exercise_revision": exercise.exercise_revision,
        "card_id": candidate["card_id"],
        "start_fen": exercise.anchor.position.start_fen,
        "prefix_uci": exercise.anchor.position.prefix_uci,
        "learner_color": exercise.anchor.position.learner_color,
        "prompt": "What danger should your next move account for?",
        "rubric_version": RECOGNITION_RUBRIC_VERSION,
        "recognition_required": True,
        "proposed_move_uci": preview.proposed_move_uci,
        "proposed_move_san": preview.proposed_move_san,
        "preview_fen": preview.position_fen,
        "fork_move_san": preview.fork_move_san,
    }


def _complete_defense_in_transaction(
    database, candidate, *, attempt_id: str, exercise_revision: int,
    queue_entry_id: int, move_uci: str, response: dict,
    light_first_interval_days: int,
) -> dict:
    """Record one real review and one completed queue attempt atomically."""
    previous = database.execute(
        "SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,),
    ).fetchone()
    if previous:
        if (previous["candidate_id"] != candidate["id"] or previous["move_uci"] != move_uci
                or previous["queue_entry_id"] != queue_entry_id):
            raise ValueError("Attempt ID already belongs to a different submission")
        return {**json.loads(previous["grade_json"]), "idempotent": True}
    _require_current(candidate)
    if candidate["exercise_revision"] != exercise_revision:
        raise ValueError("Exercise revision changed; reload before trying again")
    entry = database.execute(
        """SELECT * FROM daily_queue WHERE id=? AND card_id=? AND status='queued'""",
        (queue_entry_id, candidate["card_id"]),
    ).fetchone()
    if not entry:
        raise ValueError("This defensive exercise is no longer the active queue entry")
    now = datetime.now(timezone.utc)
    today = date.today()
    correct = response["status"] == "correct"
    scheduling = apply_scheduling_review(
        database, candidate["card_id"], "correct" if correct else "again",
        guided=False, source_kind="study", source_ref=f"defense:{attempt_id}",
        light_first_interval_days=light_first_interval_days,
        reviewed_at=now, review_day=today,
    )
    database.execute(
        """UPDATE daily_queue SET status='complete',attempt_state=?,review_result_json=?
           WHERE id=?""",
        ("clean" if correct else "again", json.dumps(scheduling), queue_entry_id),
    )
    if scheduling["requeue_today"]:
        if scheduling["requeue_after_cards"] is None:
            next_cycle = database.execute(
                "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
                (today.isoformat(), candidate["card_id"]),
            ).fetchone()[0]
            final_position = database.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (today.isoformat(),),
            ).fetchone()[0]
            database.execute(
                """INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,
                     card_bucket,admission_kind) VALUES(?,?,?,?,?,'defense','review')""",
                (today.isoformat(), candidate["card_id"], next_cycle, final_position,
                 "reinforcement"),
            )
            preserve_daily_queue_order(database, today.isoformat())
        else:
            ensure_card_queued_after(
                database, candidate["card_id"], scheduling["requeue_after_cards"],
                "reinforcement" if correct else "again",
            )
    database.execute(
        """INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,
              queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (attempt_id, candidate["id"], candidate["card_id"], queue_entry_id,
         exercise_revision, move_uci, json.dumps(response), now.isoformat()),
    )
    return {**response, "idempotent": False}


def submit_defense_recognition(candidate_id: str, request) -> dict:
    """Persist recognition, then reveal its explanation before the defense move."""
    answer = request.model_dump()
    serialized = json.dumps(answer, sort_keys=True)
    if request.rubric_version != RECOGNITION_RUBRIC_VERSION:
        raise ValueError("Recognition rubric changed; reload this card before answering")
    with read_connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if (not candidate["approved_at"] or candidate["exercise_revision"] != request.exercise_revision
                or candidate["validation_state"] not in {"engine_supported", "validated_control"}):
            raise ValueError("Exercise revision changed; reload before answering")
        exercise = _exercise_from_rows(database, candidate)
        if recognition_preview(exercise.anchor, exercise.seed, exercise.evaluated_reports[0].lines[0]) is None:
            raise ValueError("This defensive exercise needs a validated board preview")
        prior = database.execute(
            """SELECT candidate_id,queue_entry_id,exercise_revision,answer_json
               FROM defense_recognition_submissions WHERE attempt_id=?""",
            (request.attempt_id,),
        ).fetchone()
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["queue_entry_id"] != request.queue_entry_id
                    or prior["exercise_revision"] != request.exercise_revision
                    or prior["answer_json"] != serialized):
                raise ValueError("Recognition attempt ID belongs to a different answer")
            completed = database.execute(
                "SELECT grade_json FROM defense_attempts WHERE attempt_id=?", (request.attempt_id,),
            ).fetchone()
            if completed:
                return {**json.loads(completed["grade_json"]), "idempotent": True}
            evidence = json.loads(candidate["evidence_json"])
            source_game = database.execute("SELECT game_url FROM imported_games WHERE id=?",
                                           (candidate["game_id"],)).fetchone()
            return {"status": "ready_for_move", "recognition_attempt_id": request.attempt_id,
                    "recognition_correct": bool(database.execute(
                        "SELECT recognition_correct FROM defense_recognition_submissions WHERE attempt_id=?",
                        (request.attempt_id,),
                    ).fetchone()[0]),
                    "feedback": {"knight_route": [{"from_square": evidence["seed"]["geometry"]["knight_from"],
                                                   "to_square": evidence["seed"]["geometry"]["knight_to"]}],
                                 "fork_geometry": evidence["seed"]["geometry"],
                                 "sound_moves": [], "refutation_uci": json.loads(candidate["validation_json"]).get("refutation_uci", []),
                                 "source_game_id": candidate["game_id"],
                                 "source_game_url": source_game["game_url"] if source_game else None},
                    "idempotent": True}
        queue_entry = database.execute(
            "SELECT 1 FROM daily_queue WHERE id=? AND card_id=? AND status='queued'",
            (request.queue_entry_id, candidate["card_id"]),
        ).fetchone()
        if not queue_entry:
            raise ValueError("This defensive exercise is no longer active")
        evidence = json.loads(candidate["evidence_json"])
        geometry = evidence["seed"]["geometry"]
        dangerous_square = geometry["knight_from"]
        control = candidate["validation_state"] == "validated_control"
        correct = (request.no_concrete_threat and request.consequence == "none" and not request.hinted
                   if control else
                   not request.no_concrete_threat and not request.hinted
                   and request.dangerous_piece_square == dangerous_square
                   and request.destination_square == geometry["knight_to"]
                   and request.king_square == geometry["king"]["square"]
                   and request.major_square == geometry["major"]["square"]
                   and request.consequence == "checking_fork")
        validated_line = json.loads(candidate["validation_json"]).get("refutation_uci", [])
        visible_route = [{"from_square": geometry["knight_from"], "to_square": geometry["knight_to"]}]
        source_game = database.execute(
            "SELECT game_url FROM imported_games WHERE id=?", (candidate["game_id"],),
        ).fetchone()
    with connection() as database:
        prior = database.execute(
            """SELECT candidate_id,queue_entry_id,exercise_revision,answer_json
               FROM defense_recognition_submissions WHERE attempt_id=?""",
            (request.attempt_id,),
        ).fetchone()
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["queue_entry_id"] != request.queue_entry_id
                    or prior["exercise_revision"] != request.exercise_revision
                    or prior["answer_json"] != serialized):
                raise ValueError("Recognition attempt ID belongs to a different answer")
            completed = database.execute(
                "SELECT grade_json FROM defense_attempts WHERE attempt_id=?", (request.attempt_id,),
            ).fetchone()
            return ({**json.loads(completed["grade_json"]), "idempotent": True} if completed
                    else {"status": "ready_for_move", "recognition_attempt_id": request.attempt_id,
                          "idempotent": True})
        current = _candidate(database, candidate_id)
        _require_current(current)
        if current["exercise_revision"] != request.exercise_revision:
            raise ValueError("Exercise revision changed; reload before answering")
        if current["validation_state"] != candidate["validation_state"]:
            raise ValueError("Exercise evidence changed; reload before answering")
        database.execute(
            """INSERT INTO defense_recognition_submissions(
                 attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,
                 recognition_correct,created_at) VALUES(?,?,?,?,?,?,?)""",
            (request.attempt_id, candidate_id, request.queue_entry_id,
             request.exercise_revision, serialized, int(correct), _now()),
        )
        if control:
            response = {
                "status": "correct" if correct else "incorrect",
                "diagnostic": "The apparent checking fork can be captured legally",
                "recognition_correct": correct,
                "defense_status": "not_applicable",
                "feedback": {"knight_route": visible_route,
                             "fork_geometry": geometry, "sound_moves": [],
                             "refutation_uci": validated_line,
                             "control_explanation": "The forking knight is capturable before it wins material.",
                             "source_game_id": candidate["game_id"],
                             "source_game_url": source_game["game_url"] if source_game else None},
            }
            light_interval = database.execute(
                "SELECT light_first_interval_days FROM settings WHERE id=1",
            ).fetchone()[0]
            return _complete_defense_in_transaction(
                database, current, attempt_id=request.attempt_id,
                exercise_revision=request.exercise_revision,
                queue_entry_id=request.queue_entry_id, move_uci="", response=response,
                light_first_interval_days=light_interval,
            )
    return {"status": "ready_for_move", "recognition_attempt_id": request.attempt_id,
            "recognition_correct": correct,
            "feedback": {"knight_route": visible_route,
                         "fork_geometry": geometry, "sound_moves": [], "refutation_uci": validated_line,
                         "source_game_id": candidate["game_id"],
                         "source_game_url": source_game["game_url"] if source_game else None},
            "idempotent": False}


def submit_defense_attempt(
    candidate_id: str, *, attempt_id: str, exercise_revision: int,
    queue_entry_id: int, move_uci: str, light_first_interval_days: int,
    recognition_attempt_id: str | None = None,
) -> dict:
    with read_connection() as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if (not candidate["approved_at"] or candidate["exercise_revision"] != exercise_revision
                or candidate["validation_state"] not in {"engine_supported", "validated_control"}):
            raise ValueError("Exercise revision changed; reload before trying again")
        prior = database.execute(
            "SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["move_uci"] != move_uci
                    or prior["queue_entry_id"] != queue_entry_id):
                raise ValueError("Attempt ID already belongs to a different submission")
            return {**json.loads(prior["grade_json"]), "idempotent": True}
        if not candidate["card_id"]:
            raise ValueError("Candidate is not approved for training")
        if candidate["validation_state"] == "validated_control":
            raise ValueError("Control exercises finish with recognition; no defense move is required")
        if candidate["exercise_revision"] != exercise_revision:
            raise ValueError("Exercise revision changed; reload before trying again")
        recognition = None
        if recognition_attempt_id:
            recognition = database.execute(
                """SELECT * FROM defense_recognition_submissions WHERE attempt_id=?
                   AND candidate_id=? AND queue_entry_id=? AND exercise_revision=?""",
                (recognition_attempt_id, candidate_id, queue_entry_id, exercise_revision),
            ).fetchone()
            if not recognition:
                raise ValueError("Recognition answer is missing or stale")
        exercise = _exercise_from_rows(database, candidate)
        source_game = database.execute(
            "SELECT game_url FROM imported_games WHERE id=?", (candidate["game_id"],),
        ).fetchone()
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
    recognition_correct = bool(recognition["recognition_correct"]) if recognition else None
    completed_status = "incorrect" if recognition_correct is False and grade.status == "correct" else grade.status
    response = {"status": completed_status, "diagnostic": grade.diagnostic,
                "loss_cp": grade.loss_cp,
                "allows_target_fork": grade.allows_target_fork,
                "recognition_correct": recognition_correct,
                "defense_status": grade.status}
    if grade.status not in {"correct", "incorrect"}:
        return response
    evidence = json.loads(candidate["evidence_json"])
    response["feedback"] = {
        "knight_route": [{"from_square": evidence["seed"]["geometry"]["knight_from"],
                          "to_square": evidence["seed"]["geometry"]["knight_to"]}]
                        if grade.allows_target_fork or grade.status == "correct" else [],
        "fork_geometry": evidence["seed"]["geometry"] if grade.allows_target_fork
                         or grade.status == "correct" else None,
        "sound_moves": [line.root_move_uci for line in exercise.best_report.lines],
        "refutation_uci": list(exercise.refutation_uci) if grade.allows_target_fork else [],
        "source_game_id": candidate["game_id"],
        "source_game_url": source_game["game_url"] if source_game else None,
    }
    with connection() as database:
        current = _candidate(database, candidate_id)
        return _complete_defense_in_transaction(
            database, current, attempt_id=attempt_id,
            exercise_revision=exercise_revision, queue_entry_id=queue_entry_id,
            move_uci=move_uci, response=response,
            light_first_interval_days=light_first_interval_days,
        )
