"""Manual admission and idempotent study of defensive-move rubrics."""

from __future__ import annotations

from dataclasses import asdict
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import chess

from .repertoire_game_refresh import refresh_game_publications_after_mutation
from tempo_legacy_database.connections import background_read_connection, connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from tempo_legacy_database.adapters.threats import training as storage
from .activity_gate import activity_gate
from .durable_tasks import (
    advance_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    enqueue_task,
    lock_current_slice,
)
from .review_service import (
    apply_scheduling_review, capture_daily_queue_order, ensure_card_queued_after, preserve_daily_queue_order,
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


def _persist_audited_rubric_validation(database, candidate_id: str, result, eligible: bool) -> None:
    storage.persist_rubric_validation(
        database, result.state, result.diagnostic, json.dumps(asdict(result)), eligible, _now(), candidate_id,
    )


def execute_defense_rubric_audit_slice(task: dict) -> bool:
    """Recheck one saved candidate and its old reviews without holding SQLite during replay."""
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    with background_read_connection() as database:
        row = storage.next_rubric_candidate(database, cursor)
        candidate = dict(row) if row else None
        reports = [dict(item) for item in storage.rubric_reports(database, candidate["id"])] if candidate else []
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
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return True
        else:
            lease = storage.task_lease(database, task["id"])
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return True
        current = storage.rubric_candidate_source(database, candidate["id"])
        if (current and current["source_fingerprint"] == candidate["source_fingerprint"]
                and current["exercise_revision"] == candidate["exercise_revision"]):
            eligible = result.state in {"engine_supported", "validated_control"}
            _persist_audited_rubric_validation(database, candidate["id"], result, eligible)
            if current["card_id"]:
                storage.revise_card_validation(database, 0 if eligible else 1, current["card_id"])
                if not eligible:
                    storage.block_queued_card(database, current["card_id"])
                if rubric_was_misleading:
                    storage.invalidate_rubric_reviews(
                        database, _now(), "Defensive recognition rubric showed the wrong board",
                        current["card_id"], candidate["id"], candidate["exercise_revision"],
                    )
                    light_interval = storage.light_interval(database)[0]
                    rebuild_defense_schedule(database, current["card_id"], light_interval)
                    if eligible and storage.invalidated_review_exists(database, current["card_id"]):
                        ensure_card_queued_after(database, current["card_id"], after_cards=1,
                                                 attempt_state="guided")
        storage.advance_task(
            database, json.dumps({"cursor": candidate["id"]}), _now(), _now(), task["id"], task["generation"],
            task["lease_token"],
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
    return storage.candidate(database, candidate_id)


def _require_current(candidate) -> None:
    if not candidate:
        raise KeyError("Defensive candidate not found")
    if (candidate["superseded_at"]
            or candidate["analysis_version"] != candidate["current_analysis_version"]):
        raise ValueError("Defensive candidate belongs to an older game analysis")


def dismiss_defense_candidate(candidate_id: str, *, write_database=None) -> None:
    with (nullcontext(write_database) if write_database is not None else connection()) as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if candidate["approved_at"]:
            raise ValueError("Approved exercises cannot be dismissed")
        storage.dismiss_candidate(database, _now(), _now(), candidate_id)


@refresh_game_publications_after_mutation
def _approve_in_transaction(database, candidate, *, reports_verified: bool = False,
                            prepared_position_fen: str | None = None,
                            admission_mode: str = "manual") -> str:
    _require_current(candidate)
    candidate_id = candidate["id"]
    from ..card_deletion import require_card_not_deleted
    require_card_not_deleted(database, candidate["card_id"] or hashlib.sha256(f"defense\0{candidate_id}".encode()).hexdigest())
    if candidate["validation_state"] not in {"engine_supported", "validated_control"}:
        raise ValueError("Only a validated candidate can enter training")
    if not reports_verified:
        exercise = _exercise_from_rows(database, candidate)
        if recognition_preview(exercise.anchor, exercise.seed, exercise.evaluated_reports[0].lines[0]) is None:
            raise ValueError("This defensive exercise needs a validated board preview")
    if candidate["dismissed_at"]:
        raise ValueError("Dismissed candidate must be revisited after new evidence")
    if candidate["card_id"]:
        storage.clear_card_validation(database, candidate["exercise_revision"], candidate["card_id"])
        storage.retain_approval(database, _now(), admission_mode, _now(), candidate_id)
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
    storage.ensure_defense_repertoire(
        database, DEFENSE_REPERTOIRE_ID, "Defensive tactics", "Your analyzed games", today,
    )
    storage.insert_defense_card(
        database, card_id, DEFENSE_REPERTOIRE_ID, board.fen(), today, candidate_id, anchor.position.start_fen,
        anchor.position.learner_color, today, candidate["exercise_revision"],
    )
    storage.link_approved_card(database, _now(), card_id, admission_mode, _now(), candidate_id)
    return card_id


def approve_defense_candidate(candidate_id: str, *, write_database=None) -> str:
    with (nullcontext(write_database) if write_database is not None else connection()) as database:
        return _approve_in_transaction(database, _candidate(database, candidate_id))


def pause_defense_candidate(candidate_id: str, paused: bool, *, write_database=None) -> None:
    with (nullcontext(write_database) if write_database is not None else connection()) as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        if candidate["approved_at"]:
            raise ValueError("A card already in training cannot be paused here")
        storage.pause_candidate(database, _now() if paused else None, _now(), candidate_id)


def train_defense_candidate_now(candidate_id: str, *, write_database=None) -> str:
    with (nullcontext(write_database) if write_database is not None else connection()) as database:
        candidate = _candidate(database, candidate_id)
        _require_current(candidate)
        card_id = _approve_in_transaction(database, candidate, admission_mode="explicit")
        if postgres_store.configured():
            lock_queue_date_for_position(database, date.today().isoformat())
        ensure_card_queued_after(database, card_id, after_cards=0,
                                 attempt_state="guided", priority_reason="Discovery · defensive recognition")
        storage.stamp_explicit_admission(database, f"defense:{candidate_id}", date.today().isoformat(), card_id)
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
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        limit = storage.daily_limit(database)[0]
        admitted_today = storage.admitted_today(database, day)[0]
        if admitted_today >= limit:
            return False
        admitted_controls_today = storage.admitted_controls_today(database, day)[0]
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
        row = storage.admission_candidate(database, recent_cutoff, selected_state, cursor)
        if row is None and phase == "control":
            phase, cursor = "threat", 0
            control_exhausted = True
            row = storage.admission_candidate(database, recent_cutoff, "engine_supported", cursor)
        candidate = dict(row) if row else None
        report_rows = [dict(report_row) for report_row in storage.admission_reports(
            database, candidate["id"],
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
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return False
        else:
            lease = storage.task_lease(database, task["id"])
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return True
        limit = storage.daily_limit(database)[0]
        admitted_today = storage.admitted_today(database, day)[0]
        same_incident = storage.approved_incident(database, candidate["incident_id"])
        same_position = storage.existing_defense_position(database, candidate_position, candidate_position)
        refreshed = _candidate(database, candidate["id"])
        from ..card_deletion import is_card_deleted
        excluded = is_card_deleted(database, hashlib.sha256(f"defense\0{candidate['id']}".encode()).hexdigest())
        if (not excluded and reports_verified and (candidate["card_id"] or not same_incident and not same_position)
                and refreshed
                and refreshed["validation_state"] in {"engine_supported", "validated_control"}
                and not refreshed["approved_at"]
                and not refreshed["paused_at"]
                and (candidate["card_id"] or admitted_today < limit)):
            _approve_in_transaction(database, refreshed, reports_verified=True,
                                    prepared_position_fen=board.fen(),
                                    admission_mode="automatic")
            introduced = True
        next_payload = {
            "queue_date": day,
            "phase": "threat" if introduced and phase == "control" else phase,
            "cursor": 0 if introduced else cursor + 1,
            "control_exhausted": control_exhausted,
        }
        if postgres_store.configured():
            if introduced:
                enqueue_compact_postgres_task_in_transaction(
                    database, "daily_queue", "current", {"queue_date": day}, priority=10,
                )
            return advance_task_slice_in_transaction(
                database, task, next_phase=next_payload["phase"],
                next_payload=next_payload,
            )
        storage.advance_task(
            database, json.dumps(next_payload), _now(), _now(), task["id"], task["generation"],
            task["lease_token"],
        )
    if introduced:
        enqueue_task("daily_queue", "current", {"queue_date": day}, priority=10, foreground=False)
    return True


def _exercise_from_rows(database, candidate) -> DefenseExercise:
    evidence = json.loads(candidate["evidence_json"])
    reports = [dict(row) for row in storage.completed_reports(database, candidate["id"])]
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
    if postgres_store.configured():
        storage.lock_card(database, candidate["card_id"])
        queue_day = storage.queue_day(database, queue_entry_id)
        if queue_day is None:
            raise ValueError("This defensive exercise is no longer the active queue entry")
        lock_queue_date_for_position(database, queue_day[0])
        candidate = _candidate(database, candidate["id"])
    previous = storage.attempt(database, attempt_id)
    if previous:
        if (previous["candidate_id"] != candidate["id"] or previous["move_uci"] != move_uci
                or previous["queue_entry_id"] != queue_entry_id):
            raise ValueError("Attempt ID already belongs to a different submission")
        return {**json.loads(previous["grade_json"]), "idempotent": True}
    _require_current(candidate)
    if candidate["exercise_revision"] != exercise_revision:
        raise ValueError("Exercise revision changed; reload before trying again")
    entry = storage.queued_entry(database, queue_entry_id, candidate["card_id"])
    if not entry:
        raise ValueError("This defensive exercise is no longer the active queue entry")
    now = datetime.now(timezone.utc)
    today = date.today()
    correct = response["status"] == "correct"
    previous_order = capture_daily_queue_order(database, today.isoformat())
    scheduling = apply_scheduling_review(
        database, candidate["card_id"], "correct" if correct else "again",
        guided=False, source_kind="study", source_ref=f"defense:{attempt_id}",
        light_first_interval_days=light_first_interval_days,
        reviewed_at=now, review_day=today,
    )
    storage.complete_queue_entry(database, "clean" if correct else "again", json.dumps(scheduling), queue_entry_id)
    if scheduling["requeue_today"]:
        if scheduling["requeue_after_cards"] is None:
            next_cycle = storage.next_cycle(database, today.isoformat(), candidate["card_id"])[0]
            final_position = storage.final_position(database, today.isoformat())[0]
            storage.append_reinforcement(
                database, today.isoformat(), candidate["card_id"], next_cycle, final_position, "reinforcement",
            )
        else:
            ensure_card_queued_after(
                database, candidate["card_id"], scheduling["requeue_after_cards"],
                "reinforcement" if correct else "again",
                preserve_order=False,
            )
    preserve_daily_queue_order(database, today.isoformat(), previous=previous_order)
    storage.record_attempt(
        database, attempt_id, candidate["id"], candidate["card_id"], queue_entry_id, exercise_revision, move_uci,
        json.dumps(response), now.isoformat(),
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
        prior = storage.recognition_submission(database, request.attempt_id)
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["queue_entry_id"] != request.queue_entry_id
                    or prior["exercise_revision"] != request.exercise_revision
                    or prior["answer_json"] != serialized):
                raise ValueError("Recognition attempt ID belongs to a different answer")
            completed = storage.completed_grade(database, request.attempt_id)
            if completed:
                return {**json.loads(completed["grade_json"]), "idempotent": True}
            evidence = json.loads(candidate["evidence_json"])
            source_game = storage.source_game_url(database, candidate["game_id"])
            return {"status": "ready_for_move", "recognition_attempt_id": request.attempt_id,
                    "recognition_correct": bool(storage.recognition_correct(database, request.attempt_id)[0]),
                    "feedback": {"knight_route": [{"from_square": evidence["seed"]["geometry"]["knight_from"],
                                                   "to_square": evidence["seed"]["geometry"]["knight_to"]}],
                                 "fork_geometry": evidence["seed"]["geometry"],
                                 "sound_moves": [], "refutation_uci": json.loads(candidate["validation_json"]).get("refutation_uci", []),
                                 "source_game_id": candidate["game_id"],
                                 "source_game_url": source_game["game_url"] if source_game else None},
                    "idempotent": True}
        queue_entry = storage.active_queue_entry(database, request.queue_entry_id, candidate["card_id"])
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
        source_game = storage.source_game_url(database, candidate["game_id"])
    with nullcontext(write_database) if write_database is not None else connection() as database:
        if postgres_store.configured():
            storage.lock_card(database, candidate["card_id"])
            queue_day = storage.queue_day(database, request.queue_entry_id)
            if queue_day is None:
                raise ValueError("This defensive exercise is no longer active")
            lock_queue_date_for_position(database, queue_day[0])
        prior = storage.recognition_submission(database, request.attempt_id)
        if prior:
            if (prior["candidate_id"] != candidate_id or prior["queue_entry_id"] != request.queue_entry_id
                    or prior["exercise_revision"] != request.exercise_revision
                    or prior["answer_json"] != serialized):
                raise ValueError("Recognition attempt ID belongs to a different answer")
            completed = storage.completed_grade(database, request.attempt_id)
            return ({**json.loads(completed["grade_json"]), "idempotent": True} if completed
                    else {"status": "ready_for_move", "recognition_attempt_id": request.attempt_id,
                          "idempotent": True})
        current = _candidate(database, candidate_id)
        _require_current(current)
        if current["exercise_revision"] != request.exercise_revision:
            raise ValueError("Exercise revision changed; reload before answering")
        if current["validation_state"] != candidate["validation_state"]:
            raise ValueError("Exercise evidence changed; reload before answering")
        storage.record_recognition(
            database, request.attempt_id, candidate_id, request.queue_entry_id, request.exercise_revision,
            serialized, int(correct), _now(),
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
            light_interval = storage.light_interval(database)[0]
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
        prior = storage.attempt(database, attempt_id)
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
            recognition = storage.matching_recognition(
                database, recognition_attempt_id, candidate_id, queue_entry_id, exercise_revision,
            )
            if not recognition:
                raise ValueError("Recognition answer is missing or stale")
        exercise = _exercise_from_rows(database, candidate)
        source_game = storage.source_game_url(database, candidate["game_id"])
    grade = grade_defense_move(exercise, move_uci)
    if grade.status == "needs_analysis" and grade.analysis_request:
        request = grade.analysis_request
        with nullcontext(write_database) if write_database is not None else connection() as database:
            current = _candidate(database, candidate_id)
            _require_current(current)
            if current["exercise_revision"] != exercise_revision:
                raise ValueError("Exercise revision changed; reload before trying again")
            storage.retain_analysis_request(database, request.request_id, json.dumps(asdict(request)), _now(), _now())
            storage.retain_attempt_request(database, candidate_id, request.request_id)
            from .defensive_analysis import analysis_enabled
            diagnostic = (grade.diagnostic if analysis_enabled(database) else
                          "Defensive analysis is paused. Enable Defensive analysis in Settings to grade this move.")
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
