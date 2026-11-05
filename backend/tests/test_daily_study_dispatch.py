"""Study queue work must claim its lease only at execution capacity."""

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from kombu.exceptions import OperationalError as BrokerUnavailable

from tempo_legacy_database import connections as database
from app import tasks
from app.services import durable_tasks


def test_issue107_browser_fixture_uses_application_day_without_host_clock(monkeypatch):
    import database.tests.check_postgres_daily_study_dispatch as proof

    statements, queue_dates = [], []

    class FixtureDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))

        def commit(self):
            pass

    fixture_database = FixtureDatabase()
    monkeypatch.setattr(
        proof.psycopg, "connect", lambda *_: nullcontext(fixture_database)
    )
    monkeypatch.setattr(
        proof.postgres_store, "connection", lambda: nullcontext(fixture_database)
    )
    monkeypatch.setattr(
        proof,
        "request_queue_refresh_in_transaction",
        lambda _db, day: queue_dates.append(day),
    )

    class HostClockForbidden:
        fromisoformat = staticmethod(date.fromisoformat)

        @staticmethod
        def today():
            pytest.fail("The browser fixture must use its application's supplied day")

    monkeypatch.setattr(proof, "date", HostClockForbidden)
    proof.seed(
        "daily-study-proof-fixture", request_refresh=True, queue_date="2026-10-08"
    )
    assert queue_dates == ["2026-10-08"]
    locked_inserts = [
        parameters
        for statement, parameters in statements
        if "generate_series" in statement and "INSERT INTO cards" in statement
    ]
    assert locked_inserts and all(
        parameters[3] == "2026-10-08" for parameters in locked_inserts
    )


