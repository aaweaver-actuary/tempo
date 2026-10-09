"""Bounded rehearsal admission cannot hide diagnostic failures."""
from unittest.mock import Mock

import httpx
import pytest

from scripts.check_postgres_opening_segmentation import idle_prefix_response
from scripts import check_postgres_opening_segmentation as rehearsal
from app.services import redis_admission_gate


def test_prefix_rehearsal_replay_waits_only_for_documented_foreground_admission(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: False)
    busy = httpx.Response(503, headers={"Retry-After": "1"}, json={"detail": {
        "code": "evaluation_busy", "message": "Study work is active. Retry the diagnostic when study is idle."}})
    stale = httpx.Response(409, json={"detail": {"code": "stale_snapshot"}})
    client = Mock()
    client.request.side_effect = [busy, stale]
    assert idle_prefix_response(client, "POST", "/evaluate", json={"snapshot_id": "saved"}) is stale
    assert client.request.call_count == 2
    client.request.assert_called_with("POST", "/evaluate", json={"snapshot_id": "saved"})
    failure = httpx.Response(503, json={"detail": {"code": "database_unavailable"}})
    client.request.side_effect = [failure]
    assert idle_prefix_response(client, "GET", "/source") is failure


def test_prefix_rehearsal_admission_is_bounded_and_retains_retry_header_contract(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: False)
    busy = httpx.Response(503, json={"detail": {
        "code": "evaluation_busy", "message": "Study work is active. Retry the diagnostic when study is idle."}})
    client = Mock()
    client.request.return_value = busy
    with pytest.raises(AssertionError):
        idle_prefix_response(client, "GET", "/source")
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: True)
    clock = iter([0, 0, 11])
    monkeypatch.setattr("scripts.check_postgres_opening_segmentation.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("scripts.check_postgres_opening_segmentation.time.sleep", lambda _: None)
    client.reset_mock()
    with pytest.raises(AssertionError, match="within 10 seconds"):
        idle_prefix_response(client, "GET", "/source")
    client.request.assert_not_called()


def test_transition_rehearsal_source_waits_for_foreground_admission_before_reading_snapshot(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: False)
    busy = httpx.Response(503, headers={"Retry-After": "1"}, json={"detail": {
        "code": "evaluation_busy", "message": "Study work is active. Retry the diagnostic when study is idle."}})
    ready = httpx.Response(200, json={"snapshot_id": "saved"})
    client = Mock()
    client.get.return_value = busy
    client.request.side_effect = [busy, ready]
    monkeypatch.setattr("fastapi.testclient.TestClient", lambda _app: client)

    # The regular PostgreSQL rehearsal covers the real Redis wrapper.
    # This bounded unit case checks source responses before database work.
    monkeypatch.setattr(rehearsal, "test_issue79_transition_admission_isolation_preserves_foreground_priority",
                        lambda *_arguments: None)

    def stop_after_source():
        raise RuntimeError("source admitted; stop before database rehearsal")

    with pytest.raises(RuntimeError, match="source admitted"):
        rehearsal.test_issue79_readonly_planner_foreground_concurrency_and_stale_replay.__wrapped__(
            "rep", [{"id": "line"}], [], stop_after_source)
    assert client.request.call_count == 2
    client.request.assert_called_with("GET", "/api/repertoires/rep/prefix-evaluation/source")


def test_transition_rehearsal_source_reports_database_failure_without_snapshot_key_error(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: False)
    failure = httpx.Response(503, json={"detail": {"code": "database_unavailable"}})
    client = Mock()
    client.get.return_value = failure
    client.request.return_value = failure
    monkeypatch.setattr("fastapi.testclient.TestClient", lambda _app: client)
    with pytest.raises(AssertionError, match="database_unavailable"):
        rehearsal.test_issue79_readonly_planner_foreground_concurrency_and_stale_replay.__wrapped__(
            "rep", [{"id": "line"}], [], Mock())
    client.request.assert_called_once()
