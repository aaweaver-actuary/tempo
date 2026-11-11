"""Admission denial releases capacity and unavailable evidence never admits."""

from contextlib import contextmanager

import pytest
import redis

from app.services import redis_admission_gate
from app.services.activity_gate import (
    ApplicationActivityGate,
    BackgroundAdmissionDeferred,
)


@pytest.mark.parametrize("response", [0, redis.ConnectionError("controlled outage")])
def test_redis_background_admission_checks_once_and_never_sleeps(monkeypatch, response):
    calls = []

    class Server:
        def eval(self, *args):
            calls.append(args)
            if isinstance(response, Exception):
                raise response
            return response

        def zrem(self, *args):
            pytest.fail("unowned lease must not be released")

    monkeypatch.setattr(redis_admission_gate, "client", lambda: Server())
    monkeypatch.setattr(
        redis_admission_gate.time, "sleep", lambda *args: pytest.fail("occupied worker")
    )
    with pytest.raises(BackgroundAdmissionDeferred):
        with redis_admission_gate.background_lease():
            pytest.fail("denied lease")
    assert len(calls) == 1


def test_admission_redis_outage_is_unavailable_and_recovers(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)

    def outage():
        raise redis.ConnectionError("controlled outage")

    monkeypatch.setattr(redis_admission_gate, "foreground_present", outage)
    gate = ApplicationActivityGate()
    with pytest.raises(BackgroundAdmissionDeferred, match="unavailable"):
        gate.check_background_admission()
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: False)
    gate.check_background_admission()


def test_control_bookkeeping_does_not_claim_shared_admission(monkeypatch):
    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)

    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred("foreground")
        yield

    monkeypatch.setattr(redis_admission_gate, "background_lease", denied)
    with (
        gate.background_job("test", "control", yielding=True),
        gate.background_control(),
    ):
        with gate.background_database_section():
            assert gate.active_background_sections == 1
    assert gate.active_background_sections == 0
    with pytest.raises(BackgroundAdmissionDeferred):
        with gate.background_database_section():
            pytest.fail("control context leaked")




def test_short_control_receipt_is_admitted_during_existing_background_reservation(
    monkeypatch,
):
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    gate = ApplicationActivityGate()
    with gate.background_job("test", "reservation", yielding=True):
        with gate.background_database_section():
            with gate.background_control(), gate.background_database_section():
                assert gate.active_background_sections == 2
            assert gate.active_background_sections == 1
            with pytest.raises(BackgroundAdmissionDeferred):
                with gate.background_database_section():
                    pytest.fail("discretionary section must yield")
    assert gate.active_background_sections == 0




@pytest.mark.parametrize("receipt_exists", [True, False])
def test_background_receipt_read_is_prompt_during_foreground_without_false_success(
    monkeypatch, receipt_exists
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import command_gateway
    from tempo_storage import postgres
    from app.services.activity_gate import activity_gate

    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setattr(postgres, "configured", lambda: True)
    receipt = (
        (
            "coverage.maia.submit",
            "digest",
            "retrying",
            None,
            None,
            0,
            None,
            None,
            None,
            "{}",
            0,
            0,
        )
        if receipt_exists
        else None
    )
    observations = []

    def read(statement, parameters):
        assert "FROM operation_receipts WHERE operation_id=%s" in statement
        assert parameters == ("saved-receipt",)
        return SimpleNamespace(fetchone=lambda: receipt)

    @contextmanager
    def connection(**options):
        observations.append(options)
        yield SimpleNamespace(raw=SimpleNamespace(execute=read))

    monkeypatch.setattr(postgres, "connection", connection)
    with activity_gate.foreground(), activity_gate.background_request():
        result = command_gateway.read_operation("saved-receipt", background=True)
        assert result["state"] == ("retrying" if receipt_exists else "unknown")
        assert "response" not in result
        assert activity_gate.active_background_sections == 0
        assert not activity_gate.in_background_control
    assert observations == [{"read_only": True, "background": True}]




def test_foreground_receipt_denial_does_not_consume_failure_attempts(
    monkeypatch,
):
    from contextlib import nullcontext
    from app import tasks

    command_name = "queue.bury"
    monkeypatch.setattr(tasks.activity_gate, "foreground", nullcontext)
    monkeypatch.setattr(
        tasks,
        "record_operation_attempt",
        lambda *_args, **_kwargs: (True, {"saved": True}, "fenced-attempt", 1),
    )

    def denied(*_args, **_kwargs):
        raise BackgroundAdmissionDeferred("Waiting for study")

    monkeypatch.setattr(tasks, "execute_command", denied)
    observed = []
    monkeypatch.setattr(
        tasks, "defer_operation_for_foreground", lambda *args: observed.append(args)
    )
    monkeypatch.setattr(
        tasks,
        "record_operation_retry",
        lambda *_args, **_kwargs: pytest.fail("denial spent failure budget"),
    )
    assert (
        tasks.execute_foreground_command.run("retained-apply", command_name, {}) is None
    )
    assert observed == [("retained-apply", "fenced-attempt")]


def test_foreground_wait_reason_on_explicit_producers(monkeypatch):
    import io
    from app.services import activity_gate as gate_module

    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    gate.record_browser_activity(3, session_id="reason-test")
    with pytest.raises(BackgroundAdmissionDeferred) as local:
        gate.check_background_admission()
    assert local.value.reason == "foreground_wait"

    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: True)
    with pytest.raises(BackgroundAdmissionDeferred) as shared:
        gate.check_background_admission()
    assert shared.value.reason == "foreground_wait"

    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    monkeypatch.setenv("TEMPO_FOREGROUND_ACTIVITY_URL", "http://example.invalid")
    monkeypatch.setattr(
        gate_module, "urlopen", lambda *args, **kwargs: io.BytesIO(b'{"active": true}')
    )
    with pytest.raises(BackgroundAdmissionDeferred) as remote:
        gate.check_background_admission()
    assert remote.value.reason == "foreground_wait"

    class DeniedLease:
        def eval(self, *args):
            return 0

    monkeypatch.setattr(redis_admission_gate, "client", lambda: DeniedLease())
    with pytest.raises(BackgroundAdmissionDeferred) as lease:
        with redis_admission_gate.background_lease():
            pytest.fail("denied lease")
    assert lease.value.reason == "foreground_wait"
