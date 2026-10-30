"""On-demand refresh policy/HTTP proof; no provider, database or worker starts."""
from datetime import datetime
from types import SimpleNamespace
from contextlib import nullcontext

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.games.feedback.http import create_game_review_router
from app.games.feedback.refresh import refresh_job, refresh_status
from app.product_composition.game_review import CurrentGameReviewOperations, configured_job
from test_product_continuous_sync import configuration
from app.games.feedback.ports import JobState


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_refresh_catches_up_to_now_without_waiting_for_interval(provider):
    config = configuration(provider=provider)
    now = datetime.fromisoformat("2026-10-30T12:34:56.123+00:00").timestamp()
    first = refresh_job(config, None, now, "original", configured_job)
    assert first.window.start_at == datetime.fromisoformat(config.window.start_at).isoformat()
    assert first.window.end_at == "2026-10-30T12:34:56.123000+00:00"
    assert first.purpose == "refresh" and first.configuration_revision == config.revision
    checkpoint = JobState(spec=first, status="complete", window_complete=True)
    caught = refresh_job(config, checkpoint, now+60, "next", configured_job)
    assert caught.window.start_at == "2026-10-28T12:34:56.123000+00:00"
    assert config.window.end_at == configuration(provider=provider).window.end_at


def test_refresh_future_seed_is_actionable_instead_of_an_inverted_window():
    with pytest.raises(ValueError, match="start date"):
        refresh_job(configuration(), None, 0, "future", configured_job)


def test_refresh_http_dispatches_original_identity_and_reads_exact_job_without_writer(monkeypatch):
    from app import command_dispatch
    from app.services import durable_tasks
    monkeypatch.setattr(durable_tasks, "task_state", lambda *args: None)
    calls = []
    job = refresh_job(configuration(), None, 1793361600, "request", configured_job)
    status = refresh_status("request", JobState(spec=job))
    operations = CurrentGameReviewOperations()
    monkeypatch.setattr(command_dispatch, "dispatch_command",
        lambda name, payload, **options: calls.append((name, payload, options)) or status.model_dump(mode="json"))
    monkeypatch.setattr(operations, "_operations", lambda: SimpleNamespace(refresh_status=lambda identity: status))
    app = FastAPI(); app.include_router(create_game_review_router(operations))
    with TestClient(app) as client:
        result = client.post("/api/product/game-review/refresh", json={"provider": "lichess", "operation_id": "request"})
        assert result.status_code == 200 and result.json()["job_id"] == job.job_id
        assert client.get("/api/product/game-review/refresh/request").json() == result.json()
        assert client.post("/api/product/game-review/refresh", json={"provider": "unknown", "operation_id": "request"}).status_code == 422
    assert calls == [("product_games.refresh", {"provider": "lichess", "operation_id": "request"}, {"idempotency_key": "request"})]


def test_refresh_background_registration_preserves_study_only_and_foreground_deferral(monkeypatch):
    import sys
    from app import tasks
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from app.services.study_mode import STUDY_TASK_KINDS
    from app.services.durable_scheduling import CLASS_KINDS
    assert "product_game_refresh" in tasks._SUPPORTED_BACKGROUND_KINDS
    assert "product_game_refresh" in CLASS_KINDS["sync"]
    assert "product_game_refresh" not in STUDY_TASK_KINDS
    monkeypatch.setitem(sys.modules, "app.product_composition.game_refresh", None)
    deferred = []
    monkeypatch.setattr(tasks, "measure_handler", lambda *args: nullcontext())
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *args, **kwargs: nullcontext())
    def deny():
        raise BackgroundAdmissionDeferred("foreground activity")
    monkeypatch.setattr(tasks.activity_gate, "check_background_admission", deny)
    monkeypatch.setattr(tasks, "defer_task_for_foreground", lambda task: deferred.append(task))
    task = dict(kind="product_game_refresh", id="original-task", payload={}, generation=1, lease_token="original-lease")
    assert tasks._execute_claimed_background_slice(task, None) is False
    assert deferred == [task]
