"""Issue #37: observable progress is distinct from queue and delivery activity."""

import json
import threading


from app.services import background_runtime
from app.services.background_metrics import increment, BackgroundDiagnostics
from app.services.activity_gate import ApplicationActivityGate


def test_background_admission_wait_is_separate_from_handler_execution(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(background_runtime.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        background_runtime.redis_admission_gate, "configured", lambda: False
    )
    with background_runtime.measure_handler("daily_queue") as measurement:
        clock[0] = 2
        with background_runtime.admission_wait():
            clock[0] = 5
            with background_runtime.admission_wait():
                clock[0] = 8
        clock[0] = 10
        result = measurement.sample()
    assert result.admission_wait_seconds == 6
    assert result.handler_elapsed_seconds == 10
    assert result.execution_seconds == 4


def test_background_diagnostics_preserves_foreground_responsiveness(monkeypatch):
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    gate = ApplicationActivityGate()
    entered = threading.Event()
    finished = threading.Event()
    samples = []

    def background():
        with background_runtime.measure_handler("daily_queue") as measurement:
            entered.set()
            with gate.background_database_section():
                samples.append(measurement.sample())
        finished.set()

    with gate.foreground():
        worker = threading.Thread(target=background)
        worker.start()
        assert entered.wait(1)
        assert not finished.wait(0.02)
        assert gate.foreground_requests_active
    worker.join(1)
    assert finished.is_set()
    assert samples[0].admission_wait_seconds > 0


def test_postgres_background_counter_flush_follows_domain_writes_and_sorts_locks():
    from tempo_storage.postgres import PostgresConnection

    statements = []

    class Raw:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))

        def commit(self):
            statements.append(("COMMIT", ()))

        def rollback(self):
            statements.append(("ROLLBACK", ()))

    connection = PostgresConnection(Raw())
    increment(connection, "engine_game", "same", claims=1)
    increment(connection, "daily_queue", "other", claims=1)
    increment(connection, "engine_game", "same", claims=1)
    connection.execute_native("domain publication")
    assert [statement for statement, _ in statements] == ["domain publication"]
    connection.commit()
    assert [
        parameters[0]
        for statement, parameters in statements
        if statement.startswith("INSERT")
    ] == ["daily_queue", "engine_game"]
    assert statements[-2][1][-1] == 2
    assert statements[-1][0] == "COMMIT"
    statements.clear()
    increment(connection, "engine_game", "same", claims=1)
    connection.rollback()
    connection.commit()
    assert not any(statement.startswith("INSERT") for statement, _ in statements)


def test_background_runbook_snapshots_validate_without_private_fields():
    from pathlib import Path

    examples = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/background-diagnostics-example.json"
        ).read_text()
    )
    assert [example["period"] for example in examples] == ["quiet", "training", "drain"]
    for example in examples:
        snapshot = BackgroundDiagnostics.model_validate(example["snapshot"])
        assert snapshot.available
        assert all(
            entry.kind
            in {"engine_game", "discovery_recommendation", "game_analysis_publish"}
            for entry in snapshot.counters
        )


def test_background_admission_timing_excludes_diagnostic_publication(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(background_runtime.time, "monotonic", lambda: clock[0])

    def publish(measurement, **kwargs):
        if measurement.stage == "foreground_admission":
            clock[0] += 2

    monkeypatch.setattr(background_runtime.RuntimeMeasurement, "publish", publish)
    with background_runtime.measure_handler("daily_queue") as measurement:
        with background_runtime.admission_wait():
            clock[0] += 3
        assert measurement.sample().admission_wait_seconds == 3
