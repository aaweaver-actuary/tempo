"""Endgame DB delays must yield the actual Learning ASGI loop to Study."""

import asyncio
from contextlib import contextmanager, nullcontext
import json
from threading import Event, Timer

import httpx

from tempo_learning_runtime import api
from tempo_learning_runtime.config import Configuration
from tempo_learning_runtime.database import current_database


FEN = "8/8/8/8/8/2k5/8/1K6 w - - 0 1"


class FakeDatabase:
    def __init__(self, configuration):
        self.configuration = configuration
        self.entered = Event()
        self.release = Event()
        self.active = 0
        self.closed = False

    @contextmanager
    def scope(self):
        token = current_database.set(self)
        try:
            yield self
        finally:
            current_database.reset(token)

    @contextmanager
    def transaction(self, **options):
        self.active += 1
        try:
            yield self
        finally:
            self.active -= 1

    def execute(self, statement, parameters=()):
        if "tablebase_cache" in statement:
            self.entered.set()
            assert self.release.wait(2), "test delay was not released"
        return self

    def fetchone(self):
        return (json.dumps({"category": "draw", "moves": []}),)

    def close_endgames(self):
        assert self.active == 0

    def close(self):
        self.closed = True


class QuietActivity:
    def __init__(self, *args, **kwargs):
        pass

    def request(self):
        return nullcontext()

    def start(self):
        pass

    def close(self):
        pass


def test_delayed_endgame_cache_does_not_block_ordinary_study_request(monkeypatch):
    databases = []

    def database(configuration):
        result = FakeDatabase(configuration)
        databases.append(result)
        return result

    monkeypatch.setattr(api, "Database", database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    monkeypatch.setattr(api.study_bootstrap, "read_settings", lambda db: {"study_only_mode": True})
    configuration = Configuration("isolation", "unused-writer", "unused-reader", "x" * 40,
                                  maintenance=False, tablebase_url=None)
    app = api.create_app(configuration)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                    headers={"Authorization": "Bearer " + configuration.token}) as client:
            # A timer is an escape hatch for the broken implementation that blocks
            # the loop. The assertion below observes ordering, not a latency SLA.
            release = Timer(1, lambda: [db.release.set() for db in databases])
            release.start()
            probe = asyncio.create_task(client.post("/api/endgames/probe", json={"fen": FEN}))
            try:
                assert await asyncio.to_thread(lambda: any(db.entered.wait(.2) for db in databases))
                study = await asyncio.wait_for(client.get("/api/study/bootstrap"), .5)
                assert study.status_code == 200
                assert not any(db.release.is_set() for db in databases), "Study waited for optional endgame DB"
            finally:
                for db in databases:
                    db.release.set()
                release.cancel()
                response = await probe
            assert response.status_code == 200
            assert response.json()["category"] == "draw"

    asyncio.run(exercise())


def test_endgame_worker_preserves_database_context_and_rejects_busy_capacity():
    from tempo_learning_runtime.endgame_io import EndgameIO, EndgameUnavailable
    from contextvars import ContextVar
    import pytest

    marker = ContextVar("endgame_test_marker")

    async def exercise():
        cleaned = Event()
        worker = EndgameIO(cleaned.set)
        entered, release = Event(), Event()
        observed = []

        def first():
            observed.append(marker.get())
            entered.set()
            assert release.wait(2)

        marker.set("request-a")
        first_task = asyncio.create_task(worker.run(first))
        assert await asyncio.to_thread(entered.wait, 1)
        marker.set("request-b")
        with pytest.raises(EndgameUnavailable, match="busy"):
            await worker.run(lambda: observed.append("must-not-start"))
        assert observed == ["request-a"]
        release.set()
        await first_task
        await worker.run(lambda: observed.append(marker.get()))
        await worker.aclose()
        assert await asyncio.to_thread(cleaned.wait, 1)
        assert observed == ["request-a", "request-b"]

    asyncio.run(exercise())


