"""Frozen learner transport keeps original Study command and revision identity."""
import json
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import study_exercise_routes as routes
from app.services.study_grading import GRADER_VERSION

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SPEC = {"type": "choice", "prompt": "Choose", "hint": "hint", "explanation": "secret explanation",
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], "correct_option_ids": ["a"]}
BASE = "/api/studies/study/exercises/exercise"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    @contextmanager
    def read():
        yield object()
    monkeypatch.setattr(routes, "read_connection", read)
    return TestClient(app)


def test_present_projects_only_prompt_fields_and_holds_unsupported(client, monkeypatch):
    monkeypatch.setattr(routes.storage, "frozen_presentation", lambda db, study, exercise: {
        "id": exercise, "current_revision": 2, "fen": FEN, "specification_json": json.dumps(SPEC)
    } if (study, exercise) == ("study", "exercise") else None)
    result = client.get(BASE + "/present")
    assert result.status_code == 200
    assert result.json() == {"id": "exercise", "revision": 2, "type": "choice", "prompt": "Choose", "hint": "hint", "fen": FEN, "options": SPEC["options"]}
    assert "correct_option_ids" not in result.text and "secret explanation" not in result.text
    assert client.get("/api/studies/other/exercises/exercise/present").status_code == 404
    monkeypatch.setattr(routes.storage, "frozen_presentation", lambda *args: {"id": "exercise", "current_revision": 2, "fen": FEN, "specification_json": '{"type":"retired"}'})
    assert client.get(BASE + "/present").status_code == 409


def test_attempt_dispatch_preserves_exact_request_and_pending_receipt(client, monkeypatch):
    from app import command_dispatch
    seen = []
    def dispatch(name, payload, *, idempotency_key):
        seen.append((name, payload, idempotency_key))
        return JSONResponse({"operation_id": idempotency_key, "state": "pending"}, status_code=202)
    monkeypatch.setattr(command_dispatch, "dispatch_command", dispatch)
    payload = {"attempt_id": "attempt-original", "revision": 3, "context": "review", "queue_entry_id": 17,
               "queue_cycle": 2, "card_id": "card", "answer": {"type": "choice", "option_ids": ["a"]}}
    first = client.post(BASE + "/attempts", json=payload, headers={"Idempotency-Key": "original-operation"})
    replay = client.post(BASE + "/attempts", json=payload, headers={"Idempotency-Key": "original-operation"})
    assert first.status_code == replay.status_code == 202 and first.json() == replay.json()
    assert seen[0] == seen[1]
    name, envelope, key = seen[0]
    assert name == "studies.attempts.submit" and key == "original-operation"
    assert envelope == {"study_id": "study", "exercise_id": "exercise", "attempt": routes.StudyAttemptRequest.model_validate(payload).model_dump(mode="json")}
    assert GRADER_VERSION == 1 and "provenance" not in envelope["attempt"]


def test_self_assessment_dispatches_original_attempt(client, monkeypatch):
    from app import command_dispatch
    seen = []
    monkeypatch.setattr(command_dispatch, "dispatch_command", lambda name, payload, **kw: seen.append((name, payload, kw)) or {"persisted": True})
    assert client.post(BASE + "/attempts/original/self-assess", json={"rating": "again"}, headers={"Idempotency-Key": "self-original"}).json() == {"persisted": True}
    assert seen == [("studies.attempts.self_assess", {"study_id": "study", "exercise_id": "exercise", "attempt_id": "original", "assessment": {"rating": "again"}}, {"idempotency_key": "self-original"})]


def test_feedback_requires_original_binding_and_returns_original_revision(client, monkeypatch):
    calls = []
    def read(db, study, exercise, attempt):
        calls.append((study, exercise, attempt))
        return {"specification_json": json.dumps(SPEC)} if (study, exercise, attempt) == ("study", "exercise", "old-attempt") else None
    monkeypatch.setattr(routes.storage, "frozen_attempt_feedback", read)
    assert client.get(BASE + "/attempts/old-attempt/feedback").json()["specification"]["correct_option_ids"] == ["a"]
    assert client.get(BASE + "/attempts/foreign/feedback").status_code == 404
    assert client.get("/api/studies/other/exercises/exercise/attempts/old-attempt/feedback").status_code == 404
    assert calls == [("study", "exercise", "old-attempt"), ("study", "exercise", "foreign"), ("other", "exercise", "old-attempt")]


def test_retained_present_requires_exact_issued_or_pending_identity(client, monkeypatch):
    row = {"id": "exercise", "current_revision": 2, "fen": FEN, "specification_json": json.dumps(SPEC)}
    monkeypatch.setattr(routes.storage, "frozen_presentation", lambda *args: None)
    monkeypatch.setattr(routes.storage, "issued_frozen_presentation", lambda db, study, exercise, card, entry, cycle, revision:
                        row if (study, exercise, card, entry, cycle, revision) == ("study", "exercise", "card", 11, 3, 2) else None)
    monkeypatch.setattr(routes.storage, "pending_frozen_presentation", lambda db, study, exercise, attempt:
                        row if (study, exercise, attempt) == ("study", "exercise", "saved-attempt") else None)
    assert client.get(BASE + "/present").status_code == 404
    assert client.get(BASE + "/present?card_id=card&queue_entry_id=11&queue_cycle=3&revision=2").json()["revision"] == 2
    assert client.get(BASE + "/present?card_id=card&queue_entry_id=11&queue_cycle=4&revision=2").status_code == 404
    assert client.get(BASE + "/present?attempt_id=saved-attempt").json()["revision"] == 2
    assert client.get(BASE + "/present?attempt_id=foreign").status_code == 404
