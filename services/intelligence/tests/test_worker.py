"""Deterministic Explorer worker cancellation and database-thread ownership."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading

import httpx
import pytest

from tempo_intelligence import Configuration, create_app
from tempo_intelligence.credentials import CredentialError, CredentialMaterial
from tempo_intelligence.provider import ProviderFailure
from tempo_intelligence.worker import Worker


TEST_TIMEOUT = 2
THREAD_WATCHDOG = 3
REGISTRATION = "144d69cd-024d-4a5a-a5c5-55298df06e04"


class DatabaseGate:
    """A test-owned block always ends, even against the blocking old worker."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.exited = threading.Event()
        self.watchdog_expired = False

    def wait(self):
        self.entered.set()
        try:
            if not self.release.wait(THREAD_WATCHDOG):
                self.watchdog_expired = True
                raise RuntimeError("test_database_gate_watchdog_expired")
        finally:
            self.exited.set()


class LocalStore:
    def __init__(self, *, blocked=None, gate=None, available=True):
        self.blocked = blocked
        self.gate = gate
        self.state = "queued" if available else "absent"
        self.attempts = 2
        self.accepted = []
        self.failures = []
        self.claims = 0
        self.execution = 0
        self.active = 0
        self.closed = False
        self.closed_while_active = False
        self.calls = []
        self.errors = {}
        self.lock = threading.Lock()

    @contextmanager
    def operation(self, name):
        with self.lock:
            assert not self.closed, "database operation used a closed Store"
            self.active += 1
            self.calls.append((name, "start", threading.get_ident()))
        try:
            if name == self.blocked:
                self.gate.wait()
            if name in self.errors:
                raise self.errors[name]
            yield
        finally:
            with self.lock:
                self.active -= 1
                self.calls.append((name, "end", threading.get_ident()))

    def validate_startup(self):
        pass

    def health(self):
        assert not self.closed

    def close(self):
        with self.lock:
            self.closed_while_active = self.active != 0
            self.closed = True
            self.calls.append(("close", "end", threading.get_ident()))

    def claim(self):
        self.claims += 1
        with self.operation("claim"):
            if self.state != "queued":
                return None
            self.execution += 1
            self.state = "running"
            return {
                "cache_id": "source-one",
                "generation": 1,
                "execution_generation": self.execution,
                "lease_token": "lease-" + str(self.execution),
                "credential": {"registration_id": REGISTRATION},
                "descriptor": {"kind": "lichess"},
            }

    def publish(self, claim, payload):
        with self.operation("publish"):
            if self.state != "running" or claim["execution_generation"] != self.execution:
                return False
            self.accepted.append(payload)
            self.state = "complete"
            return True

    def fail(self, claim, code, **options):
        self.failures.append((claim, code, options))
        with self.operation("fail"):
            if self.state != "running" or claim["execution_generation"] != self.execution:
                return False
            if options.get("cancelled"):
                self.state = "queued"
            else:
                self.state = "retry_wait" if options.get("retryable") else "failed"
                self.attempts += 1
            return True


class LocalCredentials:
    def __init__(self, *, material=None, error=None):
        self.material = material or CredentialMaterial(REGISTRATION, "synthetic-token")
        self.error = error

    def load(self):
        if self.error is not None:
            raise self.error
        return self.material


class LocalClient:
    def __init__(self, store, *, blocked=False, error=None):
        self.store = store
        self.blocked = blocked
        self.error = error
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()
        self.calls = 0
        self.thread = None
        self.closed = False

    async def fetch(self, descriptor, token):
        self.calls += 1
        self.thread = threading.get_ident()
        assert self.store.active == 0, "database operation spans provider HTTP"
        self.entered.set()
        try:
            if self.blocked:
                await self.release.wait()
            if self.error is not None:
                raise self.error
            return {"source": descriptor["kind"], "synthetic": True}
        finally:
            self.exited.set()

    async def aclose(self):
        assert self.store.active == 0, "HTTP closed before owned database work ended"
        self.closed = True


