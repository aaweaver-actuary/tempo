"""A disposable admission proof must distinguish owned leases from API probes."""

import importlib.util
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
