"""A disposable admission proof must distinguish owned leases from API probes."""

import importlib.util
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from app.services import redis_admission_gate


@pytest.fixture
def admission_proof(monkeypatch):
    script_path = Path(__file__).resolve().parents[2] / "scripts/check_postgres_opening_evidence.py"
    monkeypatch.syspath_prepend(str(script_path.parent))
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
def test_checkpoint_http_proof_isolates_receipt_setup_from_parent_foreground(
    admission_proof, monkeypatch, fail_proof,
):
    from app import command_gateway, postgres_store

    proof, _unused_server = admission_proof
    parent_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY
    parent_lease = (parent_keys[0], "independent-api-probe")
    setup_events, writer_entries, proof_keys = [], [], []

    class AdmissionServer:
        def __init__(self):
            self.leases = {parent_lease: 1}
            self.deleted_keys = []

        def eval(self, script, *arguments):
            if script == redis_admission_gate._REGISTER_FOREGROUND:
                self.leases[(arguments[1], arguments[3])] = 1
            elif script == redis_admission_gate._CLAIM_BACKGROUND:
                if any(key == arguments[1] for key, _token in self.leases):
                    return 0
                self.leases[(arguments[2], arguments[4])] = 1
            return 1

        def pipeline(self, **options):
            return self

        def zremrangebyscore(self, *arguments):
            return self

        def zcard(self, key):
            self.cardinality = sum(lease_key == key for lease_key, _token in self.leases)
            return self

        def execute(self):
            return [0, self.cardinality]

        def zadd(self, key, entries):
            self.leases.update({(key, token): value for token, value in entries.items()})

        def zscore(self, key, token):
            return self.leases.get((key, token))

        def zrem(self, key, token):
            return self.leases.pop((key, token), None)

        def delete(self, *keys):
            self.deleted_keys.extend(keys)
            self.leases = {identity: value for identity, value in self.leases.items() if identity[0] not in keys}

    server = AdmissionServer()
    monkeypatch.setattr(redis_admission_gate, "client", lambda: server)
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: True)
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    payload = {"checkpoint": {}, "prepared_manifest": {"obsolete": True}}
    command_name = "opening_evidence.checkpoint"

    class ReceiptCursor:
        def execute(self, statement, parameters):
            if statement.startswith("SELECT command_name,request_hash"):
                self.receipt = (command_name, command_gateway.request_digest(command_name, payload),
                                "queued", json.dumps(payload), None, None, 0, 0, 0)
            return self

        def fetchone(self):
            return self.receipt

    @contextmanager
    def admitted_writer(**options):
        writer_entries.append(options)
        yield SimpleNamespace(raw=ReceiptCursor())

    monkeypatch.setattr(postgres_store, "connection", admitted_writer)

    def checkpoint_proof():
        setup_events.append("receipt setup")
        assert command_gateway.record_operation_attempt("checkpoint-setup", command_name, payload, background=True)[0]
        proof_keys.extend((redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY))
        assert all(key not in parent_keys for key in proof_keys)
        assert len(writer_entries) == 1
        setup_events.append("owned foreground contention")
        with redis_admission_gate.foreground_lease():
            with pytest.raises(redis_admission_gate.BackgroundAdmissionDeferred):
                command_gateway.record_operation_attempt("denied-setup", command_name, payload, background=True)
        assert len(writer_entries) == 1, "Denied setup opened a writer"
        for state in ("queued", "retrying"):
            setup_events.append(state + " recovery setup")
            claimed, saved, _token, _attempts = command_gateway.record_operation_attempt(
                "checkpoint-" + state, command_name, payload, background=True,
            )
            assert claimed and saved == payload
            assert server.leases[parent_lease] == 1
        if fail_proof:
            raise RuntimeError("Controlled checkpoint proof failure")

    monkeypatch.setattr(proof, "_prove_opening_checkpoint_http_admission_preserves_saved_payload_replay", checkpoint_proof)
    expected_error = pytest.raises(RuntimeError, match="Controlled checkpoint proof failure") if fail_proof else nullcontext()
    with expected_error:
        proof.test_postgres_opening_checkpoint_http_admission_preserves_saved_payload_replay()
    assert setup_events == ["receipt setup", "owned foreground contention", "queued recovery setup", "retrying recovery setup"]
    assert len(writer_entries) == 3
    assert (redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY) == parent_keys
    assert server.deleted_keys == proof_keys
    assert server.leases == {parent_lease: 1}


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