def configuration():
    return Configuration(
        "fixture", "postgresql://unused/intelligence", "c" * 40,
        "o" * 40, "w" * 40, worker_enabled=True, poll_interval=0.05,
    )


async def event_loop_turn():
    completed = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(completed.set_result, None)
    await completed


async def database_entered(gate):
    entered = await asyncio.wait_for(
        asyncio.to_thread(gate.entered.wait, TEST_TIMEOUT), TEST_TIMEOUT,
    )
    assert entered, "database call did not reach the controlled gate"
    assert not gate.watchdog_expired, "synchronous database work blocked the event loop"
    assert not gate.exited.is_set(), "database gate ended before the test released it"


async def close_worker(worker):
    # The original implementation has no executor; keep negative runs leak-free.
    if hasattr(worker, "aclose"):
        await asyncio.wait_for(worker.aclose(), TEST_TIMEOUT)


async def dispose_task(task):
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), TEST_TIMEOUT)
    except (asyncio.CancelledError, Exception):
        pass


async def assert_cancelled(task, message=None):
    completed, _ = await asyncio.wait({task}, timeout=TEST_TIMEOUT)
    assert task in completed, "cancelled worker did not terminate within its test deadline"
    # shield() creates a new cancellation without the original task's message.
    with pytest.raises(asyncio.CancelledError, match=message):
        await task


async def bounded_shutdown(lifespan):
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    try:
        completed, _ = await asyncio.wait({shutdown}, timeout=TEST_TIMEOUT)
        assert shutdown in completed, "application shutdown did not terminate the background loop"
        await shutdown
    finally:
        await dispose_task(shutdown)


def test_worker_without_claim_performs_no_provider_or_failure_operation():
    async def scenario():
        store = LocalStore(available=False)
        client = LocalClient(store)
        worker = Worker(store, LocalCredentials(), client)
        try:
            assert await worker.work_once() is False
            assert client.calls == 0 and store.failures == [] and store.accepted == []
        finally:
            await close_worker(worker)
    asyncio.run(scenario())


def test_successful_slice_offloads_database_calls_and_keeps_http_on_event_loop():
    async def scenario():
        store = LocalStore()
        client = LocalClient(store)
        worker = Worker(store, LocalCredentials(), client)
        loop_thread = threading.get_ident()
        try:
            assert await worker.work_once() is True
            assert len(store.accepted) == 1 and store.state == "complete"
            assert client.thread == loop_thread
            assert all(thread != loop_thread for name, _, thread in store.calls)
            assert store.active == 0 and store.failures == []
        finally:
            await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("credential_case,code", [
    ("missing", "credential_missing"),
    ("mismatch", "credential_registration_mismatch"),
    ("expired", "credential_expired"),
])
def test_credential_rejection_is_persisted_without_contacting_provider(credential_case, code):
    async def scenario():
        store = LocalStore()
        client = LocalClient(store)
        credentials = LocalCredentials()
        if credential_case == "missing":
            credentials.error = CredentialError(code)
        elif credential_case == "mismatch":
            credentials.material = CredentialMaterial("other-registration", "synthetic-token")
        else:
            credentials.material = CredentialMaterial(
                REGISTRATION, "synthetic-token", datetime.now(timezone.utc) - timedelta(days=1),
            )
        worker = Worker(store, credentials, client)
        try:
            assert await worker.work_once() is True
            assert client.calls == 0 and store.failures[0][1] == code
            assert store.accepted == []
        finally:
            await close_worker(worker)
    asyncio.run(scenario())


def test_provider_failure_preserves_its_retry_and_authorization_metadata():
    async def scenario():
        store = LocalStore()
        deadline = datetime.now(timezone.utc) + timedelta(minutes=2)
        client = LocalClient(store, error=ProviderFailure(
            "rate_limited", retryable=True, retry_at=deadline, auth_failed=True,
        ))
        worker = Worker(store, LocalCredentials(), client)
        try:
            assert await worker.work_once() is True
            assert store.failures[0][1:] == ("rate_limited", {
                "retryable": True, "retry_at": deadline, "auth_failed": True,
            })
            assert store.accepted == []
        finally:
            await close_worker(worker)
    asyncio.run(scenario())