@pytest.fixture
def dispatch_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "dispatch.sqlite")
    database.initialize()
    observed_clock = [datetime(2026, 10, 7, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(durable_tasks, "_now", lambda: observed_clock[0])

    def write(operation, *, label):
        with database.connection(background=True) as connection:
            return operation(connection)

    monkeypatch.setattr(durable_tasks, "submit_background_write", write)
    monkeypatch.setattr(tasks, "measure_handler", lambda *args: nullcontext())
    wakes = []
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: wakes.append((args, kwargs)),
    )
    return observed_clock, wakes


def enqueue_daily():
    return durable_tasks.enqueue_task(
        "daily_queue",
        "current",
        {"queue_date": "2026-10-07"},
        priority=10,
        foreground=False,
    )


def task_row(identifier):
    with database.read_connection() as connection:
        return dict(
            connection.execute(
                "SELECT * FROM background_tasks WHERE id=?", (identifier,)
            ).fetchone()
        )


def test_essential_queue_lease_starts_when_slice_execution_begins(
    dispatch_store, monkeypatch
):
    observed_clock, wakes = dispatch_store
    queued = enqueue_daily()
    observed_clock[0] += timedelta(seconds=120)
    assert task_row(queued["id"])["lease_token"] is None
    executed = []

    def execute_one_slice(claimed):
        executed.append(claimed)
        assert datetime.fromisoformat(claimed["lease_expires_at"]) == observed_clock[
            0
        ] + timedelta(seconds=60)
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(
        tasks, "execute_postgres_queue_refresh_slice", execute_one_slice
    )
    tasks.progress_study_queue.run()
    assert len(executed) == 1
    assert task_row(queued["id"])["state"] == "complete"
    assert wakes == []


def test_essential_queue_legacy_deliveries_preserve_restart_and_replay(
    dispatch_store, monkeypatch
):
    observed_clock, wakes = dispatch_store
    queued = enqueue_daily()
    crashed_delivery = durable_tasks.claim_task("daily_queue")
    observed_clock[0] += timedelta(seconds=61)
    executed = []

    def complete_one(claimed):
        executed.append(claimed["lease_token"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    tasks.progress_study_queue.run()
    assert task_row(queued["id"])["state"] == "complete"
    assert tasks.execute_background_slice.run(crashed_delivery) is False
    tasks.progress_study_queue.run()
    assert len(executed) == 1 and executed[0] != crashed_delivery["lease_token"]
    assert not any(args[0] == "app.tasks.execute_background_slice" for args, _ in wakes)


def test_essential_queue_continuation_broker_failure_retains_committed_slice(
    dispatch_store, monkeypatch
):
    observed_clock, _wakes = dispatch_store
    queued = enqueue_daily()
    executed_phases = []

    def execute_phase(claimed):
        phase = claimed["payload"].get("_queue_phase", "unlock_opening")
        executed_phases.append(phase)
        with database.connection(background=True) as connection:
            if phase == "unlock_opening":
                durable_tasks.advance_task_slice_in_transaction(
                    connection,
                    claimed,
                    next_phase="admit_due",
                    next_payload={
                        "queue_date": "2026-10-07",
                        "_queue_phase": "admit_due",
                    },
                )
                return True
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", execute_phase)

    def unavailable(*args, **kwargs):
        raise BrokerUnavailable("controlled missing continuation wake")

    monkeypatch.setattr(tasks.celery_app, "send_task", unavailable)
    tasks.progress_study_queue.run()
    assert task_row(queued["id"])["state"] == "queued"
    observed_clock[0] += timedelta(seconds=1)
    tasks.progress_study_queue.run()
    assert executed_phases == ["unlock_opening", "admit_due"]
    assert task_row(queued["id"])["state"] == "complete"


def test_background_current_legacy_delivery_commits_once_before_duplicate_wake(
    dispatch_store, monkeypatch
):
    enqueue_daily()
    legacy_delivery = durable_tasks.claim_task("daily_queue")
    executions = []

    def complete_once(claimed):
        executions.append(claimed["id"])
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_once)
    tasks.execute_background_slice.run(legacy_delivery)
    # Commit-before-ack replay and duplicate wakes cannot publish again.
    tasks.execute_background_slice.run(legacy_delivery)
    tasks.poll_background_tasks.run()
    assert executions == [legacy_delivery["id"]]


def test_essential_queue_crash_during_slice_rolls_back_then_recovers_after_lease(
    dispatch_store, monkeypatch
):
    observed_clock, _wakes = dispatch_store
    queued = enqueue_daily()

    class WorkerLost(BaseException):
        pass

    def interrupted(claimed):
        with database.connection(background=True) as connection:
            connection.execute(
                "UPDATE background_tasks SET phase='uncommitted' WHERE id=?",
                (claimed["id"],),
            )
            raise WorkerLost()

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", interrupted)
    with pytest.raises(WorkerLost):
        tasks.progress_study_queue.run()
    assert task_row(queued["id"])["phase"] != "uncommitted"
    assert task_row(queued["id"])["state"] == "leased"
    observed_clock[0] += timedelta(seconds=61)

    def recovered(claimed):
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", recovered)
    tasks.progress_study_queue.run()
    assert task_row(queued["id"])["state"] == "complete"


def test_essential_queue_execution_preserves_pause_priority_promotion_and_delayed_eligibility(
    dispatch_store, monkeypatch
):
    observed_clock, _wakes = dispatch_store
    queued_tasks = [
        durable_tasks.enqueue_task(
            "daily_queue",
            identifier,
            {},
            priority=priority,
            delay_seconds=delay,
            foreground=False,
        )
        for identifier, priority, delay in (
            ("paused", 1, 0),
            ("delayed", 1, 120),
            ("normal", 100, 0),
            ("promoted", 100, 0),
            ("study", 10, 0),
        )
    ]
    with database.connection(background=True) as connection:
        for queued, paused, promoted in (
            (queued_tasks[0], 1, 0),
            (queued_tasks[3], 0, 1),
        ):
            connection.execute(
                "INSERT INTO background_activity(source,work_id,paused,promoted,updated_at) VALUES('durable',?,?,?,?)",
                (queued["id"], paused, promoted, observed_clock[0].isoformat()),
            )
    executed = []

    def complete_one(claimed):
        executed.append(claimed["deduplication_key"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    for _ in range(4):
        tasks.progress_study_queue.run()
    assert executed == ["study", "promoted", "normal"]
    observed_clock[0] += timedelta(seconds=120)
    tasks.progress_study_queue.run()
    assert executed[-1] == "delayed"
    assert task_row(queued_tasks[0]["id"])["state"] == "queued"


def test_background_execution_capacity_yields_to_foreground_without_leasing(
    dispatch_store, monkeypatch
):
    queued = enqueue_daily()
    executed = []

    def complete_one(claimed):
        executed.append(claimed["id"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    with tasks.activity_gate.foreground():
        for _ in range(100):
            assert tasks.poll_background_tasks.run() is False
        assert task_row(queued["id"])["lease_token"] is None
        assert task_row(queued["id"])["attempt_count"] == 0
        assert dispatch_store[1] == []
    tasks.progress_study_queue.run()
    assert executed == [queued["id"]]
    assert task_row(queued["id"])["state"] == "complete"


def test_essential_queue_congested_wakes_complete_without_broker_lease_expiries(
    dispatch_store, monkeypatch
):
    observed_clock, wakes = dispatch_store
    queued_tasks = [
        durable_tasks.enqueue_task(
            "daily_queue", f"delayed-wake-{number}", {}, foreground=False
        )
        for number in range(5)
    ]
    executions = []

    def complete_one(claimed):
        executions.append(claimed["id"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    for _ in queued_tasks:
        # Broker delay belongs to an unleased wake, even beyond the old lease.
        observed_clock[0] += timedelta(seconds=120)
        tasks.progress_study_queue.run()
    with database.read_connection() as connection:
        counters = connection.execute(
            "SELECT COALESCE(SUM(lease_expiries),0),COALESCE(SUM(stale_deliveries),0),COALESCE(SUM(completed_generations),0) FROM background_metric_buckets WHERE kind='daily_queue'"
        ).fetchone()
    assert tuple(counters) == (0, 0, 5)
    assert len(executions) == 5 and wakes == []


def test_denied_background_admission_releases_worker_without_claim_or_failure(
    dispatch_store, monkeypatch
):
    queued = enqueue_daily()
    monkeypatch.setattr(
        tasks,
        "claim_task",
        lambda **kwargs: pytest.fail("denied work must remain unleased"),
    )
    finished = Event()
    errors = []

    def poll():
        try:
            assert tasks.poll_background_tasks.run() is False
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    with tasks.activity_gate.foreground():
        worker = Thread(target=poll)
        worker.start()
        assert finished.wait(1), "foreground denial occupied the worker"
        row = task_row(queued["id"])
        assert row["state"] == "queued" and row["attempt_count"] == 0
        assert row["lease_token"] is None
    worker.join(1)
    assert not worker.is_alive() and errors == []


def test_foreground_arriving_after_claim_preserves_checkpoint_and_resumes(
    dispatch_store, monkeypatch
):
    clock, wakes = dispatch_store
    queued = enqueue_daily()
    with database.connection(background=True) as connection:
        connection.execute(
            "UPDATE background_tasks SET phase='admit_due',payload_json=? WHERE id=?",
            ('{"_queue_phase":"admit_due","after_card_id":"saved"}', queued["id"]),
        )
    executions = []

    def raced_slice(claimed):
        with tasks.activity_gate.foreground():
            with database.connection(background=True) as connection:
                pytest.fail("database admission must yield after foreground arrives")

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", raced_slice)
    legacy = durable_tasks.claim_task("daily_queue")
    assert tasks.execute_background_slice.run(legacy) is False
    saved = task_row(queued["id"])
    assert saved["state"] == "retrying" and saved["attempt_count"] == 0
    assert saved["phase"] == "admit_due" and "saved" in saved["payload_json"]
    assert saved["lease_token"] is None and saved["last_error"] is None and wakes == []
    clock[0] += timedelta(seconds=1)

    def complete(claimed):
        executions.append(claimed["payload"]["after_card_id"])
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete)
    tasks.progress_study_queue.run()
    tasks.progress_study_queue.run()
    assert executions == ["saved"] and task_row(queued["id"])["state"] == "complete"


def test_foreground_deferral_cannot_overwrite_new_generation(dispatch_store):
    queued = enqueue_daily()
    stale_claim = durable_tasks.claim_task("daily_queue")
    # A same-day refresh coalesces into the existing lease; the next day
    # actually replaces the generation and exercises the stale-lease fence.
    replacement = durable_tasks.enqueue_task(
        "daily_queue",
        "current",
        {"queue_date": "2026-10-08"},
        priority=10,
        foreground=False,
    )
    assert replacement["generation"] > stale_claim["generation"]
    with tasks.activity_gate.foreground():
        assert durable_tasks.defer_task_for_foreground(stale_claim) is False
    current = task_row(queued["id"])
    assert (
        current["generation"] == replacement["generation"]
        and current["state"] == "queued"
    )
    assert current["attempt_count"] == 0 and current["lease_token"] is None


def test_retired_envelopes_stop_before_attempt_or_retry(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "record_operation_attempt",
        lambda *a, **k: pytest.fail("retired attempt"),
    )
    monkeypatch.setattr(
        tasks, "record_operation_retry", lambda *a, **k: pytest.fail("retired retry")
    )
    assert (
        tasks.execute_background_command.run(
            "old", "games.analysis.position.report", {}
        )
        is None
    )
    assert (
        tasks.execute_foreground_command.run("old", "endgames.attempt.create", {})
        is None
    )
    assert (
        tasks.execute_background_slice.run({"kind": "defensive_threat_scan"}) is False
    )


def test_transaction_deadline_bookkeeping_does_not_wait_for_raced_foreground(
    dispatch_store, monkeypatch
):
    from psycopg.errors import TransactionTimeout

    queued = enqueue_daily()
    foreground_started = Event()
    release_foreground = Event()

    def foreground():
        with tasks.activity_gate.foreground():
            foreground_started.set()
            assert release_foreground.wait(2)

    worker = Thread(target=foreground)

    def timed_out(claimed):
        worker.start()
        assert foreground_started.wait(1)
        raise TransactionTimeout("controlled deadline after foreground arrival")

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", timed_out)
    legacy = durable_tasks.claim_task("daily_queue")
    try:
        assert tasks.execute_background_slice.run(legacy) is True
        saved = task_row(queued["id"])
        assert saved["state"] == "retrying" and saved["transaction_timeout_count"] == 1
        assert saved["attempt_count"] == 0 and saved["lease_token"] is None
        assert "controlled deadline" in saved["last_error"]
    finally:
        release_foreground.set()
        worker.join(1)
    assert not worker.is_alive()


def test_essential_queue_progress_bypasses_waiting_browser_and_never_claims_optional_work(
    dispatch_store, monkeypatch
):
    queued = enqueue_daily()
    optional = durable_tasks.enqueue_task(
        "opening_graph_rebuild", "optional-poison", {}, priority=-100, foreground=False
    )

    def finish(claimed):
        assert claimed["kind"] == "daily_queue"
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", finish)
    with tasks.activity_gate.foreground():
        assert tasks.poll_background_tasks.run() is False
        assert (
            tasks.progress_study_queue.run() is False
        )  # False means no remaining queue slice.
    assert task_row(queued["id"])["state"] == "complete"
    assert task_row(optional["id"])["state"] == "queued"
    assert task_row(optional["id"])["lease_token"] is None


def test_essential_queue_crash_recovery_keeps_same_generation_and_rejects_old_lease(
    dispatch_store, monkeypatch
):
    clock, _wakes = dispatch_store
    queued = enqueue_daily()
    crashed = durable_tasks.claim_task("daily_queue")
    clock[0] += timedelta(seconds=61)
    observed = []

    def finish(claimed):
        observed.append(claimed)
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", finish)
    with tasks.activity_gate.foreground():
        tasks.progress_study_queue.run()
        assert tasks.execute_background_slice.run(crashed) is False
    assert len(observed) == 1
    assert observed[0]["generation"] == crashed["generation"] == queued["generation"]
    assert observed[0]["lease_token"] != crashed["lease_token"]
    assert task_row(queued["id"])["state"] == "complete"


def test_essential_scope_rejects_optional_kinds_and_releases_on_exception():
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred

    with pytest.raises(ValueError):
        with tasks.activity_gate.essential_study_progress("opening_graph_rebuild"):
            pytest.fail("Optional task obtained essential admission")
    with tasks.activity_gate.foreground():
        with pytest.raises(RuntimeError):
            with tasks.activity_gate.essential_study_progress("daily_queue"):
                tasks.activity_gate.check_background_admission()
                raise RuntimeError("owned slice failed")
        with pytest.raises(BackgroundAdmissionDeferred):
            tasks.activity_gate.check_background_admission()


def test_optional_preparation_capacity_leaves_ordinary_foreground_command_available(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    monkeypatch.setattr(tasks.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        tasks,
        "record_operation_attempt",
        lambda *_args, **_kwargs: (True, {}, "original-token", 1),
    )
    deferred, executed = [], []
    monkeypatch.setattr(
        tasks, "defer_operation_for_foreground", lambda *args: deferred.append(args)
    )
    monkeypatch.setattr(
        tasks,
        "execute_command",
        lambda *_args, **_kwargs: executed.append(True) or {"persisted": True},
    )
    fake = SimpleNamespace(request=SimpleNamespace(headers={}))
    with tasks._preparation_capacity() as acquired:
        assert acquired
        assert (
            tasks._execute_foreground_command(
                fake, "same-operation", "queue.prepare_next", {}
            )
            is None
        )
        assert deferred == [("same-operation", "original-token")]
        assert tasks._execute_foreground_command(
            fake, "review", "cards.review", {}
        ) == {"persisted": True}
    assert len(executed) == 1
    with tasks._preparation_capacity() as acquired:
        assert acquired


def test_foreground_receipt_recovery_uses_existing_essential_only_filter_and_original_identity(
    monkeypatch,
):
    from types import SimpleNamespace

    claims, deliveries = [], []
    monkeypatch.setattr(tasks, "_maintenance_cooldowns", {})
    monkeypatch.setattr(tasks, "study_only_enabled", lambda: True)

    def claim(**options):
        claims.append(options)
        return dict(
            operation_id="original-operation",
            command_name="cards.review",
            payload={"original": "bytes"},
            background=False,
        )

    monkeypatch.setattr(tasks, "claim_recoverable_operation", claim)
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: deliveries.append((args, kwargs)),
    )
    with tasks.activity_gate.foreground():
        assert tasks.recover_study_operations.run()
    assert claims == [
        dict(
            allowed_background_commands=(),
            paused_commands=tasks.RETIRED_COMMANDS | tasks.PAUSED_IMPORT_COMMANDS,
        )
    ]
    assert deliveries[0][0] == ("app.tasks.execute_foreground_command",)
    assert deliveries[0][1]["queue"] == "foreground"
    assert deliveries[0][1]["args"] == [
        "original-operation",
        "cards.review",
        {"original": "bytes"},
    ]
    assert deliveries[0][1]["task_id"] == "original-operation"


def test_essential_rollover_uses_authoritative_study_day_instead_of_worker_host_day(
    monkeypatch,
):
    from app import queue_commands
    from contextlib import contextmanager

    observed = []

    @contextmanager
    def read():
        yield object()

    monkeypatch.setattr(tasks, "read_connection", read)
    monkeypatch.setattr(
        queue_commands, "current_study_day", lambda _database: "2026-10-31"
    )
    monkeypatch.setattr(
        tasks,
        "daily_queue_projection",
        lambda _database, day: observed.append(day) or None,
    )
    monkeypatch.setattr(
        tasks,
        "execute_command",
        lambda _id, _name, payload: (
            observed.append(payload) or {"refresh_pending": True}
        ),
    )
    assert tasks.ensure_daily_queue.run()
    assert observed == ["2026-10-31", {"queue_date": "2026-10-31"}]


@pytest.mark.parametrize("study_only", [False, True])
def test_optional_background_pool_failure_cannot_reserve_essential_queue(
    dispatch_store, monkeypatch, study_only
):
    from psycopg_pool import PoolTimeout

    monkeypatch.setattr(tasks, "_maintenance_cooldowns", {})
    monkeypatch.setattr(tasks, "study_only_enabled", lambda: study_only)
    original_claim = tasks.claim_task
    claims = []

    def claim(*args, **options):
        claims.append(options)
        # Explicit-import predicates are native PostgreSQL receipt joins. This
        # SQLite fixture has no such import; preserve and observe that fallback.
        return (
            None
            if options.get("explicit_imports_only")
            else original_claim(*args, **options)
        )

    monkeypatch.setattr(tasks, "claim_task", claim)
    with database.connection(background=True) as connection:
        connection.execute(
            "UPDATE settings SET study_only_mode=? WHERE id=1", (int(study_only),)
        )
    queued = enqueue_daily()
    optional = durable_tasks.enqueue_task(
        "opening_graph_rebuild",
        "optional-pool-failure",
        {},
        priority=100,
        foreground=False,
    )
    failures = []

    def fail_optional(claimed):
        failures.append(claimed["id"])
        raise PoolTimeout("controlled optional pool failure after its own claim")

    def finish_essential(claimed):
        assert claimed["id"] == queued["id"]
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False

    monkeypatch.setattr(tasks, "execute_postgres_opening_graph_slice", fail_optional)
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", finish_essential)
    assert tasks.poll_background_tasks.run() is False
    head = task_row(queued["id"])
    assert (
        head["state"] == "queued"
        and head["lease_token"] is None
        and head["attempt_count"] == 0
    )
    assert failures == ([] if study_only else [optional["id"]])
    if study_only:
        assert claims == [dict(explicit_imports_only=True)]
    with tasks.activity_gate.foreground():
        tasks.progress_study_queue.run()
    assert task_row(queued["id"])["state"] == "complete"


@pytest.mark.parametrize("study_only", [False, True])
def test_generic_receipt_recovery_reserves_only_background_identity(
    monkeypatch, study_only
):
    claims, deliveries = [], []
    monkeypatch.setattr(tasks, "_maintenance_cooldowns", {})
    monkeypatch.setattr(tasks, "study_only_enabled", lambda: study_only)

    def claim(**options):
        claims.append(options)
        return dict(
            operation_id="original-background",
            command_name="game_evidence.product_accept",
            payload={"original": "bytes"},
            background=True,
        )

    monkeypatch.setattr(tasks, "claim_recoverable_operation", claim)
    monkeypatch.setattr(
        tasks.celery_app,
        "send_task",
        lambda *args, **kwargs: deliveries.append((args, kwargs)),
    )
    assert tasks.recover_operations.run()
    assert claims == [
        dict(
            background_only=True,
            allowed_background_commands=tasks.STUDY_BACKGROUND_COMMANDS
            if study_only
            else None,
            paused_commands=tasks.RETIRED_COMMANDS
            | (tasks.PAUSED_IMPORT_COMMANDS if study_only else frozenset()),
        )
    ]
    assert deliveries[0][0] == ("app.tasks.execute_background_command",)
    assert deliveries[0][1]["task_id"] == "original-background"
    assert deliveries[0][1]["args"] == [
        "original-background",
        "game_evidence.product_accept",
        {"original": "bytes"},
    ]


def test_retired_durable_task_cannot_be_claimed_or_manually_retried(
    dispatch_store, monkeypatch
):
    monkeypatch.setattr(
        durable_tasks, "submit_foreground_write", durable_tasks.submit_background_write
    )
    retired = durable_tasks.enqueue_task(
        "defensive_threat_scan", "old", {}, foreground=False
    )
    assert durable_tasks.claim_task("defensive_threat_scan") is None
    with database.connection(background=True) as connection:
        connection.execute(
            "UPDATE background_tasks SET state='failed' WHERE id=?", (retired["id"],)
        )
    assert durable_tasks.retry_task(retired["id"]) is None
    assert task_row(retired["id"])["state"] == "failed"
    live = enqueue_daily()
    assert (
        durable_tasks.claim_task(
            allowed_kinds=("defensive_threat_scan", "daily_queue")
        )["id"]
        == live["id"]
    )


def test_held_game_projection_chain_remains_claimable():
    # Retain the entire current chain until accepted-obligation replacement is proven.
    stages = {
        "game_derivation_positions",
        "game_derivation_compare",
        "game_derivation_findings",
        "game_derivation_misses",
        "game_derivation_events",
        "game_derivation_features",
        "game_derivation_priorities",
        "repertoire_game_refresh",
    }
    assert stages <= set(tasks._SUPPORTED_BACKGROUND_KINDS)
    assert stages.isdisjoint(tasks.RETIRED_TASK_KINDS)
