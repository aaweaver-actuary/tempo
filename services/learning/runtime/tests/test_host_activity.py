"""Same-host admission is optional and cannot become a Study dependency."""
from dataclasses import replace
import json
from fastapi.testclient import TestClient
import pytest
from tempo_learning_runtime.api import create_app
from tempo_learning_runtime.host_activity import ACTIVITY_NS, HostActivity


def test_host_activity_sessions_expire_independently_and_clean_shutdown_removes_only_owner(tmp_path):
    now = [100_000_000_000]
    first = HostActivity(tmp_path, clock=lambda: now[0])
    second = HostActivity(tmp_path, clock=lambda: now[0])
    first.record("tab-a")
    now[0] += 1_000_000_000
    first.record("tab-b")
    now[0] += ACTIVITY_NS - 1_000_000_000
    first.record("tab-b")
    snapshot = json.loads((tmp_path / (first.producer + ".json")).read_text())
    assert set(snapshot["sessions"]) == {"tab-b"}
    second.record("other-runtime")
    first.close()
    assert not (tmp_path / (first.producer + ".json")).exists()
    assert (tmp_path / (second.producer + ".json")).exists()
    second.close()


def test_host_activity_failure_retains_last_snapshot_and_never_imports_a_broker(tmp_path, monkeypatch):
    activity = HostActivity(tmp_path)
    activity.record("held")
    retained = (tmp_path / (activity.producer + ".json")).read_bytes()
    monkeypatch.setattr(activity, "_publish", lambda: (_ for _ in ()).throw(OSError("unavailable filesystem")))
    with pytest.raises(OSError):
        activity.record("pending")
    assert (tmp_path / (activity.producer + ".json")).read_bytes() == retained
    activity.close()


def test_host_activity_final_pulse_retains_remaining_quiet_time_instead_of_starting_a_new_lease(tmp_path):
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, clock=lambda: now[0])
    activity.record("released", 3000)
    now[0] += 2_500_000_000
    activity.record("released", 500)
    snapshot = json.loads((tmp_path / (activity.producer + ".json")).read_text())
    assert int(snapshot["sessions"]["released"]) == 103_000_000_000
    activity.close()


def test_host_activity_pending_essential_requests_outlive_browser_leases_and_release_independently(tmp_path):
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, clock=lambda: now[0])
    snapshot = lambda: json.loads((tmp_path / (activity.producer + ".json")).read_text())
    with activity.request():
        with activity.request():
            now[0] += 10_000_000_000
            activity.record("late-pulse", 1)
            assert snapshot()["pendingRequests"] is True
        assert snapshot()["pendingRequests"] is True
    assert snapshot()["pendingRequests"] is False
    assert max(map(int, snapshot()["sessions"].values())) == now[0] + ACTIVITY_NS
    activity.close()


def test_host_activity_endpoint_authenticates_and_needs_no_database(tmp_path):
    from tempo_learning_runtime.config import Configuration
    configuration = Configuration("activity", "postgresql://unused/learning", "postgresql://reader/learning", "x" * 40,
                                  maintenance=False, host_activity_directory=tmp_path)
    app = create_app(configuration)
    with TestClient(app) as client:
        assert client.post("/api/study/activity?session_id=tab-a").status_code == 401
        headers = {"Authorization": "Bearer " + configuration.token, "Origin": configuration.origins[0]}
        assert client.post("/api/study/activity?session_id=tab-a", headers=headers).json()["available"]
        assert client.post("/api/study/activity?session_id=../foreign", headers=headers).status_code == 422
        assert client.post("/api/study/activity?session_id=tab-b", headers={**headers, "Origin": "http://foreign.test"}).status_code == 403
        snapshot = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert set(snapshot["sessions"]) == {"tab-a"}
        assert snapshot["source"] == configuration.workspace_id
    assert not list(tmp_path.glob("*.json"))
    disabled = create_app(replace(configuration, host_activity_directory=None))
    with TestClient(disabled) as client:
        assert client.post("/api/study/activity?session_id=tab-a", headers=headers).status_code == 503