def test_normal_cancellation_releases_claim_without_consuming_failure_attempts():
    async def scenario():
        store = LocalStore()
        client = LocalClient(store, blocked=True)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        replacement = None
        try:
            await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
            pending.cancel()
            await assert_cancelled(pending)
            assert client.exited.is_set()
            assert store.state == "queued" and store.attempts == 2
            assert store.failures[0][1:] == ("request_cancelled", {"cancelled": True})
            assert store.accepted == [] and store.active == 0
            replacement = Worker(store, LocalCredentials(), LocalClient(store))
            assert await replacement.work_once() is True
            assert len(store.accepted) == 1 and store.execution == 2
        finally:
            client.release.set()
            await dispose_task(pending)
            await close_worker(worker)
            if replacement is not None:
                await close_worker(replacement)
    asyncio.run(scenario())


def test_cancellation_cleanup_database_error_never_replaces_original_cancellation():
    async def scenario():
        store = LocalStore()
        store.errors["fail"] = RuntimeError("SECRET_DATABASE_CLEANUP_FAILURE")
        client = LocalClient(store, blocked=True)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        try:
            await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
            pending.cancel("original-cancellation")
            await assert_cancelled(pending, "original-cancellation")
            assert pending.cancelled() and client.exited.is_set()
            assert store.state == "running" and store.accepted == []
            assert store.attempts == 2 and store.active == 0
        finally:
            client.release.set()
            await dispose_task(pending)
            await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["claim", "publish", "fail"])
def test_background_database_latency_does_not_block_event_loop(operation):
    async def scenario():
        gate = DatabaseGate()
        store = LocalStore(blocked=operation, gate=gate)
        client = LocalClient(store, error=ProviderFailure("provider_unavailable", retryable=True) if operation == "fail" else None)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        try:
            await database_entered(gate)
            await asyncio.wait_for(event_loop_turn(), TEST_TIMEOUT)
            assert not gate.release.is_set() and not gate.exited.is_set()
            assert not pending.done() and store.claims == 1 and store.active == 1
            gate.release.set()
            assert await asyncio.wait_for(pending, TEST_TIMEOUT) is True
            assert store.active == 0
            assert len(store.accepted) == (0 if operation == "fail" else 1)
        finally:
            gate.release.set()
            await dispose_task(pending)
            await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["claim", "publish", "fail"])
def test_cancellation_drains_inflight_database_operation_before_terminating(operation):
    async def scenario():
        gate = DatabaseGate()
        store = LocalStore(blocked=operation, gate=gate)
        client = LocalClient(store, error=ProviderFailure("provider_unavailable", retryable=True) if operation == "fail" else None)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        try:
            await database_entered(gate)
            pending.cancel("original-cancellation")
            await event_loop_turn()
            pending.cancel("repeated-cancellation")
            await event_loop_turn()
            assert not pending.done() and store.active == 1
            gate.release.set()
            await assert_cancelled(pending, "original-cancellation")
            assert store.active == 0 and store.claims == 1
            if operation == "claim":
                assert client.calls == 0 and store.state == "queued"
                assert store.failures[0][1:] == ("request_cancelled", {"cancelled": True})
            elif operation == "publish":
                assert len(store.accepted) == 1 and store.state == "complete"
                assert store.failures == [], "accepted publication was followed by cancellation cleanup"
            else:
                assert store.accepted == [] and store.attempts == 3
            await close_worker(worker)
            store.close()
            assert not store.closed_while_active
        finally:
            gate.release.set()
            await dispose_task(pending)
            await close_worker(worker)
    asyncio.run(scenario())