def test_endgame_cancellation_and_shutdown_leave_active_transaction_with_daemon_owner():
    from tempo_learning_runtime.endgame_io import EndgameIO, EndgameUnavailable
    import pytest

    async def exercise():
        entered, release, left, cleaned = Event(), Event(), Event(), Event()

        def cleanup():
            assert left.is_set()
            cleaned.set()

        worker = EndgameIO(cleanup)

        def transaction():
            entered.set()
            try:
                assert release.wait(2)
            finally:
                left.set()

        running = asyncio.create_task(worker.run(transaction))
        assert await asyncio.to_thread(entered.wait, 1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, .2)
        assert not left.is_set()
        with pytest.raises(EndgameUnavailable, match="busy"):
            await worker.run(lambda: None)
        await asyncio.wait_for(worker.aclose(), .2)
        assert worker._thread.daemon
        assert not left.is_set()
        assert not cleaned.is_set()
        with pytest.raises(EndgameUnavailable, match="closed"):
            await worker.run(lambda: None)
        release.set()
        assert await asyncio.to_thread(cleaned.wait, 1)

    asyncio.run(exercise())


def test_cancelled_unadmitted_endgame_phase_never_enters_worker():
    from tempo_learning_runtime.endgame_io import EndgameIO
    import pytest

    async def exercise():
        worker = EndgameIO()
        observed = []
        task = asyncio.create_task(worker.run(lambda: observed.append("abandoned")))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await worker.aclose()
        assert observed == []

    asyncio.run(exercise())


