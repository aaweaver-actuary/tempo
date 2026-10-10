"""A disposable admission proof must distinguish owned leases from API probes."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import redis_admission_gate


def test_checkpoint_recovery_driver_waits_for_admission_without_spending_prior_attempts(admission_proof, monkeypatch):
    from app import command_gateway, tasks
    from app.services.activity_gate import activity_gate, _control_section
    proof, _server = admission_proof
    payload = {"checkpoint": {"attempt_id": "original-checkpoint"}}
    recovered = {"operation_id": "owned-recovery", "background": True,
                 "command_name": "opening_evidence.checkpoint", "payload": payload}
    result = {"persisted": True}
    calls, waits = [], []
    outcomes = iter([None, result])
    monkeypatch.setattr(proof.postgres_store, "connection", lambda **_options: nullcontext(
        SimpleNamespace(execute_native=lambda *_arguments: None)))
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)
    monkeypatch.setattr(redis_admission_gate, "foreground_present", lambda: True)
    def claim_receipt():
        assert _control_section.get()
        activity_gate.check_background_admission()
        return recovered
    monkeypatch.setattr(command_gateway, "claim_recoverable_operation", claim_receipt)
    monkeypatch.setattr(proof, "_checkpoint_operation_receipt", lambda _identifier: {
        "state": "retrying", "attempt_count": 2, "last_error_json": None})
    def deliver(*arguments):
        assert not _control_section.get(), "Preparation/publication must retain ordinary admission"
        calls.append(arguments)
        return next(outcomes)
    monkeypatch.setattr(tasks.execute_background_command, "run", deliver)
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: 0, sleep=waits.append))
    assert proof._recover_checkpoint_operation("owned-recovery") == result
    assert calls == [("owned-recovery", "opening_evidence.checkpoint", payload)] * 2
    assert all(call[2] is payload for call in calls)
    assert waits == [0.01]


@pytest.mark.parametrize("receipt", [
    {"state": "retrying", "attempt_count": 3, "last_error_json": None},
    {"state": "retrying", "attempt_count": 2, "last_error_json": '{"error":"SQL timeout"}'},
    {"state": "blocked", "attempt_count": 2, "last_error_json": None},
])
def test_checkpoint_delivery_never_retries_errors_or_spent_attempts(admission_proof, monkeypatch, receipt):
    from app import tasks
    proof, _server = admission_proof
    monkeypatch.setattr(tasks.execute_background_command, "run", lambda *_arguments: None)
    monkeypatch.setattr(proof, "_checkpoint_operation_receipt", lambda _identifier: receipt)
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: 0,
        sleep=lambda _interval: pytest.fail("A real failure must not be retried")))
    assert proof.admitted_checkpoint_delivery("owned", "opening_evidence.checkpoint", {}, prior_attempt_count=2) is None


@pytest.mark.parametrize("deny_first", [False, True], ids=["already-admitted", "admitted-after-denial"])
def test_completed_checkpoint_replay_waits_only_for_explicit_admission_deferral(admission_proof, monkeypatch, deny_first):
    from app import command_gateway
    proof, _server = admission_proof
    payload = {"checkpoint": {"attempt_id": "immutable-replay"}}
    completed = {"state": "complete", "attempt_count": 2, "payload_json": "original"}
    result = {"persisted": True}
    calls, waits = [], []
    def replay(identifier, command, supplied, **options):
        calls.append((identifier, command, supplied, options))
        if deny_first and len(calls) == 1:
            raise redis_admission_gate.BackgroundAdmissionDeferred("Waiting for foreground activity")
        return result
    monkeypatch.setattr(command_gateway, "execute_command", replay)
    monkeypatch.setattr(proof, "_checkpoint_operation_receipt", lambda _identifier: completed.copy())
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: 0, sleep=waits.append))
    assert proof.admitted_checkpoint_replay("owned", payload) is result
    assert calls == [("owned", "opening_evidence.checkpoint", payload, {"background": True})] * (2 if deny_first else 1)
    assert all(call[2] is payload for call in calls)
    assert waits == ([0.01] if deny_first else [])


def test_completed_checkpoint_replay_has_bounded_diagnostics_and_propagates_other_failures(admission_proof, monkeypatch):
    from app import command_gateway
    proof, _server = admission_proof
    completed = {"state": "complete", "attempt_count": 2}
    elapsed, calls = [0], []
    def deny(*arguments, **_options):
        calls.append(arguments)
        raise redis_admission_gate.BackgroundAdmissionDeferred("Waiting for foreground activity")
    def advance(interval):
        assert interval == 0.01
        elapsed[0] += 1
    monkeypatch.setattr(command_gateway, "execute_command", deny)
    monkeypatch.setattr(proof, "_checkpoint_operation_receipt", lambda _identifier: completed.copy())
    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=advance))
    with pytest.raises(AssertionError, match="Checkpoint replay never obtained foreground-idle admission within 10 seconds"):
        proof.admitted_checkpoint_replay("owned", {})
    assert elapsed[0] == 10 and len(calls) == 11
    failure = RuntimeError("Real SQL/publication failure")
    def fail(*_arguments, **_options):
        raise failure
    monkeypatch.setattr(command_gateway, "execute_command", fail)
    with pytest.raises(RuntimeError) as observed:
        proof.admitted_checkpoint_replay("owned", {})
    assert observed.value is failure


def test_completed_checkpoint_replay_rejects_receipt_changes_after_denial(admission_proof, monkeypatch):
    from app import command_gateway
    proof, _server = admission_proof
    receipts = iter([{"state": "complete", "attempt_count": 2}, {"state": "complete", "attempt_count": 3}])
    monkeypatch.setattr(proof, "_checkpoint_operation_receipt", lambda _identifier: next(receipts))
    def deny(*_arguments, **_options):
        raise redis_admission_gate.BackgroundAdmissionDeferred("Waiting for foreground activity")
    monkeypatch.setattr(command_gateway, "execute_command", deny)
    with pytest.raises(AssertionError, match="Denied checkpoint replay changed the completed receipt"):
        proof.admitted_checkpoint_replay("owned", {})


@pytest.fixture
def admission_proof(monkeypatch):
    script_path = Path(__file__).resolve().parents[2] / "scripts/check_postgres_opening_evidence.py"
    specification = importlib.util.spec_from_file_location("opening_admission_proof", script_path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    class AdmissionServer:
        def __init__(self):
            self.leases = {(redis_admission_gate._FOREGROUND_KEY, "independent-api-probe"): 1}

        def eval(self, script, *arguments):
            if script == redis_admission_gate._REGISTER_FOREGROUND:
                self.leases[(arguments[1], arguments[3])] = 1
            elif script == redis_admission_gate._CLAIM_BACKGROUND:
                self.leases[(arguments[2], arguments[4])] = 1
            return 1

        def zscore(self, key, token):
            return self.leases.get((key, token))

        def zrem(self, key, token):
            return self.leases.pop((key, token), None)

    server = AdmissionServer()
    monkeypatch.setattr(redis_admission_gate, "client", lambda: server)
    return module, server


def test_admission_proof_cleanup_ignores_independent_foreground_probe(admission_proof):
    proof, server = admission_proof
    with proof._assert_owned_admission_cleanup():
        with redis_admission_gate.foreground_lease():
            assert len(server.leases) == 2
        assert len(server.leases) == 1
    assert server.zscore(redis_admission_gate._FOREGROUND_KEY, "independent-api-probe") == 1


@pytest.mark.parametrize("work_class", ["foreground", "background"])
def test_admission_proof_cleanup_rejects_owned_lease_leaks(admission_proof, work_class):
    proof, server = admission_proof
    with pytest.raises(AssertionError, match="Admission proof leaked 1 owned leases"):
        with proof._assert_owned_admission_cleanup():
            if work_class == "foreground":
                server.eval(redis_admission_gate._REGISTER_FOREGROUND,
                    1, redis_admission_gate._FOREGROUND_KEY, 0, "leaked-foreground", 30_000)
            else:
                server.eval(redis_admission_gate._CLAIM_BACKGROUND,
                    2, redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY,
                    0, "leaked-background", 5_000)
    assert server.zscore(redis_admission_gate._FOREGROUND_KEY, "independent-api-probe") == 1


def test_admission_proof_cleanup_preserves_original_assertion(admission_proof):
    proof, _server = admission_proof
    with pytest.raises(AssertionError, match="Original admission assertion"):
        with proof._assert_owned_admission_cleanup():
            raise AssertionError("Original admission assertion")


@pytest.mark.parametrize("fail_proof", [False, True], ids=["complete", "failure-cleanup"])
def test_real_game_queue_proof_isolates_parent_admission_but_retains_own_foreground_denial(
    monkeypatch, fail_proof,
):
    script_path = Path(__file__).resolve().parents[2] / "scripts/check_postgres_queue_attempt_recovery.py"
    specification = importlib.util.spec_from_file_location("queue_attempt_proof", script_path)
    proof = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof)
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)
    parent_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY

    class AdmissionServer:
        def __init__(self):
            self.leases = {(parent_keys[0], "parent-request"): 1}
            self.deleted_keys = []

        def eval(self, script, *arguments):
            if script == redis_admission_gate._REGISTER_FOREGROUND:
                self.leases[(arguments[1], arguments[3])] = 1
            elif script == redis_admission_gate._CLAIM_BACKGROUND:
                if any(key == arguments[1] for key, _token in self.leases):
                    return 0
                self.leases[(arguments[2], arguments[4])] = 1
            return 1

        def zrem(self, key, token):
            return self.leases.pop((key, token), None)

        def delete(self, *keys):
            self.deleted_keys.extend(keys)
            self.leases = {identity: value for identity, value in self.leases.items() if identity[0] not in keys}

    server = AdmissionServer()
    monkeypatch.setattr(redis_admission_gate, "client", lambda: server)
    expected_error = pytest.raises(RuntimeError, match="Controlled proof failure") if fail_proof else nullcontext()
    with expected_error:
        with proof._owned_queue_refresh_admission():
            owned_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY
            assert all(key not in parent_keys for key in owned_keys)
            with redis_admission_gate.background_lease():
                assert server.leases[(parent_keys[0], "parent-request")] == 1
            with redis_admission_gate.foreground_lease():
                with pytest.raises(redis_admission_gate.BackgroundAdmissionDeferred):
                    with redis_admission_gate.background_lease():
                        pytest.fail("Owned foreground must still deny background preparation")
            if fail_proof:
                raise RuntimeError("Controlled proof failure")
    assert (redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY) == parent_keys
    assert server.deleted_keys == list(owned_keys)
    assert server.leases == {(parent_keys[0], "parent-request"): 1}