def test_repeated_cancellation_during_cleanup_waits_and_preserves_first_cancellation():
    async def scenario():
        gate = DatabaseGate()
        store = LocalStore(blocked="fail", gate=gate)
        client = LocalClient(store, blocked=True)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        try:
            await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
            pending.cancel("first-cancellation")
            await database_entered(gate)
            pending.cancel("second-cancellation")
            await event_loop_turn()
            assert not pending.done() and store.active == 1
            gate.release.set()
            await assert_cancelled(pending, "first-cancellation")
            assert store.state == "queued" and store.attempts == 2
            assert store.active == 0 and len(store.failures) == 1
        finally:
            gate.release.set()
            client.release.set()
            await dispose_task(pending)
            await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["claim", "publish", "fail"])
def test_database_errors_propagate_without_becoming_provider_failures(operation):
    async def scenario():
        store = LocalStore()
        database_error = RuntimeError("synthetic_database_failure")
        store.errors[operation] = database_error
        client = LocalClient(store, error=ProviderFailure("provider_unavailable", retryable=True) if operation == "fail" else None)
        worker = Worker(store, LocalCredentials(), client)
        try:
            with pytest.raises(RuntimeError) as caught:
                await worker.work_once()
            assert caught.value is database_error
            assert len(store.failures) == (1 if operation == "fail" else 0)
            assert store.accepted == [] and store.active == 0
        finally:
            await close_worker(worker)
    asyncio.run(scenario())


def test_lifespan_shutdown_with_failed_cleanup_terminates_without_new_slice(monkeypatch, caplog):
    async def scenario():
        store = LocalStore()
        store.errors["fail"] = RuntimeError("SECRET_DATABASE_CLEANUP_FAILURE")
        client = LocalClient(store, blocked=True)
        worker = Worker(store, LocalCredentials(), client)
        import tempo_intelligence.store as store_module
        monkeypatch.setattr(store_module, "Store", lambda configuration: store)
        app = create_app(configuration(), worker=worker)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        try:
            await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
        finally:
            client.release.clear()
            try:
                await bounded_shutdown(lifespan)
            finally:
                await close_worker(worker)
        assert store.claims == 1 and len(store.failures) == 1
        assert store.closed and not store.closed_while_active and store.active == 0
        assert store.accepted == [] and "SECRET_DATABASE_CLEANUP_FAILURE" not in caplog.text
    asyncio.run(scenario())