def test_endgame_database_lane_reserves_study_pool_even_when_pool_size_is_one(monkeypatch):
    """Compose real Database routing with finite fake pool leases and actual ASGI."""
    from threading import Lock
    from tempo_learning_runtime import database as database_module

    entered, release = Event(), Event()
    pools = []

    class Row:
        def __init__(self, values):
            self.values = values

        def fetchone(self):
            return self.values

    class Raw:
        def execute(self, statement, parameters=()):
            if "rolsuper" in statement:
                return Row((False,))
            if "instance.workspace_id" in statement:
                return Row(("reserved", "America/New_York"))
            if "tablebase_cache" in statement:
                entered.set()
                assert release.wait(2)
                return Row((json.dumps({"category": "draw"}),))
            return Row(None)

    class Pool:
        check_connection = staticmethod(lambda connection: None)

        def __init__(self, *args, **kwargs):
            assert kwargs["max_size"] == 1
            self.lease = Lock()
            self.acquisitions = 0
            pools.append(self)

        @contextmanager
        def connection(self):
            assert self.lease.acquire(timeout=.2), "Study shared optional endgame pool"
            self.acquisitions += 1
            try:
                yield Raw()
            finally:
                self.lease.release()

        def close(self):
            assert not self.lease.locked()

    monkeypatch.setattr(database_module, "ConnectionPool", Pool)
    monkeypatch.setattr(database_module, "PostgresConnection", lambda raw, **kwargs: raw)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    monkeypatch.setattr(api.study_bootstrap, "read_settings", lambda db: {"study_only_mode": True})
    configuration = Configuration("reserved", "unused-writer", "unused-reader", "x" * 40,
                                  maintenance=False, pool_size=1, tablebase_url=None)
    app = api.create_app(configuration)
    assert len(pools) == 3  # Optional connections remain lazy.

    async def exercise():
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + configuration.token},
        ) as client:
            probe = asyncio.create_task(client.post("/api/endgames/probe", json={"fen": FEN}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                study = await asyncio.wait_for(client.get("/api/study/bootstrap"), .5)
                assert study.status_code == 200
                assert not release.is_set()
                assert pools[1].acquisitions == 1  # Foreground reader.
                assert len(pools) == 4
                assert pools[3].acquisitions == 1  # Dedicated optional reader.
            finally:
                release.set()
                assert (await probe).status_code == 200

    asyncio.run(exercise())


def test_endgame_attempt_phases_preserve_provider_transaction_and_receipt_boundaries(monkeypatch):
    from threading import get_ident
    from tempo_learning_runtime.database import endgame_lane

    loop_thread = get_ident()
    phase_threads = []
    receipts = {}
    template = {"white_material": "KQ", "black_material": "K", "trained_color": "white", "goal_mix": "both"}

    class AttemptDatabase(FakeDatabase):
        def execute(self, statement, parameters=()):
            assert get_ident() != loop_thread
            assert endgame_lane.get()
            assert self.active == 1
            phase_threads.append(get_ident())
            self.statement, self.parameters = statement, parameters
            return self

        def fetchone(self):
            if "operation_receipts" in self.statement:
                return receipts.get(self.parameters[0])
            if "endgame_templates" in self.statement:
                return template
            if "tablebase_cache" in self.statement:
                return None
            raise AssertionError(self.statement)

    database = AttemptDatabase(None)
    monkeypatch.setattr(api, "Database", lambda configuration: database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    monkeypatch.setattr(api, "generate_position", lambda *args, **kwargs: FEN)
    provider_calls = []
    original_client = httpx.AsyncClient

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 3, "follow_redirects": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params):
            assert get_ident() == loop_thread
            assert database.active == 0
            provider_calls.append(params)
            return httpx.Response(200, json={"category": "draw", "moves": []},
                                  request=httpx.Request("GET", url))

    def dispatch(name, payload, operation_id):
        assert get_ident() != loop_thread
        assert endgame_lane.get()
        assert current_database.get() is database
        assert database.active == 0
        phase_threads.append(get_ident())
        receipts[operation_id] = {"command_name": name, "payload_json": json.dumps(payload)}
        return {"id": "immutable-attempt", **payload}

    monkeypatch.setattr(api.httpx, "AsyncClient", Provider)
    monkeypatch.setattr(api.gateway, "dispatch", dispatch)
    configuration = Configuration("attempt", "unused", "unused", "x" * 40, maintenance=False,
                                  tablebase_url="https://provider.invalid/standard")
    app = api.create_app(configuration)

    async def exercise():
        async with app.router.lifespan_context(app), original_client(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + configuration.token, "Idempotency-Key": "retry-id"},
        ) as client:
            first = await client.post("/api/endgames/templates/template-a/attempt")
            assert first.status_code == 200
            replay = await client.post("/api/endgames/templates/template-a/attempt")
            assert replay.status_code == 200
            assert replay.json() == first.json()
            conflict = await client.post("/api/endgames/templates/template-b/attempt")
            assert conflict.status_code == 409
            assert len(provider_calls) == 1
        assert database.closed
        assert len(phase_threads) == 8  # Lookup, template, cache read/write, dispatch, replay read/dispatch, conflict read.
        assert len(set(phase_threads)) == 1

    asyncio.run(exercise())


def test_optional_endgame_writer_uses_separate_lazy_capacity_and_keeps_foreground_defaults(monkeypatch):
    from tempo_learning_runtime import database as database_module
    from tempo_learning_runtime.endgame_io import EndgameIO

    pools = []

    class Pool:
        check_connection = staticmethod(lambda connection: None)

        def __init__(self, url, **options):
            self.url, self.options = url, options
            pools.append(self)

        def close(self):
            pass

    monkeypatch.setattr(database_module, "ConnectionPool", Pool)
    configuration = Configuration("lane", "unused-writer", "unused-reader", "x" * 40,
                                  maintenance=False, pool_size=1)
    database = database_module.Database(configuration)
    # Inspect the actual selected pool without replacing Database.transaction.
    selected = []

    def select_writer():
        with database_module.connection():
            pass

    @contextmanager
    def lease(pool):
        selected.append(pool)
        raise LookupError("selected")
        yield  # pragma: no cover -- lease intentionally stops before any SQL.

    monkeypatch.setattr(Pool, "connection", lease, raising=False)

    async def exercise():
        import pytest
        worker = EndgameIO()
        with database.scope():
            with pytest.raises(LookupError, match="selected"):
                select_writer()
            with pytest.raises(LookupError, match="selected"):
                await worker.run(select_writer)
            with pytest.raises(LookupError, match="selected"):
                select_writer()
        await worker.aclose()
        database.close()

    asyncio.run(exercise())
    assert selected[0] is database.writer
    assert selected[2] is database.writer
    assert selected[1] is pools[3]
    assert selected[1].url == "unused-writer"
    assert selected[1].options["max_size"] == 1
    assert selected[1].options["timeout"] == .25
    assert len(pools) == 4


def test_asgi_shutdown_finishes_before_delayed_optional_connection_is_released(monkeypatch):
    from tempo_learning_runtime.endgame_io import EndgameIO

    cleaned = Event()

    class OwnedDatabase(FakeDatabase):
        def close_endgames(self):
            assert self.active == 0
            cleaned.set()

        def close(self):
            assert self.active == 1  # Main closes without touching optional I/O.
            self.closed = True

    database = OwnedDatabase(None)
    monkeypatch.setattr(api, "Database", lambda configuration: database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    monkeypatch.setattr(api.study_bootstrap, "read_settings", lambda db: {})
    configuration = Configuration("shutdown", "unused", "unused", "x" * 40,
                                  maintenance=False, tablebase_url=None)
    app = api.create_app(configuration)

    async def exercise():
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                    headers={"Authorization": "Bearer " + configuration.token}) as client:
            probe = asyncio.create_task(client.post("/api/endgames/probe", json={"fen": FEN}))
            try:
                assert await asyncio.to_thread(database.entered.wait, 1)
                assert (await client.get("/api/study/bootstrap")).status_code == 200
                await asyncio.wait_for(lifespan.__aexit__(None, None, None), .3)
                assert database.closed
                assert not cleaned.is_set()
                assert not database.release.is_set()
                response = await asyncio.wait_for(probe, .3)
                assert response.status_code == 503
                assert response.json()["detail"]["state"] == "unknown"
            finally:
                database.release.set()
            assert await asyncio.to_thread(cleaned.wait, 1)

    asyncio.run(exercise())


def test_late_worker_exception_after_request_loop_exit_keeps_optional_cleanup_owner():
    from tempo_learning_runtime.endgame_io import EndgameIO
    import pytest

    entered, release, cleaned = Event(), Event(), Event()
    worker = EndgameIO(cleaned.set)

    def phase():
        entered.set()
        assert release.wait(2)
        raise ValueError("late optional failure")

    async def exercise():
        request = asyncio.create_task(worker.run(phase))
        assert await asyncio.to_thread(entered.wait, 1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await worker.aclose()
        assert not cleaned.is_set()

    asyncio.run(exercise())
    # The original event loop is gone; result delivery must not require it.
    release.set()
    assert cleaned.wait(1)


def test_endgame_lane_rejects_maintenance_escape_and_main_close_never_takes_optional_lock(monkeypatch):
    from tempo_learning_runtime import database as database_module
    import pytest

    closed = []

    class Pool:
        check_connection = staticmethod(lambda connection: None)

        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            closed.append(self)

    monkeypatch.setattr(database_module, "ConnectionPool", Pool)
    database = database_module.Database(Configuration("close", "unused", "unused", "x" * 40))
    token = database_module.endgame_lane.set(True)
    try:
        with pytest.raises(RuntimeError, match="maintenance"):
            with database.transaction(background=True):
                raise AssertionError("must not start")
    finally:
        database_module.endgame_lane.reset(token)
    # A stalled optional constructor/cleanup lock cannot become main-close work.
    with database._endgame_pool_lock:
        database.close()
    assert closed == [database.writer, database.reader, database.maintenance]


def test_timed_out_gateway_can_commit_then_replay_same_operation_without_duplicate(monkeypatch):
    """Actual gateway authority; fake persistence does not replace native PG proof."""
    from tempo_learning_runtime.endgame_io import EndgameIO

    entered, release, committed, cleaned = Event(), Event(), Event(), Event()
    receipts, effects = {}, []
    template = {"white_material": "KQ", "black_material": "K", "trained_color": "white", "goal_mix": "both"}

    class ReceiptDatabase(FakeDatabase):
        def execute(self, statement, parameters=()):
            if "operation_receipts" in statement:
                self.row = receipts.get(parameters[0])
            elif "endgame_templates" in statement:
                self.row = template
            elif "tablebase_cache" in statement:
                self.row = (json.dumps({"category": "draw", "moves": []}),)
            else:
                raise AssertionError(statement)
            return self

        def execute_native(self, statement, parameters=()):
            assert self.active == 1
            if statement.startswith("INSERT INTO operation_receipts"):
                operation_id, name, digest, payload = parameters
                receipts.setdefault(operation_id, {"operation_id": operation_id, "command_name": name,
                    "request_hash": digest, "payload_json": payload, "state": "pending", "response_json": None, "error_json": None})
                self.row = None
            elif statement.startswith("SELECT command_name,request_hash"):
                receipt = receipts[parameters[0]]
                self.row = (receipt["command_name"], receipt["request_hash"])
            elif statement.startswith("SELECT * FROM operation_receipts"):
                self.row = receipts.get(parameters[0])
            elif statement.startswith("SELECT pg_advisory") or statement.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")):
                self.row = None
            elif statement.startswith("UPDATE operation_receipts SET state='complete'"):
                receipts[parameters[1]].update(state="complete", response_json=parameters[0])
                committed.set()
            else:
                raise AssertionError(statement)
            return self

        def fetchone(self):
            return self.row

        def close_endgames(self):
            assert self.active == 0
            cleaned.set()

    database = ReceiptDatabase(None)

    def handler(db, payload):
        entered.set()
        assert release.wait(2)
        effects.append(payload["template_id"])
        return {"id": "one-attempt", **payload}

    monkeypatch.setattr(api, "Database", lambda configuration: database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    monkeypatch.setattr(api, "EndgameIO", lambda close: EndgameIO(close, wait_seconds=.05))
    monkeypatch.setattr(api, "generate_position", lambda *args, **kwargs: FEN)
    monkeypatch.setitem(api.gateway._handlers, "endgames.attempt.create", handler)
    app = api.create_app(Configuration("retry", "unused", "unused", "x" * 40,
                                       maintenance=False, tablebase_url=None))

    async def exercise():
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + "x" * 40},
        ) as client:
            # No idempotency header: the server must reveal its generated ID
            # rather than report that the admitted write failed or rolled back.
            pending = await client.post("/api/endgames/templates/template-a/attempt")
            assert entered.is_set()
            assert pending.status_code == 503
            detail = pending.json()["detail"]
            operation_id = detail["operation_id"]
            assert detail["state"] == "unknown"
            assert pending.headers["location"] == "/api/operations/" + operation_id
            assert receipts[operation_id]["state"] == "pending"
            retry_headers = {"Idempotency-Key": operation_id}
            busy = await client.post("/api/endgames/templates/template-a/attempt", headers=retry_headers)
            assert busy.status_code == 503
            assert busy.json()["detail"]["operation_id"] == operation_id
            assert effects == []
            release.set()
            assert await asyncio.to_thread(committed.wait, 1)
            for _ in range(50):
                replay = await client.post("/api/endgames/templates/template-a/attempt", headers=retry_headers)
                if replay.status_code == 200:
                    break
                assert replay.status_code == 503
                await asyncio.sleep(.001)
            assert replay.status_code == 200
            assert replay.json()["id"] == "one-attempt"
            assert len(receipts) == 1
            assert effects == ["template-a"]
            assert receipts[operation_id]["state"] == "complete"
        assert await asyncio.to_thread(cleaned.wait, 1)

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_daemon_endgame_phase_does_not_join_interpreter_shutdown():
    """A fresh process exits even when its optional phase never returns."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent('''
        import asyncio
        from threading import Event
        from tempo_learning_runtime.endgame_io import EndgameIO, EndgameUnavailable
        entered = Event()
        never = Event()
        worker = EndgameIO()
        def stalled():
            entered.set()
            never.wait()
        async def main():
            pending = asyncio.create_task(worker.run(stalled))
            while not entered.is_set():
                await asyncio.sleep(.001)
            await worker.aclose()
            try:
                await pending
            except EndgameUnavailable:
                pass
        asyncio.run(main())
        print("optional phase detached; process exiting", flush=True)
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=3, check=True)
    assert "process exiting" in result.stdout


def test_asgi_cancelled_probe_does_not_wait_for_optional_connection(monkeypatch):
    import pytest

    cleaned = Event()

    class OwnedDatabase(FakeDatabase):
        def close_endgames(self):
            assert self.active == 0
            cleaned.set()

    database = OwnedDatabase(None)
    monkeypatch.setattr(api, "Database", lambda configuration: database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    configuration = Configuration("cancel", "unused", "unused", "x" * 40,
                                  maintenance=False, tablebase_url=None)
    app = api.create_app(configuration)

    async def exercise():
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + configuration.token},
        ) as client:
            request = asyncio.create_task(client.post("/api/endgames/probe", json={"fen": FEN}))
            try:
                assert await asyncio.to_thread(database.entered.wait, 1)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request, .3)
                assert database.active == 1
                assert not database.release.is_set()
                assert not cleaned.is_set()
            finally:
                database.release.set()
        assert await asyncio.to_thread(cleaned.wait, 1)

    asyncio.run(exercise())
