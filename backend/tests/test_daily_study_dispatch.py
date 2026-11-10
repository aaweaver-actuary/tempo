"""Study queue work must claim its lease only at execution capacity."""

from contextlib import nullcontext
from datetime import date

import pytest

from app import tasks


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
