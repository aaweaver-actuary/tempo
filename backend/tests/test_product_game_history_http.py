"""Semantic operator/transport checks; no root, provider, DB, Redis or worker."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.contracts.product_v02.games import GameWindow
from app.games.feedback.history import (
    HistoryComparison, HistorySelection, RetainedHistoryGame, RetainedHistoryPage,
)
from app.games.feedback.history_http import HistoryUnavailable, create_game_history_router
from app.product_composition import game_history_runtime as runtime


def selection(**changes):
    values = dict(workspace_id="legacy", provider="lichess", account="Learner",
        window=GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="second"),
        repertoire_ids=("repertoire",), speeds=("rapid",), rated=True)
    return HistorySelection(**dict(values, **changes))


def service(*, receiver=True, maximum_previews=8, admission=True, claim=None):
    events = []

    @contextmanager
    def admitted():
        events.append("admitted")
        yield
        events.append("admission_released")

    @contextmanager
    def read():
        events.append("read")
        yield "reader"

    @contextmanager
    def write():
        events.append("write")
        yield "writer"
        events.append("commit")

    class Reader:
        def page(self, request):
            events.append(("page", request))
            return RetainedHistoryPage((RetainedHistoryGame("retained-game", False, 2),), False)

    class Comparer:
        projection_available = True

        def compare(self, request, game_id, repertoire_id, detected_at):
            events.append(("compare", detected_at))
            return HistoryComparison(2, (), matches_complete=False)

    result = runtime.RetainedHistoryOperations(workspace_id="legacy", reader=Reader(),
        comparer=Comparer(), read=read, write=write,
        admission=admitted if admission is True else admission,
        receiver=SimpleNamespace(policy_identity="qualified-encounter-exception") if receiver else None,
        clock=lambda: datetime(2026, 10, 27, tzinfo=timezone.utc),
        maximum_previews=maximum_previews, claim=claim)
    return result, events


def client(operations):
    app = FastAPI()
    app.include_router(create_game_history_router(operations))
    return TestClient(app)


def request_body(request=None, run_id="bounded-pass"):
    return dict(run_id=run_id, selection=(request or selection()).model_dump(mode="json"))


def test_construction_does_not_read_write_claim_or_start_work():
    operations, events = service()
    create_game_history_router(operations)
    assert events == []
    assert operations.operation_stopped() is None


def test_preview_is_read_only_and_preserves_unknown_lower_bound_counts():
    operations, events = service()
    response = client(operations).post("/api/product/game-history/preview", json=request_body())
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["manifest_digest"]) == 64
    assert "encounters" not in payload and "detected_at" not in payload
    assert payload["preview"]["matches"] == payload["preview"]["eligible_lapses"] == 0
    assert payload["preview"]["matches_are_lower_bound"]
    assert payload["preview"]["unreported_positions"] == 2
    assert "write" not in events and "read" not in events
    assert events[0] == "admitted" and events[-1] == "admission_released"


def test_start_uses_server_plan_original_detection_and_commits_before_acknowledgment(monkeypatch):
    operations, events = service()
    original = operations.preview(selection=selection(), run_id="bounded-pass")
    received = []

    def enqueue(database, plan, *, receiver, selection):
        assert database == "writer" and receiver is operations.receiver
        assert plan.selection == selection
        received.append(plan)
        assert "commit" not in events
        return {"state": "queued"}

    monkeypatch.setattr(runtime, "start_history_in_transaction", enqueue)
    operations.clock = lambda: datetime(2026, 10, 28, tzinfo=timezone.utc)
    payload = dict(request_body(), manifest_digest=original["manifest_digest"])
    response = client(operations).post("/api/product/game-history/start", json=payload)
    assert response.status_code == 202
    assert received[0].detected_at == "2026-10-27T00:00:00+00:00"
    assert response.json() == dict(run_id="bounded-pass", manifest_digest=original["manifest_digest"],
        state="queued", planned_encounters=0, status_url="/api/product/game-history/runs/bounded-pass")
    assert events[-2:] == ["commit", "admission_released"]
    assert "available" not in response.json()


@pytest.mark.parametrize("change", [dict(account="Other"), dict(provider="chesscom"),
    dict(repertoire_ids=("another-repertoire",)), dict(maximum_positions=1)])
def test_start_rejects_scope_changed_after_preview_without_a_write(change):
    operations, events = service()
    prepared = operations.preview(selection=selection(), run_id="bounded-pass")
    with pytest.raises(HistoryUnavailable, match="selection_changed"):
        operations.start(selection=selection(**change), run_id="bounded-pass",
                         manifest_digest=prepared["manifest_digest"])
    assert "write" not in events


def test_browser_cannot_supply_or_replace_executable_encounters():
    operations, events = service()
    payload = dict(request_body(), manifest_digest="a"*64, encounters=[{"observation": "missed"}])
    response = client(operations).post("/api/product/game-history/start", json=payload)
    assert response.status_code == 422 and events == []


def test_workspace_is_bound_before_any_source_access():
    operations, events = service()
    response = client(operations).post("/api/product/game-history/preview",
                                      json=request_body(selection(workspace_id="other-workspace")))
    assert response.status_code == 409 and events == []


def test_missing_owner_allows_only_read_only_preview():
    operations, events = service(receiver=False)
    prepared = operations.preview(selection=selection(), run_id="bounded-pass")
    assert prepared["preview"]["execution_ready"] is False
    with pytest.raises(HistoryUnavailable, match="receiving_owner_unavailable"):
        operations.start(selection=selection(), run_id="bounded-pass",
                         manifest_digest=prepared["manifest_digest"])
    with pytest.raises(HistoryUnavailable, match="receiving_owner_unavailable"):
        operations.run_ready(maximum_units=1)
    assert "write" not in events


def test_evicted_or_restarted_unstarted_preview_requires_preview_again():
    operations, events = service(maximum_previews=1)
    first = operations.preview(selection=selection(), run_id="first")
    operations.preview(selection=selection(), run_id="second")
    with pytest.raises(HistoryUnavailable, match="preview_required"):
        operations.start(selection=selection(), run_id="first", manifest_digest=first["manifest_digest"])
    assert "write" not in events


def test_status_reads_actual_receipt_fields_without_owning_queue_work(monkeypatch):
    operations, events = service()
    stored = dict(run_id="durable-run", state="complete", processed_encounters=1,
        receipts=[dict(interface_version="0.2.1", observation="missed", observation_recorded=True,
            disposition="first_introduction", admission_status="pending", deferred_reason="active_attempt")])

    def status(database, *, workspace_id, run_id):
        assert (database, workspace_id, run_id) == ("reader", "legacy", "durable-run")
        return stored

    monkeypatch.setattr(runtime, "history_run_status", status)
    response = client(operations).get("/api/product/game-history/runs/durable-run")
    assert response.status_code == 200 and response.json() == stored
    assert events == ["admitted", "read", "admission_released"]
    assert operations.operation_stopped() is None


@pytest.mark.parametrize("route", ["run-ready", "resume"])
def test_explicit_runner_resumes_only_approved_history_queue_and_binds_actual_claim(monkeypatch, route):
    claims = iter([{"kind": runtime.KIND, "id": "approved-durable-task"}])
    operations, events = service(claim=lambda: next(claims))

    def run_once(*, receiver, write, admission, maximum_units, claim):
        assert maximum_units == 2 and receiver is operations.receiver
        assert operations.operation_stopped() is None
        with admission():
            assert operations.operation_stopped() is None
            assert claim()["kind"] == runtime.KIND
            assert operations.operation_stopped() is False
        assert operations.operation_stopped() is None
        return ("checkpoint",)

    monkeypatch.setattr(runtime, "run_history_once", run_once)
    response = client(operations).post("/api/product/game-history/" + route, json={"maximum_units": 2})
    assert response.status_code == 200
    assert response.json() == dict(scope="approved_retained_history_queue", maximum_units=2,
                                   processed_units=1, slice_results=["checkpoint"])
    assert events == ["admitted", "admission_released"]


def test_kind_scoped_runner_rejects_an_unrelated_claim_without_authorizing_receiver():
    operations, _ = service(claim=lambda: {"kind": "unrelated-work"})
    with operations._admitted():
        with pytest.raises(ValueError, match="unexpected_claim_kind"):
            operations._claim_ready()
        assert operations.operation_stopped() is None


@pytest.mark.parametrize("units", [0, 101, True])
def test_http_enforces_strict_bounded_work_units(units):
    operations, events = service()
    response = client(operations).post("/api/product/game-history/run-ready", json={"maximum_units": units})
    assert response.status_code == 422 and events == []


def test_missing_admission_fails_closed_before_preview_reads_or_writes():
    operations, events = service(admission=None)
    response = client(operations).post("/api/product/game-history/preview", json=request_body())
    assert response.status_code == 503 and events == []


def test_unavailable_existing_admission_is_safe_deferral_before_write():
    from app.services.activity_gate import BackgroundAdmissionDeferred

    @contextmanager
    def unavailable():
        raise BackgroundAdmissionDeferred("private control details must remain private")
        yield

    operations, events = service(admission=unavailable)
    response = client(operations).post("/api/product/game-history/preview", json=request_body())
    assert response.status_code == 503
    assert response.json() == {"detail": "retained_history_admission_deferred"}
    assert events == []


def test_missing_durable_run_is_a_read_only_404(monkeypatch):
    operations, events = service()
    monkeypatch.setattr(runtime, "history_run_status", lambda *args, **kwargs: None)
    response = client(operations).get("/api/product/game-history/runs/unknown")
    assert response.status_code == 404
    assert events == ["admitted", "read", "admission_released"]
