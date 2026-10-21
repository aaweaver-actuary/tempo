"""Study/tactic decisions retain identity, revision and scheduling safeguards."""
from datetime import date, datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import chess
from fastapi import HTTPException
import pytest

from tempo_learning_runtime import study_attempts as completion, study_attempt_commands as commands
from tempo_learning_runtime import tactic_commands as tactics


def study_payload():
    return dict(study_id="study", exercise_id="exercise", attempt=dict(
        attempt_id="attempt", revision=2, context="practice",
        answer=dict(type="move_line", moves=["e2e4"])))


def test_practice_completion_records_result_without_scheduling(monkeypatch):
    writes = []
    monkeypatch.setattr(completion.storage, "completion_rows", lambda *_: pytest.fail("Practice touched queue"))
    monkeypatch.setattr(completion, "apply_scheduling_review", lambda *_args, **_kw: pytest.fail("Practice scheduled a card"))
    monkeypatch.setattr(completion.storage, "finalize_attempt", lambda *args: writes.append(args[1:]))
    attempt = dict(id="attempt", context="practice", assessment_json='{"outcome":"correct"}')
    result = completion.finish_study_attempt(None, attempt, "correct")
    assert result == dict(attempt_id="attempt", rating="correct", requested_rating="correct",
                          review=None, assessment=dict(outcome="correct"), persisted=True)
    assert json.loads(writes[0][1]) == result
    assert writes[0][2] == "attempt"
    assert datetime.fromisoformat(writes[0][0]).tzinfo is not None


@pytest.mark.parametrize("guide", [None, "attempt_failed", "hint_seen", "solution_seen_before_answer"])
def test_review_guidance_keeps_requested_rating_requeue_and_completion_order(monkeypatch, guide):
    from tempo_learning_runtime import queue_application
    events = []
    attempt = dict(id="attempt", context="review", card_id="card", exercise_id="exercise", revision=2,
                   queue_entry_id=4, cycle=0, hint_seen=guide == "hint_seen",
                   solution_seen_before_answer=guide == "solution_seen_before_answer", assessment_json='{}')
    monkeypatch.setattr(completion.storage, "completion_rows", lambda *_: (
        dict(id="card", revision=2), dict(id=4, queue_date="2026-10-21", attempt_failed=guide == "attempt_failed")))
    monkeypatch.setattr(completion.storage, "light_interval_days", lambda *_: (3,))
    def schedule(_db, card_id, rating, **context):
        assert (card_id, rating, context["guided"]) == ("card", "again" if guide else "correct", bool(guide))
        assert (context["source_ref"], context["light_first_interval_days"]) == ("attempt", 3)
        events.append("schedule")
        return dict(requeue_today=True, requeue_after_cards=2)
    monkeypatch.setattr(completion, "apply_scheduling_review", schedule)
    def requeue(_db, day, card_id, after, state):
        assert (day, card_id, after, state) == ("2026-10-21", "card", 2, "guided" if guide else "reinforcement")
        events.append("requeue")
        return 9
    monkeypatch.setattr(queue_application, "requeue", requeue)
    monkeypatch.setattr(completion.storage, "complete_queue", lambda _db, state, _entry:
                        events.append(("complete", state)))
    monkeypatch.setattr(completion.storage, "latest_review_id", lambda *_: events.append("review id") or 7)
    monkeypatch.setattr(completion.storage, "finalize_attempt", lambda *_: events.append("finalize"))
    monkeypatch.setattr(completion.storage, "retain_queue_result", lambda *_: events.append("queue result"))
    monkeypatch.setattr(completion.storage, "exercise", lambda *_: dict(id="exercise"))
    monkeypatch.setattr(completion.storage, "sibling_exercises", lambda *_: [("sibling",)])
    monkeypatch.setattr(completion.storage, "record_sibling_burial", lambda _db, sibling, _day, reason:
                        events.append(("burial", sibling, reason)))
    monkeypatch.setattr(completion.storage, "block_sibling_queue", lambda _db, _day, sibling:
                        events.append(("block", sibling)))
    result = completion.finish_study_attempt(None, attempt, "correct")
    assert result["requested_rating"] == "correct"
    assert (result["review"]["review_id"], result["review"]["requeue_entry_id"]) == (7, 9)
    assert events == ["schedule", "requeue", ("complete", "guided" if guide else "clean"),
                      "review id", "finalize", "queue result", ("burial", "sibling", "answer exposure"), ("block", "sibling")]


