"""Same-host admission is optional and cannot become a Study dependency."""
from dataclasses import replace
import json
import threading
import time
from fastapi.testclient import TestClient
import pytest
from tempo_learning_runtime.api import create_app
from tempo_learning_runtime.host_activity import ACTIVITY_NS, HostActivity


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "optional publisher did not reach the expected state"


def record(activity, session, remaining_ms=3000):
    # Deterministic publication for protocol tests; production publication is async.
    if not activity.published_until:
        activity._publish()
    activity.record(session, remaining_ms)
    activity._publish()


def close(activity):
    activity.close()
    if activity.thread:
        activity.thread.join(timeout=2)
        assert not activity.thread.is_alive()
    elif activity.directory is not None:
        activity._cleanup()


def snapshot(activity):
    activity._publish()
    return json.loads((activity.directory / (activity.producer + ".json")).read_text())


def test_host_activity_sessions_expire_independently_and_clean_shutdown_removes_only_owner(tmp_path):
    now = [100_000_000_000]
    first = HostActivity(tmp_path, clock=lambda: now[0])
    second = HostActivity(tmp_path, clock=lambda: now[0])
    record(first, "tab-a")
    now[0] += 1_000_000_000
    record(first, "tab-b")
    now[0] += ACTIVITY_NS - 1_000_000_000
    record(first, "tab-b")
    snapshot = json.loads((tmp_path / (first.producer + ".json")).read_text())
    assert set(snapshot["sessions"]) == {"tab-b"}
    record(second, "other-runtime")
    close(first)
    assert not (tmp_path / (first.producer + ".json")).exists()
    assert (tmp_path / (second.producer + ".json")).exists()
    close(second)


def test_host_activity_failure_retains_last_snapshot_and_never_imports_a_broker(tmp_path, monkeypatch):
    activity = HostActivity(tmp_path)
    record(activity, "held")
    retained = (tmp_path / (activity.producer + ".json")).read_bytes()
    monkeypatch.setattr(activity, "_publish", lambda: (_ for _ in ()).throw(OSError("unavailable filesystem")))
    with pytest.raises(OSError):
        record(activity, "pending")
    assert (tmp_path / (activity.producer + ".json")).read_bytes() == retained
    close(activity)


def test_host_activity_final_pulse_retains_remaining_quiet_time_instead_of_starting_a_new_lease(tmp_path):
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, clock=lambda: now[0])
    record(activity, "released", 3000)
    now[0] += 2_500_000_000
    record(activity, "released", 500)
    snapshot = json.loads((tmp_path / (activity.producer + ".json")).read_text())
    assert int(snapshot["sessions"]["released"]) == 103_000_000_000
    close(activity)


def test_host_activity_pending_essential_requests_outlive_browser_leases_and_release_independently(tmp_path):
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, clock=lambda: now[0])
    current = lambda: snapshot(activity)
    with activity.request():
        with activity.request():
            now[0] += 10_000_000_000
            record(activity, "late-pulse", 1)
            assert current()["pendingRequests"] is True
        assert current()["pendingRequests"] is True
    assert current()["pendingRequests"] is False
    assert max(map(int, current()["sessions"].values())) == now[0] + ACTIVITY_NS
    close(activity)


def test_host_activity_endpoint_authenticates_and_needs_no_database(tmp_path):
    from tempo_learning_runtime.config import Configuration
    configuration = Configuration("activity", "postgresql://unused/learning", "postgresql://reader/learning", "x" * 40,
                                  maintenance=False, host_activity_directory=tmp_path)
    app = create_app(configuration)
    with TestClient(app) as client:
        assert client.post("/api/study/activity?session_id=tab-a").status_code == 401
        headers = {"Authorization": "Bearer " + configuration.token, "Origin": configuration.origins[0]}
        wait_for(lambda: bool(list(tmp_path.glob("*.json"))))
        assert client.post("/api/study/activity?session_id=tab-a", headers=headers).json()["available"]
        assert client.post("/api/study/activity?session_id=../foreign", headers=headers).status_code == 422
        assert client.post("/api/study/activity?session_id=tab-b", headers={**headers, "Origin": "http://foreign.test"}).status_code == 403
        wait_for(lambda: "tab-a" in json.loads(next(tmp_path.glob("*.json")).read_text())["sessions"])
        snapshot = json.loads(next(tmp_path.glob("*.json")).read_text())
        assert set(snapshot["sessions"]) == {"tab-a"}
        assert snapshot["source"] == configuration.workspace_id
    wait_for(lambda: not list(tmp_path.glob("*.json")))
    disabled = create_app(replace(configuration, host_activity_directory=None))
    with TestClient(disabled) as client:
        assert client.post("/api/study/activity?session_id=tab-a", headers=headers).status_code == 503


