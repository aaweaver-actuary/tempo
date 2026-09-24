import io
import threading
import time

from fastapi.testclient import TestClient

from app import database
from app.main import app, coordinator
from app.services.activity_gate import activity_gate
from app.services.durable_tasks import (
    claim_task, complete_task, enqueue_task, requeue_interrupted_tasks,
)


def test_external_worker_gate_yields_to_foreground_and_replays_bounded_task(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    monkeypatch.setenv("TEMPO_COORDINATOR_MODE", "external")
    with TestClient(app) as client:
        assert coordinator._task is None
        queued = enqueue_task("external-test", "one-slice", {"value": 1})
        browser_active = threading.Event()
        browser_active.set()
        entered_slice = threading.Event()

        def foreground_response(request, timeout):
            assert request.get_header("X-tempo-work-class") == "background"
            return io.BytesIO(b'{"active":true}' if browser_active.is_set() else b'{"active":false}')

        monkeypatch.setattr("app.services.activity_gate.urlopen", foreground_response)
        monkeypatch.setenv("TEMPO_FOREGROUND_ACTIVITY_URL", "http://api/foreground-active")

        def background_slice():
            with activity_gate.background_database_section():
                entered_slice.set()

        worker = threading.Thread(target=background_slice)
        worker.start()
        try:
            assert not entered_slice.wait(timeout=0.1)
            started = time.monotonic()
            assert client.get("/api/settings").status_code == 200
            assert time.monotonic() - started < 0.5
        finally:
            browser_active.clear()
            worker.join(timeout=2)
        assert entered_slice.is_set()
        first_lease = claim_task("external-test")
        assert first_lease and first_lease["id"] == queued["id"]
        requeue_interrupted_tasks()
        replay_lease = claim_task("external-test")
        assert replay_lease and replay_lease["id"] == queued["id"]
        assert replay_lease["lease_token"] != first_lease["lease_token"]
        assert not complete_task(first_lease["id"], first_lease["generation"], first_lease["lease_token"])
        assert complete_task(replay_lease["id"], replay_lease["generation"], replay_lease["lease_token"])
        assert claim_task("external-test") is None