def test_changed_completion_definition_rejects_before_schedule_or_write(monkeypatch):
    monkeypatch.setattr(completion.storage, "completion_rows", lambda *_: (dict(revision=3), dict(id=4)))
    monkeypatch.setattr(completion.storage, "light_interval_days", lambda *_: pytest.fail("Changed definition read settings"))
    with pytest.raises(HTTPException) as error:
        completion.finish_study_attempt(None, dict(context="review", revision=2), "correct")
    assert error.value.status_code == 409


@pytest.mark.parametrize("conflict", [False, True])
def test_study_identity_replay_precedes_current_definition_reads(monkeypatch, conflict):
    payload = study_payload()
    request = commands.StudyAttemptRequest.model_validate(payload["attempt"])
    digest = hashlib.sha256(commands._json(dict(request=request.model_dump(mode="json"))).encode()).hexdigest()
    monkeypatch.setattr(commands.storage, "lock_attempt_identity", lambda *_: None)
    monkeypatch.setattr(commands.storage, "attempt_by_id", lambda *_: dict(
        answer_hash="different" if conflict else digest, exercise_id="exercise", result_json='{"saved":true}'))
    monkeypatch.setattr(commands.storage, "exercise_owner", lambda *_: ("study",))
    monkeypatch.setattr(commands.storage, "exercise_for_update", lambda *_: pytest.fail("Replay read current definition"))
    if conflict:
        with pytest.raises(HTTPException) as error:
            commands.submit_study_attempt(None, payload)
        assert error.value.status_code == 409
    else:
        assert commands.submit_study_attempt(None, payload) == dict(saved=True)


@pytest.mark.parametrize("outcome", ["needs_self_assessment", "correct"])
def test_new_study_answer_records_before_finish_and_refresh(monkeypatch, outcome):
    events = []
    payload = study_payload()
    monkeypatch.setattr(commands.storage, "lock_attempt_identity", lambda *_: events.append("identity lock"))
    reads = iter([None, dict(id="persisted")])
    monkeypatch.setattr(commands.storage, "attempt_by_id", lambda *_: next(reads))
    monkeypatch.setattr(commands.storage, "exercise_for_update", lambda *_: dict(
        id="exercise", study_id="study", status="published", current_revision=2, position_id="position"))
    monkeypatch.setattr(commands.storage, "serving_exercise_card", lambda *_: (1,))
    monkeypatch.setattr(commands.storage, "exercise_position", lambda *_: dict(fen=chess.STARTING_FEN))
    monkeypatch.setattr(commands.storage, "revision_specification", lambda *_: (json.dumps(dict(
        type="move_line", prompt="Move", accepted_lines=[["e2e4"]])),))
    monkeypatch.setattr(commands, "evaluate_answer", lambda *_: dict(outcome=outcome))
    def record(_db, **fields):
        assert fields["assessment_method"] == ("self_assessed" if outcome == "needs_self_assessment" else "automatic")
        assert fields["revision"] == 2 and fields["started_at"] == fields["committed_at"]
        assert json.loads(fields["answer_json"]) == dict(type="move_line", moves=["e2e4"])
        events.append("record")
    monkeypatch.setattr(commands.storage, "record_attempt", record)
    def finish(_db, attempt, rating):
        assert (attempt["id"], rating) == ("persisted", "correct")
        events.append("finish")
        return dict(persisted=True)
    monkeypatch.setattr(commands, "finish_study_attempt", finish)
    monkeypatch.setattr(commands, "request_queue_refresh_in_transaction", lambda *_: events.append("refresh"))
    result = commands.submit_study_attempt(None, payload)
    if outcome == "needs_self_assessment":
        assert result["pending_self_assessment"] and events == ["identity lock", "record"]
    else:
        assert result["persisted"] and events == ["identity lock", "record", "finish", "refresh"]