def test_host_activity_reporting_thread_failure_does_not_prevent_service_startup(tmp_path, monkeypatch, caplog):
    import threading
    from tempo_learning_runtime.config import Configuration
    original_start = threading.Thread.start

    def start_thread(thread):
        if thread.name == "learning-host-activity":
            raise RuntimeError("injected reporter thread unavailable")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start_thread)
    configuration = Configuration("thread-failure", "postgresql://unused/learning", "postgresql://reader/learning", "x" * 40,
                                  maintenance=False, host_activity_directory=tmp_path)
    app = create_app(configuration)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        with app.state.host_activity.request():
            assert app.state.host_activity.requests == 1
        assert app.state.host_activity.requests == 0
        assert not list(tmp_path.glob("*.json"))
    assert "reporter thread unavailable" in caplog.text


def test_host_activity_shutdown_reporting_failure_is_diagnostic_and_retains_owned_snapshot(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    activity = HostActivity(tmp_path)
    record(activity, "held")
    path = tmp_path / (activity.producer + ".json")
    retained = path.read_bytes()
    original_unlink = Path.unlink

    def unlink(candidate, *arguments, **options):
        if candidate == path:
            raise OSError("injected owned snapshot removal unavailable")
        return original_unlink(candidate, *arguments, **options)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", unlink)
        close(activity)
    assert path.read_bytes() == retained
    assert "removal unavailable" in caplog.text
    close(activity)


def test_host_activity_stale_short_pulse_cannot_shorten_a_fresh_held_session(tmp_path):
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, clock=lambda: now[0])
    record(activity, "held", 3000)
    now[0] += 10_000_000
    record(activity, "held", 1)
    snapshot = json.loads((tmp_path / (activity.producer + ".json")).read_text())
    assert int(snapshot["sessions"]["held"]) == 103_000_000_000
    close(activity)


def test_host_activity_clock_retains_reader_domain_after_host_sleep(tmp_path, monkeypatch):
    import json
    import sys
    from tempo_learning_runtime import host_activity
    # macOS uptime excludes suspend; the reader's continuous clock includes it.
    continuous_now = 90_000_000_000_000
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(host_activity.time, 'CLOCK_MONOTONIC_RAW', 4, raising=False)
    monkeypatch.setattr(host_activity.time, 'clock_gettime_ns', lambda clock: continuous_now)
    reporter = host_activity.HostActivity(tmp_path, source='sleep-proof')
    record(reporter, 'held')
    snapshot = json.loads((tmp_path / (reporter.producer + '.json')).read_text())
    assert int(snapshot['validUntilNs']) == continuous_now + host_activity.POLICY_NS
    assert int(snapshot['sessions']['held']) == continuous_now + host_activity.ACTIVITY_NS