def test_foreground_asgi_health_completes_while_background_claim_is_blocked():
    async def scenario():
        gate = DatabaseGate()
        store = LocalStore(blocked="claim", gate=gate)
        worker = Worker(store, LocalCredentials(), LocalClient(store))
        app = create_app(configuration(), store=store, worker=worker)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        try:
            await database_entered(gate)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                response = await asyncio.wait_for(http.get("/healthz"), TEST_TIMEOUT)
            assert response.status_code == 200
            assert response.json() == {"status": "ready", "workspace_id": "fixture"}
            assert not gate.exited.is_set() and store.active == 1
        finally:
            gate.release.set()
            try:
                await bounded_shutdown(lifespan)
            finally:
                await close_worker(worker)
        assert store.active == 0 and not store.closed
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["claim", "publish", "fail"])
def test_lifespan_does_not_close_owned_store_until_inflight_operation_ends(monkeypatch, operation):
    async def scenario():
        gate = DatabaseGate()
        store = LocalStore(blocked=operation, gate=gate)
        client = LocalClient(store, error=ProviderFailure("provider_unavailable", retryable=True) if operation == "fail" else None)
        worker = Worker(store, LocalCredentials(), client)
        import tempo_intelligence.store as store_module
        monkeypatch.setattr(store_module, "Store", lambda configuration: store)
        app = create_app(configuration(), worker=worker)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        shutdown = None
        try:
            await database_entered(gate)
            shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await event_loop_turn()
            assert not shutdown.done() and not store.closed and store.active == 1
            gate.release.set()
            await asyncio.wait_for(asyncio.shield(shutdown), TEST_TIMEOUT)
            assert store.closed and not store.closed_while_active and store.active == 0
            assert store.claims == 1
            operation_end = next(index for index, call in enumerate(store.calls) if call[:2] == (operation, "end"))
            close_index = next(index for index, call in enumerate(store.calls) if call[0] == "close")
            assert operation_end < close_index
            if operation == "publish":
                assert len(store.accepted) == 1 and store.state == "complete"
        finally:
            gate.release.set()
            try:
                if shutdown is None:
                    await bounded_shutdown(lifespan)
                else:
                    await dispose_task(shutdown)
            finally:
                await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_cli_single_slice_closes_its_worker_on_success_and_cancellation(monkeypatch, cancelled):
    async def scenario():
        from tempo_intelligence import cli
        import tempo_intelligence.credentials as credentials_module
        import tempo_intelligence.provider as provider_module
        import tempo_intelligence.worker as worker_module

        store = LocalStore(available=cancelled)
        client = LocalClient(store, blocked=cancelled)
        workers = []
        closed_workers = []

        class TrackedWorker(Worker):
            def __init__(self, *arguments, **options):
                super().__init__(*arguments, **options)
                workers.append(self)

            async def aclose(self):
                if hasattr(super(), "aclose"):
                    await super().aclose()
                closed_workers.append(self)

        monkeypatch.setattr(credentials_module, "PrivateCredentials", lambda path: LocalCredentials())
        monkeypatch.setattr(provider_module, "ExplorerClient", lambda url: client)
        monkeypatch.setattr(worker_module, "Worker", TrackedWorker)
        pending = asyncio.create_task(cli.bounded_work(store, configuration()))
        try:
            if cancelled:
                await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
                pending.cancel()
                await assert_cancelled(pending)
                assert store.state == "queued" and store.attempts == 2
            else:
                assert await asyncio.wait_for(pending, TEST_TIMEOUT) == {"work_performed": False}
            assert len(workers) == 1 and closed_workers == workers
            assert client.closed and store.active == 0
        finally:
            client.release.set()
            await dispose_task(pending)
            for worker in workers:
                await close_worker(worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("available", [False, True])
def test_cancellation_during_executor_join_remains_authoritative(monkeypatch, available):
    async def scenario():
        import tempo_intelligence.worker as worker_module
        from tempo_intelligence.runtime import DatabaseRunner

        runner = DatabaseRunner()
        gate = DatabaseGate()
        original_shutdown = runner.executor.shutdown

        def blocked_shutdown(*arguments, **options):
            gate.wait()
            return original_shutdown(*arguments, **options)

        monkeypatch.setattr(runner.executor, "shutdown", blocked_shutdown)
        monkeypatch.setattr(worker_module, "DatabaseRunner", lambda: runner)
        store = LocalStore(available=available)
        worker = Worker(store, LocalCredentials(), LocalClient(store))
        pending = asyncio.create_task(worker.work_once())
        try:
            await database_entered(gate)
            pending.cancel("executor-join-cancellation")
            await event_loop_turn()
            assert not pending.done() and store.active == 0
            gate.release.set()
            await assert_cancelled(pending, "executor-join-cancellation")
            assert len(store.accepted) == int(available)
        finally:
            gate.release.set()
            await dispose_task(pending)
            await runner.aclose()
            await close_worker(worker)
    asyncio.run(scenario())


def test_repeated_cancellation_during_final_executor_join_preserves_provider_cancellation(monkeypatch):
    async def scenario():
        import tempo_intelligence.worker as worker_module
        from tempo_intelligence.runtime import DatabaseRunner

        runner = DatabaseRunner()
        gate = DatabaseGate()
        original_shutdown = runner.executor.shutdown

        def blocked_shutdown(*arguments, **options):
            gate.wait()
            return original_shutdown(*arguments, **options)

        monkeypatch.setattr(runner.executor, "shutdown", blocked_shutdown)
        monkeypatch.setattr(worker_module, "DatabaseRunner", lambda: runner)
        store = LocalStore()
        client = LocalClient(store, blocked=True)
        worker = Worker(store, LocalCredentials(), client)
        pending = asyncio.create_task(worker.work_once())
        try:
            await asyncio.wait_for(client.entered.wait(), TEST_TIMEOUT)
            pending.cancel("provider-cancellation")
            await database_entered(gate)
            pending.cancel("executor-join-cancellation")
            await event_loop_turn()
            assert not pending.done() and store.active == 0
            gate.release.set()
            await assert_cancelled(pending, "provider-cancellation")
            assert store.state == "queued" and store.attempts == 2
        finally:
            gate.release.set()
            client.release.set()
            await dispose_task(pending)
            await runner.aclose()
            await close_worker(worker)
    asyncio.run(scenario())


def test_lifespan_cancellation_during_owned_store_close_drains_and_joins_runner(monkeypatch):
    async def scenario():
        import tempo_intelligence.api as api_module
        import tempo_intelligence.store as store_module
        from tempo_intelligence.runtime import DatabaseRunner

        gate = DatabaseGate()
        runners = []

        class ClosingStore(LocalStore):
            close_thread = None

            def close(self):
                self.close_thread = threading.current_thread()
                with self.operation("close"):
                    pass
                super().close()

        def create_runner():
            runner = DatabaseRunner()
            runners.append(runner)
            return runner

        store = ClosingStore(blocked="close", gate=gate)
        monkeypatch.setattr(store_module, "Store", lambda configuration: store)
        monkeypatch.setattr(api_module, "DatabaseRunner", create_runner)
        app = create_app(replace(configuration(), worker_enabled=False))
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await database_entered(gate)
            shutdown.cancel("lifespan-close-cancellation")
            await event_loop_turn()
            shutdown.cancel("repeated-lifespan-close-cancellation")
            await event_loop_turn()
            assert not shutdown.done() and store.active == 1
            gate.release.set()
            await assert_cancelled(shutdown, "^lifespan-close-cancellation$")
            assert store.closed and store.active == 0 and not store.closed_while_active
            assert runners and all(runner.closed for runner in runners)
            assert store.close_thread is not None and not store.close_thread.is_alive()
        finally:
            gate.release.set()
            await dispose_task(shutdown)
            for runner in runners:
                await runner.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["construct", "validate_startup"])
