"""Queue owner operations and the transport/review consumers borrowing them."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from tempo_learning_runtime import api, durable_tasks, queue_application, remediation_adapter, review_adapter
from tempo_learning_runtime.config import Configuration


def test_review_refresh_effects_preserve_daily_remediation_projection_lock_order(monkeypatch):
    database = object()
    events = []

    def daily(borrowed, kind, subject, payload, *, priority):
        assert borrowed is database
        assert (kind, subject, priority) == ("daily_queue", "current", 10)
        events.append(("daily", dict(payload)))

    def remediation(borrowed, day):
        assert borrowed is database
        events.append(("remediation", day))

    def projection(borrowed, day):
        assert borrowed is database
        events.append(("projection", day))

    monkeypatch.setattr(durable_tasks, "enqueue_task_in_transaction", daily)
    monkeypatch.setattr(remediation_adapter, "request_remediation", remediation)
    monkeypatch.setattr(queue_application, "mark_queue_refresh_pending_in_transaction", projection)
    adapter = review_adapter.LearningReviewAdapter(database, None)
    adapter.queue_refreshes = {"2026-10-20": {"preserve_through_entry_id": 12}, "2026-10-19": {}}
    adapter.remediation_days = {"2026-10-20"}
    adapter.finish_compatibility_effects()
    assert events == [
        ("daily", {"queue_date": "2026-10-19"}),
        ("daily", {"queue_date": "2026-10-20", "preserve_through_entry_id": 12}),
        ("remediation", "2026-10-20"),
        ("projection", "2026-10-19"),
        ("projection", "2026-10-20"),
    ]


def test_refresh_request_uses_public_projection_operation_on_borrowed_transaction(monkeypatch):
    database = object()
    events = []
    task = {"id": 7}

    def enqueue(borrowed, kind, subject, payload, *, priority):
        assert borrowed is database
        assert (kind, subject, priority) == ("daily_queue", "current", 10)
        events.append(("daily", payload))
        return task

    def remediation(borrowed, day):
        assert borrowed is database
        events.append(("remediation", day))

    def projection(borrowed, day):
        assert borrowed is database
        events.append(("projection", day))

    monkeypatch.setattr(queue_application, "enqueue_task_in_transaction", enqueue)
    monkeypatch.setattr(remediation_adapter, "request_remediation", remediation)
    monkeypatch.setattr(queue_application, "mark_queue_refresh_pending_in_transaction", projection)
    assert queue_application.request_queue_refresh_in_transaction(
        database, "2026-10-20", preserve_through_entry_id=12
    ) is task
    assert events == [
        ("daily", {"queue_date": "2026-10-20", "preserve_through_entry_id": 12}),
        ("remediation", "2026-10-20"), ("projection", "2026-10-20"),
    ]


@pytest.mark.parametrize("issued", [True, False])
def test_queue_http_window_consumes_semantic_projection_and_retains_head_issuance(monkeypatch, issued):
    database = object()
    calls = []
    cards = [{"id": "card-a", "queue_entry_id": 13, "revision": 2, "moves": ["e2e4"]}]
    projection = dict(queue_date="2026-10-20", state="ready", generation=4,
                      updated_at=None, refresh_pending=0, last_error=None)
    window = queue_application.QueueWindowProjection(cards, 28, projection)
    monkeypatch.setattr(api, "Database", lambda configuration: object())
    monkeypatch.setattr(api, "connection", lambda **options: nullcontext(database))
    monkeypatch.setattr(api, "safe_run_once", lambda: None)
    monkeypatch.setattr(api, "date", SimpleNamespace(today=lambda: SimpleNamespace(isoformat=lambda: "2026-10-20")))

    def forbidden_sql():
        raise AssertionError("HTTP queue projection must not consume SQL fragments")

    def read_window(borrowed, day, *, limit):
        assert borrowed is database
        assert (day, limit) == ("2026-10-20", 7)
        calls.append("read")
        return window

    def dispatch(name, payload, **options):
        calls.append((name, payload))
        return {"issued": issued}

    monkeypatch.setattr(queue_application, "available_attempt_sql", forbidden_sql)
    monkeypatch.setattr(queue_application, "read_queue_window", read_window)
    monkeypatch.setattr(api.gateway, "dispatch", dispatch)
    monkeypatch.setattr(api.evidence, "queue_manifests", lambda value: calls.append(("evidence", value)))
    app = api.create_app(Configuration("test", "postgresql://unused/learning", "postgresql://unused/learning", "x" * 40,
                                       maintenance=False, host_activity_directory=None))
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/queue/window")
    if not issued:
        with pytest.raises(HTTPException, match="Queue changed before issuance") as error:
            endpoint(limit=7, include_opening_evidence=True)
        assert error.value.status_code == 409
        assert not any(isinstance(call, tuple) and call[0] == "evidence" for call in calls)
    else:
        result = endpoint(limit=7, include_opening_evidence=True)
        assert result == dict(local_date="2026-10-20", cards=cards, count=28, diagnostics=[], projection=projection)
        assert calls[-1] == ("evidence", cards)
    assert calls[:3] == [
        ("queue.ensure_current", {"queue_date": "2026-10-20"}), "read",
        ("queue.issue_head", {"queue_date": "2026-10-20", "entry_id": 13, "card_id": "card-a", "expected_revision": 2}),
    ]


class ScriptedDatabase:
    def __init__(self, results):
        self.results = iter(results)
        self.parameters = []
        self.statements = []

    def execute(self, statement, parameters):
        self.parameters.append(parameters)
        self.statements.append(statement)
        result = next(self.results)
        return SimpleNamespace(fetchone=lambda: result, fetchall=lambda: result)


def test_queue_owner_projects_legacy_cards_review_identity_and_typed_sources():
    rows = [
        dict(id="opening", queue_entry_id=1, revision=2, content_type="opening", moves_json='["e2e4"]', private_metadata="hidden"),
        dict(id="defense", queue_entry_id=2, revision=3, content_type="defense", moves_json="[]", source_ref="obsolete"),
        dict(id="study", queue_entry_id=3, revision=4, content_type="study_exercise", moves_json="[]", study_exercise_id="exercise", start_fen="fen"),
    ]
    projection = dict(state="ready", refresh_pending=0)
    db = ScriptedDatabase([rows, (17,), (18,), ("accepted-candidate",), (19,), ('{"type":"move_line"}',), projection, (8,)])
    window = queue_application.read_queue_window(db, "2026-10-20", limit=3)
    assert window.available_count == 8
    assert window.projection == projection
    assert [card["id"] for card in window.cards] == ["opening", "defense", "study"]
    assert [card["latest_review_id"] for card in window.cards] == [17, 18, 19]
    assert window.cards[0]["moves"] == ["e2e4"]
    assert "moves_json" not in window.cards[0] and "private_metadata" not in window.cards[0]
    assert window.cards[1]["source_ref"] == "accepted-candidate"
    assert window.cards[2]["study_snapshot"] == dict(schema_version=1, grader_version=1, exercise_id="exercise", revision=4, fen="fen", specification={"type": "move_line"})
    assert db.parameters == [("2026-10-20", 3), ("opening",), ("defense",), ("defense", 3), ("study",), ("exercise", 4), ("2026-10-20",), ("2026-10-20",)]


def test_queue_owner_empty_window_retains_count_and_absent_projection():
    db = ScriptedDatabase([[], None, (0,)])
    assert queue_application.read_queue_window(db, "2026-10-20", limit=20) == queue_application.QueueWindowProjection([], 0, None)


@pytest.mark.parametrize("limit", [-1, 0, 201])
def test_queue_owner_rejects_unbounded_window_before_reading(limit):
    with pytest.raises(ValueError, match="between 1 and 200"):
        queue_application.read_queue_window(object(), "2026-10-20", limit=limit)


def test_queue_owner_preserves_bounded_legacy_today_and_prepared_window():
    db = ScriptedDatabase([[], None, (0,)])
    queue_application.read_queue_window(db, "2026-10-20", limit=200)
    assert db.parameters[0] == ("2026-10-20", 200)


def test_queue_refresh_projection_marks_only_the_borrowed_transaction():
    db = ScriptedDatabase([None])
    assert queue_application.mark_queue_refresh_pending_in_transaction(db, "2026-10-20") is None
    assert db.parameters == [("2026-10-20",)]