def test_host_activity_stalled_publication_does_not_block_start_essential_request_or_shutdown(tmp_path, monkeypatch):
    from pathlib import Path
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original_mkdir = Path.mkdir
    activity = HostActivity(tmp_path)

    def stalled_mkdir(path, *args, **kwargs):
        if path == tmp_path:
            entered.set()
            assert release.wait(3), "test must release its own publisher"
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", stalled_mkdir)

    def essential_lifecycle():
        activity.start()
        with activity.request():
            assert activity.requests == 1
        activity.close()
        completed.set()

    caller = threading.Thread(target=essential_lifecycle, daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        assert completed.wait(0.25), "essential lifecycle waited on optional filesystem I/O"
        assert activity.requests == 0
        assert activity.quiet_until > 0
    finally:
        release.set()
        caller.join(2)
        close(activity)
    assert not list(tmp_path.glob("*.json"))


def test_host_activity_stalled_publisher_recovers_pending_and_independent_quiet_holds(tmp_path, monkeypatch):
    from pathlib import Path
    now = [100_000_000_000]
    activity = HostActivity(tmp_path, source="learning-a", clock=lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    original_replace = __import__("os").replace
    should_stall = [False]

    def replace_file(source, target):
        if should_stall[0]:
            should_stall[0] = False
            entered.set()
            assert release.wait(3)
        return original_replace(source, target)

    monkeypatch.setattr("tempo_learning_runtime.host_activity.os.replace", replace_file)
    activity.start()
    path = tmp_path / (activity.producer + ".json")
    try:
        wait_for(lambda: activity.published_until > 0)
        should_stall[0] = True
        activity.record("held", 3000)
        assert entered.wait(1)
        with activity.request():
            with activity.request():
                now[0] += 16_000_000_000
            # A stalled publication never renews its producer deadline in memory.
            assert activity.published_until <= now[0]
            with pytest.raises(RuntimeError, match="unavailable"):
                activity.record("recovered", 3000)
            release.set()
            wait_for(lambda: json.loads(path.read_text())["pendingRequests"] is True)
            recovered = json.loads(path.read_text())
            assert recovered["source"] == "learning-a"
            assert "held" not in recovered["sessions"]
            assert "recovered" in recovered["sessions"]
        wait_for(lambda: json.loads(path.read_text())["pendingRequests"] is False)
        assert int(json.loads(path.read_text())["sessions"]["request_quiet"]) == now[0] + ACTIVITY_NS
    finally:
        release.set()
        close(activity)


def test_host_activity_stalled_cleanup_does_not_block_shutdown(tmp_path, monkeypatch):
    from pathlib import Path
    entered, release = threading.Event(), threading.Event()
    activity = HostActivity(tmp_path)
    path = tmp_path / (activity.producer + ".json")
    original_read_text = Path.read_text
    activity.start()
    wait_for(lambda: activity.published_until > 0)

    def stalled_read(candidate, *args, **kwargs):
        if candidate == path:
            entered.set()
            assert release.wait(3)
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", stalled_read)
    try:
        activity.close()
        assert entered.wait(1)
        # A repeated shutdown must not reenter or wait for publisher cleanup.
        activity.close()
        assert path.exists()
    finally:
        release.set()
        close(activity)
    assert not path.exists()


def test_host_activity_api_request_and_lifespan_do_not_wait_for_stalled_publication(tmp_path, monkeypatch):
    from pathlib import Path
    from tempo_learning_runtime.config import Configuration
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original_mkdir = Path.mkdir
    configuration = Configuration("stalled-activity", "postgresql://unused/learning", "postgresql://reader/learning", "x" * 40,
                                  maintenance=False, host_activity_directory=tmp_path)
    app = create_app(configuration)
    errors = []

    def stalled_mkdir(path, *args, **kwargs):
        if path == tmp_path:
            entered.set()
            assert release.wait(3)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", stalled_mkdir)

    def serve_request():
        try:
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer " + configuration.token, "Origin": configuration.origins[0]}
                # Authenticated unknown essential route exercises middleware without a DB fixture.
                assert client.get("/api/missing-for-activity-proof", headers=headers).status_code == 404
                assert client.post("/api/study/activity?session_id=tab-a", headers=headers).status_code == 503
        except BaseException as error:
            errors.append(error)
        finally:
            completed.set()

    caller = threading.Thread(target=serve_request, daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        assert completed.wait(0.5), "HTTP lifecycle waited on optional publication"
        assert not errors
        assert app.state.host_activity.requests == 0
    finally:
        release.set()
        caller.join(2)
        close(app.state.host_activity)


def test_host_activity_browser_capacity_keeps_essential_state_bounded(tmp_path):
    activity = HostActivity(tmp_path)
    activity._publish()
    for index in range(1024):
        activity.record(str(index))
    with pytest.raises(RuntimeError, match="capacity"):
        activity.record("overflow")
    with activity.request():
        assert activity.requests == 1
    assert len(activity.sessions) == 1024
    assert activity.requests == 0
    close(activity)

def test_host_activity_cancelled_essential_request_releases_pending_without_publication(tmp_path):
    import asyncio
    activity = HostActivity(tmp_path)

    async def exercise():
        entered = asyncio.Event()

        async def request():
            with activity.request():
                entered.set()
                await asyncio.Future()

        pending = asyncio.create_task(request())
        await entered.wait()
        assert activity.requests == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert activity.requests == 0
        assert activity.quiet_until > 0
        assert not list(tmp_path.glob("*.json"))

    asyncio.run(exercise())
    activity.close()


def test_host_activity_publication_error_reports_unavailable_then_recovers_without_study_restart(tmp_path, monkeypatch):
    activity = HostActivity(tmp_path)
    original_replace = __import__("os").replace
    fail = threading.Event()

    def replace_file(source, target):
        if fail.is_set():
            raise OSError("injected optional publication failure")
        return original_replace(source, target)

    monkeypatch.setattr("tempo_learning_runtime.host_activity.os.replace", replace_file)
    activity.start()
    try:
        wait_for(lambda: activity.published_until > 0)
        fail.set()
        activity.record("held")
        wait_for(lambda: activity.publication_failed)
        with activity.request():
            with pytest.raises(RuntimeError, match="unavailable"):
                activity.record("retry")
        fail.clear()
        # A retry wakes the publisher even while the caller sees unavailable.
        try:
            activity.record("retry")
        except RuntimeError:
            pass  # Publication may recover before this concurrent retry.
        wait_for(lambda: not activity.publication_failed)
        activity.record("recovered")
        path = tmp_path / (activity.producer + ".json")
        wait_for(lambda: "recovered" in json.loads(path.read_text())["sessions"])
    finally:
        fail.clear()
        close(activity)
