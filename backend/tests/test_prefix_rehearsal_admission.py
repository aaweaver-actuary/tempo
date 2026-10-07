"""Bounded rehearsal admission cannot hide diagnostic failures."""
from unittest.mock import Mock

import httpx
import pytest

from scripts.check_postgres_opening_segmentation import idle_prefix_response
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
