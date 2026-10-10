"""A disposable admission proof must distinguish owned leases from API probes."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path

import pytest

from app.services import redis_admission_gate


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


@pytest.mark.parametrize("proof_script,scope_name", [
    ("check_postgres_queue_attempt_recovery.py", "_owned_queue_refresh_admission"),
    ("check_postgres_next_opponent.py", "_owned_profile_admission"),
])
@pytest.mark.parametrize("fail_proof", [False, True], ids=["complete", "failure-cleanup"])
def test_real_game_queue_proof_isolates_parent_admission_but_retains_own_foreground_denial(
    monkeypatch, fail_proof, proof_script, scope_name,
):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / proof_script
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
        with getattr(proof, scope_name)():
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