@pytest.mark.parametrize("guard", ["already_answered", "newer_review"])
def test_review_queue_locks_precede_existing_answer_and_review_fences(monkeypatch, guard):
    events = []
    request = SimpleNamespace(card_id="card", queue_entry_id=4, queue_cycle=0, revision=2, expected_review_id=7)
    monkeypatch.setattr(commands.storage, "review_card_for_update", lambda *_: events.append("card") or dict(id="card", revision=2))
    monkeypatch.setattr(commands.storage, "review_queue_date", lambda *_: events.append("day") or ("2026-10-21",))
    monkeypatch.setattr(commands, "lock_queue_date_for_position", lambda *_: events.append("position lock"))
    monkeypatch.setattr(commands.storage, "review_queue_for_update", lambda *_: events.append("queue") or dict(id=4))
    monkeypatch.setattr(commands.storage, "existing_queue_answer", lambda *_: events.append("answer") or ((1,) if guard == "already_answered" else None))
    monkeypatch.setattr(commands.storage, "latest_review_id", lambda *_: events.append("latest") or 8)
    with pytest.raises(HTTPException) as error:
        commands._lock_review_queue(None, request, "exercise")
    assert error.value.status_code == 409
    assert events == ["card", "day", "position lock", "queue", "answer"] + (["latest"] if guard == "newer_review" else [])


@pytest.mark.parametrize("guard", ["retired", "revision"])
def test_self_assessment_retirement_and_revision_fences_precede_finish(monkeypatch, guard):
    monkeypatch.setattr(commands.storage, "pending_assessment_attempt", lambda *_: dict(context="practice"))
    monkeypatch.setattr(commands.storage, "assessment_attempt_for_update", lambda *_: dict(
        result_json=None, retired_operation_id="retired" if guard == "retired" else None, revision=2))
    monkeypatch.setattr(commands.storage, "assessment_exercise", lambda *_: dict(study_id="study", current_revision=3))
    monkeypatch.setattr(commands, "finish_study_attempt", lambda *_: pytest.fail("Stale attempt finalized"))
    with pytest.raises(HTTPException) as error:
        commands.self_assess_study_attempt(None, dict(attempt_id="attempt", exercise_id="exercise", study_id="study", assessment=dict(rating="correct")))
    assert error.value.status_code == 409


@pytest.mark.parametrize("rating", ["correct", "again"])
def test_self_assessment_replay_uses_requested_rating_before_current_revision(monkeypatch, rating):
    result = dict(rating="again", requested_rating="correct")
    monkeypatch.setattr(commands.storage, "pending_assessment_attempt", lambda *_: dict(context="practice"))
    monkeypatch.setattr(commands.storage, "assessment_attempt_for_update", lambda *_: dict(result_json=json.dumps(result)))
    monkeypatch.setattr(commands.storage, "assessment_exercise", lambda *_: dict(study_id="study"))
    payload = dict(attempt_id="attempt", exercise_id="exercise", study_id="study", assessment=dict(rating=rating))
    if rating == "correct":
        assert commands.self_assess_study_attempt(None, payload) == result
    else:
        with pytest.raises(HTTPException) as error:
            commands.self_assess_study_attempt(None, payload)
        assert error.value.status_code == 409