def test_lifespan_startup_cancellation_closes_eventually_created_store_and_runner(monkeypatch, operation):
    async def scenario():
        import tempo_intelligence.api as api_module
        import tempo_intelligence.store as store_module
        from tempo_intelligence.runtime import DatabaseRunner

        gate = DatabaseGate()
        runners = []

        class StartingStore(LocalStore):
            close_thread = None

            def validate_startup(self):
                with self.operation("validate_startup"):
                    pass

            def close(self):
                self.close_thread = threading.current_thread()
                super().close()

        store = StartingStore(blocked=operation, gate=gate)

        def create_store(configuration):
            with store.operation("construct"):
                return store

        def create_runner():
            runner = DatabaseRunner()
            runners.append(runner)
            return runner

        monkeypatch.setattr(store_module, "Store", create_store)
        monkeypatch.setattr(api_module, "DatabaseRunner", create_runner)
        app = create_app(replace(configuration(), worker_enabled=False))
        lifespan = app.router.lifespan_context(app)
        startup = asyncio.create_task(lifespan.__aenter__())
        try:
            await database_entered(gate)
            startup.cancel("lifespan-startup-cancellation")
            await event_loop_turn()
            startup.cancel("repeated-lifespan-startup-cancellation")
            await event_loop_turn()
            assert not startup.done() and store.active == 1
            gate.release.set()
            await assert_cancelled(startup, "^lifespan-startup-cancellation$")
            assert store.closed and store.active == 0 and not store.closed_while_active
            assert runners and all(runner.closed for runner in runners)
            assert store.close_thread is not None and not store.close_thread.is_alive()
        finally:
            gate.release.set()
            await dispose_task(startup)
            if startup.done() and not startup.cancelled() and startup.exception() is None:
                await bounded_shutdown(lifespan)
            for runner in runners:
                await runner.aclose()
    asyncio.run(scenario())
