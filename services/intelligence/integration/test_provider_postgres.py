"""Installed provider/session races against invocation-owned PostgreSQL and HTTP."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Event, Thread
import time

import httpx
import psycopg
import pytest
from tempo_analysis_jobs_contracts.v1 import MaiaResult

from tempo_intelligence.api import create_app
from tempo_intelligence.credentials import CredentialMaterial, PrivateCredentials
from tempo_intelligence.models import RequestInput
from tempo_intelligence.provider import ExplorerClient
from tempo_intelligence.store import Store
from tempo_intelligence.worker import Worker

from .support import maia_result, owner_execute, ready, register, request_value


def valid_counts():
    return {"white": 4, "draws": 2, "black": 4,
            "moves": [{"uci": "e2e4", "white": 3, "draws": 1, "black": 2},
                      {"uci": "d2d4", "white": 1, "draws": 1, "black": 2}]}


@contextmanager
def fake_provider(behavior=None):
    """Actual bounded loopback HTTP; no real credentials or external network."""
    paths = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            paths.append(self.path.split("?", 1)[0])
            try:
                status, headers, body = behavior(len(paths)) if behavior else (200, {}, valid_counts())
                encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                # The cancellation regression intentionally disconnects first.
                pass
            except Exception as error:
                errors.append(error)
                self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", paths
        assert not errors, "local_provider_boundary_failed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def worker(state, base_url):
    store, _, path = state
    return Worker(store, PrivateCredentials(path), ExplorerClient(base_url))


def sources(store, request_id="request"):
    return {source["kind"]: source for source in store.inspect(request_id)["sources"]}


def submit(state, requested_sources=None):
    store, _, _ = state
    return store.submit(RequestInput.model_validate(request_value(sources=requested_sources)))


def test_postgres_provider_counts_for_both_endpoints_survive_service_restart(state):
    store, configuration, path = state
    register(state)
    submit(state, [{"kind": "lichess"}, {"kind": "masters"}])
    with fake_provider() as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        ready(store)
        assert asyncio.run(worker(state, url).work_once())
        assert set(paths) == {"/lichess", "/masters"} and len(paths) == 2
    accepted = {kind: source["evidence_id"] for kind, source in sources(store).items()}
    before = {kind: store.evidence(identity) for kind, identity in accepted.items()}
    assert all(value["payload"]["total_games"] == 10 for value in before.values())
    assert all(len(value["payload"]["legal_observations"]) == 20 for value in before.values())
    store.close()
    path.unlink()
    restarted = Store(configuration)
    try:
        restarted.validate_startup()
        assert {kind: restarted.evidence(identity) for kind, identity in accepted.items()} == before
        assert {kind: value["evidence_id"] for kind, value in sources(restarted).items()} == accepted
    finally:
        restarted.close()


def test_postgres_provider_429_deadline_survives_restart_without_failure_attempts(state):
    store, configuration, _ = state
    register(state)
    submit(state)
    with fake_provider(lambda _: (429, {"Retry-After": "120"}, {})) as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        status = sources(store)["lichess"]
        assert status["state"] == "retry_wait" and status["error_code"] == "rate_limited"
        assert status["attempts"] == 0
        deadline = datetime.fromisoformat(status["retry_at"])
        assert (deadline - datetime.now(timezone.utc)).total_seconds() > 110
        assert status["provider_retry_at"] == status["retry_at"]
        assert not asyncio.run(worker(state, url).work_once()) and len(paths) == 1
    restarted = Store(configuration)
    try:
        restarted.validate_startup()
        assert sources(restarted)["lichess"]["retry_at"] == status["retry_at"]
        assert restarted.claim() is None
    finally:
        restarted.close()


def test_postgres_late_auth_rejection_cannot_reject_new_registration_even_for_same_token(state):
    store, _, _ = state
    older = register(state)
    submit(state)
    entered, release = Event(), Event()

    def behavior(number):
        if number == 1:
            entered.set()
            assert release.wait(5), "provider_release_timeout"
            return 401, {}, {}
        return 200, {}, valid_counts()

    with fake_provider(behavior) as (url, paths):
        async def race():
            pending = asyncio.create_task(worker(state, url).work_once())
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                newer = register(state)  # Same synthetic material; fresh explicit identity.
                assert newer.registration_id != older.registration_id
                release.set()
                assert await pending
                current = store.credential_status()
                assert current["registration_id"] == newer.registration_id
                assert current["status"] == "registered" and current["generation"] == 2
                ready(store)
                assert await worker(state, url).work_once()
                assert store.credential_status()["status"] == "accepted"
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        asyncio.run(race())
        assert len(paths) == 2


@pytest.mark.parametrize("failure,expected_state", [(503, "retry_wait"), (401, "waiting_credentials")])
def test_postgres_failed_explorer_refresh_preserves_accepted_explorer_and_maia(state, failure, expected_state):
    store, configuration, _ = state
    profile = configuration.model_profiles[0]
    maia = {"kind": "maia", "rating": profile["ratings"][0],
            **{name: profile[name] for name in ("model_id", "model_version", "artifact_sha256")}}
    register(state)
    submit(state, [{"kind": "lichess"}, maia])
    first_job = store.maia_jobs("request")["jobs"][0]
    store.accept_maia(MaiaResult.model_validate(maia_result(first_job)))
    with fake_provider(lambda number: (200, {}, valid_counts()) if number == 1 else (failure, {}, {})) as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        retained = {kind: source["evidence_id"] for kind, source in sources(store).items()}
        evidence = {kind: store.evidence(identity) for kind, identity in retained.items()}
        store.refresh("request", "refresh-sources", 1)
        ready(store)
        assert asyncio.run(worker(state, url).work_once())
        failed = sources(store)["lichess"]
        assert failed["state"] == expected_state and failed["error_code"]
        assert {kind: source["evidence_id"] for kind, source in sources(store).items()} == retained
        assert {kind: store.evidence(identity) for kind, identity in retained.items()} == evidence
        # A later accepted Maia result must not clear independent Explorer error.
        replacement_job = store.maia_jobs("request")["jobs"][0]
        store.accept_maia(MaiaResult.model_validate(maia_result(replacement_job)))
        assert sources(store)["lichess"] == failed
        assert sources(store)["maia"]["state"] == "complete"
        assert len(paths) == 2


def test_postgres_one_provider_permit_serializes_lichess_and_masters(state):
    store, configuration, _ = state
    register(state)
    submit(state, [{"kind": "lichess"}, {"kind": "masters"}])
    entered, release = Event(), Event()
    def behavior(number):
        if number == 1:
            entered.set()
            assert release.wait(5)
        return 200, {}, valid_counts()
    other = Store(configuration)
    try:
        other.validate_startup()
        with fake_provider(behavior) as (url, paths):
            async def race():
                pending = asyncio.create_task(worker(state, url).work_once())
                try:
                    assert await asyncio.to_thread(entered.wait, 3)
                    assert other.claim() is None
                    assert not await worker(state, url).work_once()
                    assert len(paths) == 1
                    release.set()
                    assert await pending
                    assert other.claim() is None  # Shared one-second post-request interval.
                    ready(store)
                    assert await worker(state, url).work_once()
                finally:
                    release.set()
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            asyncio.run(race())
            assert set(paths) == {"/lichess", "/masters"}
    finally:
        other.close()


def test_postgres_provider_http_has_no_checked_out_connection_or_transaction_and_reads_continue(state):
    store, _, _ = state
    register(state)
    submit(state)
    entered, release = Event(), Event()
    active_states = []
    def behavior(_):
        with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_ADMIN_URL"]) as database:
            active_states.extend(database.execute(
                "SELECT state FROM pg_stat_activity WHERE application_name IN "
                "('tempo-intelligence','tempo-intelligence-worker') AND state<>'idle'"
            ).fetchall())
        entered.set()
        assert release.wait(5)
        return 200, {}, valid_counts()
    with fake_provider(behavior) as (url, _):
        async def race():
            pending = asyncio.create_task(worker(state, url).work_once())
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                assert active_states == []
                statistics = store.background.get_stats()
                assert statistics["pool_available"] == statistics["pool_size"]
                started = time.monotonic()
                assert sources(store)["lichess"]["state"] == "running"
                store.health()
                assert time.monotonic() - started < 1
                assert not pending.done()
                release.set()
                assert await pending
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        asyncio.run(race())


def test_postgres_cancelled_http_releases_lease_without_consuming_attempt_and_is_recoverable(state):
    store, _, _ = state
    register(state)
    submit(state)
    entered, release = Event(), Event()
    def behavior(number):
        if number == 1:
            entered.set()
            assert release.wait(5)
        return 200, {}, valid_counts()
    with fake_provider(behavior) as (url, paths):
        async def cancel_then_recover():
            pending = asyncio.create_task(worker(state, url).work_once())
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                status = sources(store)["lichess"]
                assert status["state"] == "queued" and status["attempts"] == 0
                assert status["error_code"] == "request_cancelled"
                assert owner_execute("SELECT lease_token,leased_until FROM intelligence.provider_control WHERE id=1") == [(None, None)]
                assert owner_execute("SELECT lease_token,leased_until FROM intelligence.caches") == [(None, None)]
                release.set()
                ready(store)
                assert await worker(state, url).work_once()
                assert sources(store)["lichess"]["state"] == "complete"
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        asyncio.run(cancel_then_recover())
        assert len(paths) == 2


@pytest.mark.parametrize("cleanup_failure",["exception","client-deadline"])
def test_postgres_cancelled_http_with_failed_cleanup_recovers_by_native_lease_expiry_and_fences_stale_publication(state,monkeypatch,cleanup_failure):
    from tempo_intelligence import runtime
    store,configuration,path=state
    register(state)
    submit(state)
    entered,release=Event(),Event()
    captured_claims=[]
    cleanup_calls=[]
    expired_connections=[]
    original_claim=store.claim

    def capture_claim():
        claim=original_claim()
        if claim is not None:
            captured_claims.append(claim)
        return claim

    def failed_cleanup(claim,code,**options):
        cleanup_calls.append((claim["cache_id"],code,options))
        if cleanup_failure=="client-deadline":
            with store.transaction(background=True) as database:
                expired_connections.append(database)
                database.execute("SELECT pg_sleep(30)")
        raise RuntimeError("synthetic_cleanup_failure_must_not_replace_cancellation")

    def behavior(number):
        if number==2:
            entered.set()
            assert release.wait(5)
        return 200,{},valid_counts()

    with fake_provider(behavior) as (url,paths):
        assert asyncio.run(worker(state,url).work_once())
        accepted=sources(store)["lichess"]["evidence_id"]
        evidence=store.evidence(accepted)
        store.refresh("request","refresh-before-failed-cleanup")
        ready(store)
        monkeypatch.setattr(store,"claim",capture_claim)
        monkeypatch.setattr(store,"fail",failed_cleanup)

        async def cancel_with_failed_cleanup():
            runner=runtime.DatabaseRunner()
            client=ExplorerClient(url)
            active_worker=Worker(store,PrivateCredentials(path),client,runner=runner)
            pending=asyncio.create_task(active_worker.work_once())
            try:
                assert await asyncio.to_thread(entered.wait,3)
                cache_before=owner_execute("SELECT row_to_json(c) FROM intelligence.caches c")
                permit_before=owner_execute("SELECT row_to_json(p) FROM intelligence.provider_control p")
                if cleanup_failure=="client-deadline":
                    monkeypatch.setattr(runtime,"BACKGROUND_OPERATION_SECONDS",0.2)
                started=time.monotonic()
                pending.cancel("native_cancelled_request")
                with pytest.raises(asyncio.CancelledError,match="native_cancelled_request"):
                    await pending
                assert time.monotonic()-started<1
                assert pending.cancelled() and runner.pending is None
                assert cleanup_calls==[(captured_claims[0]["cache_id"],"request_cancelled",{"cancelled":True})]
                assert owner_execute("SELECT row_to_json(c) FROM intelligence.caches c")==cache_before
                assert owner_execute("SELECT row_to_json(p) FROM intelligence.provider_control p")==permit_before
                assert sources(store)["lichess"]["attempts"]==0
                assert sources(store)["lichess"]["evidence_id"]==accepted
                assert store.evidence(accepted)==evidence
                assert store.claim() is None
                if expired_connections:
                    assert expired_connections[0].closed
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending,return_exceptions=True)
                await active_worker.aclose()
                await runner.aclose()
                assert runner.pending is None and runner.closed
                assert not any(thread.is_alive() for thread in runner.executor._threads)

        asyncio.run(cancel_with_failed_cleanup())
        assert len(paths)==2

    old_claim=captured_claims[0]
    assert len(captured_claims)==1
    owner_execute("UPDATE intelligence.caches SET leased_until=now()-interval '1 second' WHERE cache_id=%s",(old_claim["cache_id"],))
    owner_execute("UPDATE intelligence.provider_control SET leased_until=now()-interval '1 second' WHERE id=1")
    assert not store.publish(old_claim,{"source":"stale-before-reclaim"})
    restarted=Store(configuration)
    try:
        restarted.validate_startup()
        replacement=restarted.claim()
        assert replacement and replacement["generation"]==old_claim["generation"]
        assert replacement["execution_generation"]==old_claim["execution_generation"]+1
        assert replacement["attempts"]==old_claim["attempts"]==0
        assert not store.publish(old_claim,{"source":"stale-after-reclaim"})
        assert restarted.publish(replacement,{"source":"replacement","total_games":29})
        newer=sources(restarted)["lichess"]["evidence_id"]
        assert newer!=accepted
        assert not store.publish(old_claim,{"source":"stale-after-acceptance"})
        assert sources(restarted)["lichess"]["evidence_id"]==newer
        assert restarted.evidence(newer)["payload"]["total_games"]==29
        assert restarted.evidence(accepted)==evidence
    finally:
        restarted.close()


def test_postgres_advisory_locked_background_claim_keeps_actual_asgi_health_responsive(state,monkeypatch):
    store,configuration,_=state
    register(state)
    submit(state)
    entered,completed=Event(),Event()
    waiting_backend=[]
    original_claim=store.claim
    advisory_identity=109289

    def locked_claim():
        with store.transaction(background=True) as database:
            database.execute("SET LOCAL lock_timeout='1400ms'")
            waiting_backend.append(database.info.backend_pid)
            entered.set()
            database.execute("SELECT pg_advisory_xact_lock(%s)",(advisory_identity,))
        completed.set()
        return original_claim()

    def wait_for_actual_database_lock():
        deadline=time.monotonic()+0.6
        with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_ADMIN_URL"],autocommit=True) as database:
            while time.monotonic()<deadline:
                row=database.execute("SELECT state,wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s",(waiting_backend[0],)).fetchone()
                if row==("active","Lock","advisory"):
                    return True
                time.sleep(0.005)
        return False

    monkeypatch.setattr(store,"claim",locked_claim)
    with psycopg.connect(os.environ["TEMPO_INTELLIGENCE_TEST_OWNER_URL"]) as lock_owner:
        lock_owner.execute("SELECT pg_advisory_xact_lock(%s)",(advisory_identity,))
        with fake_provider() as (url,paths):
            active_worker=worker(state,url)
            application=create_app(replace(configuration,worker_enabled=True),store=store,worker=active_worker)

            async def check_health_during_lock():
                try:
                    async with application.router.lifespan_context(application):
                        assert await asyncio.to_thread(entered.wait,2)
                        assert await asyncio.to_thread(wait_for_actual_database_lock)
                        assert not completed.is_set() and paths==[]
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(application),base_url="http://testserver") as client:
                            started=time.monotonic()
                            response=await asyncio.wait_for(client.get("/healthz"),timeout=0.5)
                            assert response.status_code==200 and response.json()=={"status":"ready","workspace_id":"fixture"}
                            assert time.monotonic()-started<0.5
                        assert not completed.is_set() and paths==[]
                        lock_owner.rollback()
                        deadline=time.monotonic()+2
                        while time.monotonic()<deadline:
                            current=await asyncio.to_thread(sources,store)
                            if current["lichess"]["state"]=="complete":
                                break
                            await asyncio.sleep(0.005)
                        assert current["lichess"]["state"]=="complete"
                finally:
                    lock_owner.rollback()
            asyncio.run(check_health_during_lock())
            assert len(paths)==1 and completed.is_set() and active_worker.closed


def test_postgres_background_client_deadline_discards_connection_resets_context_and_allows_pool_replacement(state,monkeypatch):
    from tempo_intelligence import runtime
    store,_,_=state
    expired_connections=[]
    replacement_connections=[]
    monkeypatch.setattr(runtime,"BACKGROUND_OPERATION_SECONDS",0.2)

    def exceed_client_deadline():
        with store.transaction(background=True) as database:
            expired_connections.append(database)
            database.execute("SELECT pg_sleep(30)")

    def use_replacement_connection():
        with store.transaction(background=True) as database:
            replacement_connections.append(database)
            return database.execute("SELECT 1 AS answer").fetchone()["answer"]

    async def exercise_deadline():
        runner=runtime.DatabaseRunner()
        try:
            started=time.monotonic()
            with pytest.raises(psycopg.OperationalError,match="^background_database_(deadline|unavailable)$"):
                await runner.run(exceed_client_deadline)
            assert 0.15<=time.monotonic()-started<1
            assert len(expired_connections)==1 and expired_connections[0].closed
            assert runner.pending is None and runtime.background_deadline.get() is None
            observed_context=await asyncio.get_running_loop().run_in_executor(runner.executor,runtime.background_deadline.get)
            assert observed_context is None
            assert await runner.run(use_replacement_connection)==1
            assert replacement_connections[0] is not expired_connections[0]
            assert not replacement_connections[0].closed
        finally:
            await runner.aclose()
            assert runner.closed and runner.pending is None
            assert not any(thread.is_alive() for thread in runner.executor._threads)
    asyncio.run(exercise_deadline())


def test_postgres_lost_private_file_is_unavailable_then_explicit_registration_recovers(state):
    store, _, path = state
    register(state)
    submit(state)
    path.unlink()
    with fake_provider() as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        failed = sources(store)["lichess"]
        assert failed["state"] == "waiting_credentials"
        assert failed["error_code"] == "credential_storage_unavailable" and paths == []
        assert failed["credential"]["status"] == "credential_storage_unavailable"
        register(state)
        ready(store)
        assert asyncio.run(worker(state, url).work_once())
        assert sources(store)["lichess"]["state"] == "complete" and len(paths) == 1


def test_postgres_rejected_registration_is_not_retried_until_explicit_replacement(state):
    store, _, _ = state
    register(state)
    submit(state)
    with fake_provider(lambda number: (401, {}, {}) if number == 1 else (200, {}, valid_counts())) as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        assert store.credential_status()["status"] == "rejected"
        ready(store)
        assert not asyncio.run(worker(state, url).work_once()) and len(paths) == 1
        assert sources(store)["lichess"]["attempts"] == 0
        register(state)
        ready(store)
        assert asyncio.run(worker(state, url).work_once()) and len(paths) == 2


def test_postgres_invalid_provider_response_is_failed_without_automatic_retry(state):
    store, _, _ = state
    register(state)
    submit(state)
    def behavior(number):
        invalid = {"white": -1, "draws": 0, "black": 0, "moves": []}
        return 200, {}, invalid if number == 1 else valid_counts()
    with fake_provider(behavior) as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        failed = sources(store)["lichess"]
        assert failed["state"] == "failed" and failed["error_code"] == "invalid_response"
        assert failed["evidence_id"] is None and failed["attempts"] == 1
        assert not failed["retryable"] and not failed["retry_eligible"]
        ready(store)
        assert not asyncio.run(worker(state, url).work_once()) and len(paths) == 1
        before_explicit_retry = sources(store)["lichess"]
        assert store.retry("request", "invalid-same-generation-retry", 1)["accepted"]
        assert sources(store)["lichess"]==before_explicit_retry
        store.refresh("request", "corrected-provider-refresh", 1)
        ready(store)
        assert asyncio.run(worker(state, url).work_once()) and len(paths) == 2
        accepted = sources(store)["lichess"]
        assert accepted["state"] == "complete" and accepted["work_generation"] == 2
        assert accepted["retryable"]


def test_postgres_late_auth_rejection_cannot_replace_explicit_revoked_state(state):
    store, _, _ = state
    register(state)
    submit(state)
    entered, release = Event(), Event()
    def behavior(_):
        entered.set()
        assert release.wait(5)
        return 401, {}, {}
    with fake_provider(behavior) as (url, paths):
        async def race():
            pending = asyncio.create_task(worker(state, url).work_once())
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                current = store.credential_status()
                store.credential_revoke(current["generation"])
                assert store.credential_status()["status"] == "revoked"
                release.set()
                assert await pending
                assert store.credential_status()["status"] == "revoked"
                ready(store)
                assert not await worker(state, url).work_once()
                assert sources(store)["lichess"]["credential"]["status"] == "revoked"
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
        asyncio.run(race())
        assert len(paths) == 1


def test_postgres_provider_secret_never_reaches_any_owned_durable_table(state):
    store, _, _ = state
    token = "synthetic_provider_secret_must_remain_in_private_file"
    register(state, token=token)
    submit(state)
    def behavior(number):
        sample = valid_counts()
        sample["provider_debug"] = token  # Untrusted extra data must not persist.
        return (200, {}, sample) if number == 1 else (401, {}, {"error": token})
    with fake_provider(behavior) as (url, paths):
        assert asyncio.run(worker(state, url).work_once())
        store.refresh("request", "secret-boundary-refresh", 1)
        ready(store)
        assert asyncio.run(worker(state, url).work_once())
        assert len(paths) == 2
    tables = ("snapshots", "requests", "request_heads", "caches", "evidence", "commands", "maia_jobs",
              "model_profiles", "credential_head", "credential_registrations", "provider_control",
              "instance", "migration_history")
    for table in tables:
        rows = owner_execute(f"SELECT to_jsonb(owned_row)::text FROM intelligence.{table} owned_row")
        assert all(token not in row[0] for row in rows), "provider_material_reached_durable_state"


def test_postgres_expired_registration_waits_without_http_or_failure_attempts(state,monkeypatch):
    store,_,_=state
    original=register(state)
    expiry=datetime.now(timezone.utc)+timedelta(hours=1)
    # Provision an explicit finite registration, then advance the service clock.
    from uuid import uuid4
    material=CredentialMaterial(str(uuid4()),original.token,expiry)
    store.credential_reload(material,1)
    submit(state)
    monkeypatch.setattr("tempo_intelligence.store.utcnow",lambda:expiry+timedelta(seconds=1))
    with fake_provider() as (url,paths):
        assert not asyncio.run(worker(state,url).work_once())
        status=sources(store)["lichess"]
        assert status["state"]=="waiting_credentials" and status["credential"]["status"]=="expired"
        assert status["attempts"]==0 and paths==[]


def test_postgres_provider_outage_stops_after_three_failures_and_explicit_retry_recovers(state):
    store,_,_=state
    register(state)
    submit(state)
    with fake_provider(lambda number:(503,{}, {}) if number<=3 else (200,{},valid_counts())) as (url,paths):
        for attempt in range(1,4):
            ready(store)
            assert asyncio.run(worker(state,url).work_once())
            assert sources(store)["lichess"]["attempts"]==attempt
        assert sources(store)["lichess"]["state"]=="failed"
        ready(store)
        assert not asyncio.run(worker(state,url).work_once()) and len(paths)==3
        store.retry("request","explicit-outage-retry",1)
        ready(store)
        assert asyncio.run(worker(state,url).work_once()) and len(paths)==4
        assert sources(store)["lichess"]["state"]=="complete"