@pytest.fixture
def tactic_context(monkeypatch):
    from tempo_learning_runtime import card_deletion
    events = []
    monkeypatch.setattr(tactics, "date", SimpleNamespace(today=lambda: date(2026, 10, 21)))
    monkeypatch.setattr(tactics, "datetime", SimpleNamespace(now=lambda _tz: datetime(2026, 10, 21, 12, tzinfo=timezone.utc)))
    monkeypatch.setattr(card_deletion, "require_card_not_deleted", lambda *_: events.append("deletion"))
    monkeypatch.setattr(tactics, "lock_daily_tactic_admission", lambda *_: events.append("day lock"))
    monkeypatch.setattr(tactics.storage, "lock_puzzle", lambda *_: events.append("puzzle lock"))
    payload = dict(pack_id="pack", training_fen=chess.STARTING_FEN, solution=["e2e4"], request=dict(
        attempt_id="attempt", puzzle_id="puzzle", deck_id="deck", correct=True, clean=True,
        source_fen=chess.STARTING_FEN, moves=["e2e4"]))
    return payload, events


def test_tactic_receipt_replay_precedes_progress_and_scheduling(monkeypatch, tactic_context):
    payload, events = tactic_context
    monkeypatch.setattr(tactics.storage, "attempt_result", lambda *_: ('{"retained":true}',))
    monkeypatch.setattr(tactics.storage, "light_interval_days", lambda *_: pytest.fail("Replay read scheduling settings"))
    assert tactics.submit_tactic_attempt(None, payload) == dict(retained=True)
    assert events == ["deletion", "day lock", "puzzle lock"]


@pytest.mark.parametrize("correct,clean,queued", [(True, True, False), (True, False, False), (False, False, False), (False, False, True)])
def test_tactic_clean_due_date_and_failure_requeue_decisions_stay_in_application(monkeypatch, tactic_context, correct, clean, queued):
    payload, events = tactic_context
    payload["request"].update(correct=correct, clean=clean)
    monkeypatch.setattr(tactics.storage, "attempt_result", lambda *_: None)
    monkeypatch.setattr(tactics.storage, "light_interval_days", lambda *_: 3)
    monkeypatch.setattr(tactics.storage, "ensure_tactic_repertoire", lambda *_: events.append("repertoire"))
    def progress(_db, _puzzle, pack, _card, clean_at, _now, mode):
        assert pack == "pack" and bool(clean_at) == (correct and clean)
        assert mode == ("light" if correct and clean else "normal")
        events.append("progress")
    monkeypatch.setattr(tactics.storage, "record_progress", progress)
    def card(_db, _card, _fen, _moves, due, _type, mode, *_rest):
        assert due == ("2026-10-24" if correct and clean else "2026-10-21")
        assert mode == ("light" if correct and clean else "normal")
        events.append("card and row lock")
    monkeypatch.setattr(tactics.storage, "ensure_tactic_card", card)
    monkeypatch.setattr(tactics.storage, "restore_normal_learning", lambda *_: events.append("normal"))
    monkeypatch.setattr(tactics, "lock_queue_date_for_position", lambda *_: events.append("queue lock"))
    monkeypatch.setattr(tactics.storage, "queued_attempt", lambda *_: (1,) if queued else None)
    def enqueue(_db, _card, after, state):
        assert (after, state) == (4, "guided")
        events.append("guided queue")
    monkeypatch.setattr(tactics, "ensure_card_queued_after", enqueue)
    monkeypatch.setattr(tactics.storage, "saved_schedule", lambda *_: ("retained", "2026-10-25"))
    monkeypatch.setattr(tactics.storage, "retain_admission_mode", lambda _db, mode, _puzzle: events.append(("mode", mode)))
    def receipt(_db, attempt, deck, _puzzle, clean_pass, result_json):
        assert (attempt, deck, clean_pass) == ("attempt", "deck", int(correct and clean))
        assert json.loads(result_json)["mode"] == "retained"
        events.append("receipt")
    monkeypatch.setattr(tactics.storage, "retain_attempt_result", receipt)
    result = tactics.submit_tactic_attempt(None, payload)
    assert (result["mode"], result["next_due"]) == ("retained", "2026-10-25")
    assert events == ["deletion", "day lock", "puzzle lock", "repertoire", "progress", "card and row lock"] + (
        ["normal"] if not correct or not clean else []) + (["queue lock"] if not correct else []) + (
        ["guided queue"] if not correct and not queued else []) + [("mode", "retained"), "receipt"]
