from __future__ import annotations
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime,timezone
import json
from .clock import date
from .database import connection,read_connection
from . import postgres_store
from .queue_position_lock import lock_queue_date_for_position
from .review_service import apply_scheduling_review,ensure_card_queued_after,preserve_daily_queue_order
from .threat_grading import DefenseExercise,grade_defense_move
from .defense_decode import _anchor_from_json,_seed_from_json,report_from_json,validate_analysis_report
from .threat_models import ThreatPolicy
from .threat_validation import recognition_preview
RECOGNITION_RUBRIC_VERSION=3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

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
    if postgres_store.configured():
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (candidate["card_id"],))
        queue_day = database.execute(
            "SELECT queue_date FROM daily_queue WHERE id=?", (queue_entry_id,),
        ).fetchone()
        if queue_day is None:
            raise ValueError("This defensive exercise is no longer the active queue entry")
        lock_queue_date_for_position(database, queue_day[0])
        candidate = _candidate(database, candidate["id"])
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

def submit_defense_recognition(candidate_id: str, request, *, write_database=None) -> dict:
    """Persist recognition, then reveal its explanation before the defense move."""
    answer = request.model_dump()
    serialized = json.dumps(answer, sort_keys=True)
    if request.rubric_version != RECOGNITION_RUBRIC_VERSION:
        raise ValueError("Recognition rubric changed; reload this card before answering")
    with nullcontext(write_database) if write_database is not None else read_connection() as database:
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
            source_game = database.execute("SELECT definition_json::jsonb->>'source_game_url' game_url FROM accepted_defense_exercises WHERE definition_json::jsonb->>'game_id'=? LIMIT 1",
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
            "SELECT definition_json::jsonb->>'source_game_url' game_url FROM accepted_defense_exercises WHERE definition_json::jsonb->>'game_id'=? LIMIT 1", (candidate["game_id"],),
        ).fetchone()
    with nullcontext(write_database) if write_database is not None else connection() as database:
        if postgres_store.configured():
            database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (candidate["card_id"],))
            queue_day = database.execute(
                "SELECT queue_date FROM daily_queue WHERE id=?", (request.queue_entry_id,),
            ).fetchone()
            if queue_day is None:
                raise ValueError("This defensive exercise is no longer active")
            lock_queue_date_for_position(database, queue_day[0])
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
    recognition_attempt_id: str | None = None, write_database=None,
) -> dict:
    with nullcontext(write_database) if write_database is not None else read_connection() as database:
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
            "SELECT definition_json::jsonb->>'source_game_url' game_url FROM accepted_defense_exercises WHERE definition_json::jsonb->>'game_id'=? LIMIT 1", (candidate["game_id"],),
        ).fetchone()
    grade = grade_defense_move(exercise, move_uci)
    if grade.status == "needs_analysis" and grade.analysis_request:
        request = grade.analysis_request
        with nullcontext(write_database) if write_database is not None else connection() as database:
            current = _candidate(database, candidate_id)
            _require_current(current)
            if current["exercise_revision"] != exercise_revision:
                raise ValueError("Exercise revision changed; reload before trying again")
            database.execute("INSERT INTO analysis_obligations(request_id,candidate_id,request_json) VALUES(?,?,?) ON CONFLICT DO NOTHING",(request.request_id,candidate_id,json.dumps(asdict(request))))
            diagnostic = "This move needs analysis; the request is retained in Learning."
        return {"status": "needs_analysis", "diagnostic": diagnostic,
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
    with nullcontext(write_database) if write_database is not None else connection() as database:
        current = _candidate(database, candidate_id)
        return _complete_defense_in_transaction(
            database, current, attempt_id=attempt_id,
            exercise_revision=exercise_revision, queue_entry_id=queue_entry_id,
            move_uci=move_uci, response=response,
            light_first_interval_days=light_first_interval_days,
        )


def exercise_from_definition(definition):
    reports=[report_from_json(report) for report in definition['reports']]
    if len(reports)<2: raise ValueError('Defense requires best and historical reports')
    for report in reports: validate_analysis_report(report.request,report)
    exercise=DefenseExercise(candidate_id=definition['candidate_id'],finding_id=definition['finding_id'],exercise_revision=definition['exercise_revision'],anchor=_anchor_from_json(definition['anchor']),seed=_seed_from_json(definition['seed']),policy=ThreatPolicy(**definition['policy']),best_report=reports[0],evaluated_reports=tuple(reports[1:]),refutation_uci=tuple(definition.get('refutation_uci',())))
    if recognition_preview(exercise.anchor,exercise.seed,exercise.evaluated_reports[0].lines[0]) is None:raise ValueError('Defense requires a validated preview')
    return exercise

def _candidate(database,candidate_id):
    row=database.execute("SELECT d.*,c.serving_active,c.archived FROM accepted_defense_exercises d JOIN cards c ON c.id=d.card_id WHERE d.candidate_id=?",(candidate_id,)).fetchone()
    if row is None:raise KeyError('Defense exercise unavailable')
    definition=json.loads(row['definition_json'])
    return {**definition,'id':candidate_id,'card_id':row['card_id'],'exercise_revision':row['exercise_revision'],'approved_at':'accepted','serving_active':row['serving_active'],'archived':row['archived'],'validation_state':definition['validation_state'],'evidence_json':json.dumps({'anchor':definition['anchor'],'seed':definition['seed']}),'validation_json':json.dumps({'refutation_uci':definition.get('refutation_uci',[])}),'definition':definition}

def _require_current(candidate):
    if not candidate['serving_active'] or candidate['archived']:raise ValueError('Defense exercise was retired')

def _exercise_from_rows(database,candidate):return exercise_from_definition(candidate['definition'])
