"""Scoped HTTP and explicit configuration regressions require no database."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import threading

from fastapi.testclient import TestClient
import pytest

from tempo_intelligence import Configuration, create_app
from tempo_intelligence.config import private_text
from tempo_intelligence.errors import DomainError


def configuration(**changes):
    values = dict(workspace_id="fixture", database_url="postgresql://unused/intelligence", caller_token="c" * 40, operator_token="o" * 40, worker_token="w" * 40, worker_enabled=False)
    values.update(changes)
    return Configuration(**values)


class FakeStore:
    def __init__(self):
        self.calls = []

    def validate_startup(self):
        self.calls.append(("startup",))

    def health(self):
        pass

    def inspect(self, request_id):
        self.calls.append(("inspect", request_id))
        if request_id == "missing":
            raise DomainError("request_not_found", 404)
        if request_id == "unavailable":
            raise RuntimeError("postgresql://private:SECRET@database/private")
        return {"request_id": request_id, "sources": []}

    def submit(self, payload):
        self.calls.append(("submit", payload.request_id))
        return {"request_id": payload.request_id}

    def accept_maia(self, payload):
        self.calls.append(("accept_maia", payload.job_id))
        return {"job_id": payload.job_id, "status": "accepted"}

    def maia_status(self, payload):
        self.calls.append(("maia_status", payload.job_id, payload.status))
        return {"job_id": payload.job_id, "status": payload.status}

    def evidence(self, evidence_id):
        self.calls.append(("evidence", evidence_id))
        return {"evidence_id": evidence_id}

    def refresh(self, request_id, command_id, expected_revision):
        self.calls.append(("refresh", request_id, command_id, expected_revision))
        return {"requested": request_id}

    def retry(self, request_id, command_id, expected_revision):
        self.calls.append(("retry", request_id, command_id, expected_revision))
        return {"requested": request_id}

    def maia_jobs(self, request_id):
        self.calls.append(("maia_jobs", request_id))
        return []

    def credential_revoke(self, expected_generation):
        self.calls.append(("revoke", expected_generation))
        return {"generation": expected_generation + 1, "status": "revoked"}

    def credential_reload(self, material, expected_generation):
        self.calls.append(("reload", material.registration_id, expected_generation))
        return {"generation": expected_generation + 1, "status": "usable"}


def authorization(scope="caller"):
    token = {"caller": "c", "operator": "o", "worker": "w"}[scope] * 40
    return {"Authorization": "Bearer " + token}


def test_authentication_precedes_request_validation_and_database_use():
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store)) as client:
        assert client.get("/healthz").json() == {"status": "ready", "workspace_id": "fixture"}
        assert client.get("/v1/requests/one").status_code == 401
        assert client.post("/v1/requests", json={}).status_code == 401
        assert client.get("/v1/requests/one", headers=authorization("operator")).status_code == 401
        assert client.get("/v1/requests/one", headers=authorization("worker")).status_code == 401
        assert store.calls == [("startup",)]


def test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands():
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store), headers=authorization()) as client:
        assert client.get("/v1/requests/one").json()["request_id"] == "one"
        assert client.get("/v1/evidence/saved").json() == {"evidence_id": "saved"}
        assert client.get("/v1/requests/one/maia-jobs").json() == []
        for operation in ("refresh", "retry"):
            assert client.post("/v1/requests/one/" + operation, json={"command_id": operation + "-1", "expected_revision": 2}).status_code == 200
            assert (operation, "one", operation + "-1", 2) in store.calls


def test_valid_versioned_requests_and_worker_payloads_reach_their_own_ports():
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    requested = json.loads((fixtures / "request-v1.json").read_text())
    result = json.loads((fixtures / "maia-result-v1.json").read_text())
    status = {key: result[key] for key in ("contract_version", "workspace_id", "job_id", "request_revision", "execution_generation")}
    status.update(status="running", retryable=False, reason=None)
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store)) as client:
        response = client.post("/v1/requests", json=requested, headers=authorization())
        assert response.status_code == 200 and response.json()["request_id"] == requested["request_id"]
        response = client.post("/v1/maia/results", json=result, headers=authorization("worker"))
        assert response.status_code == 200 and response.json()["status"] == "accepted"
        response = client.post("/v1/maia/status", json=status, headers=authorization("worker"))
        assert response.status_code == 200 and response.json()["status"] == "running"
    assert ("submit", requested["request_id"]) in store.calls
    assert ("accept_maia", result["job_id"]) in store.calls
    assert ("maia_status", result["job_id"], "running") in store.calls


@pytest.mark.parametrize("body", [{}, {"command_id": "x", "expected_revision": 0}, {"command_id": "x", "expected_revision": True}, {"command_id": "x", "token": "DO_NOT_ECHO_SECRET"}])
def test_strict_command_errors_never_echo_unrecognized_private_input(body):
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store), headers=authorization()) as client:
        response = client.post("/v1/requests/one/refresh", json=body)
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request"}
        assert store.calls == [("startup",)]


def test_scoped_credentials_and_workspace_header_cannot_be_substituted():
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store)) as client:
        for scope in ("caller", "worker"):
            assert client.post("/v1/credentials/lichess-explorer/revoke", json={"expected_generation": 0}, headers=authorization(scope)).status_code == 401
        assert client.post("/v1/credentials/lichess-explorer/revoke", json={"expected_generation": 0}, headers=authorization("operator")).status_code == 200
        for scope in ("caller", "operator"):
            assert client.post("/v1/maia/results", json={}, headers=authorization(scope)).status_code == 401
            assert client.post("/v1/maia/status", json={}, headers=authorization(scope)).status_code == 401
        assert client.post("/v1/maia/results", json={}, headers=authorization("worker")).status_code == 422
        assert client.get("/v1/requests/one", headers={**authorization(), "X-Tempo-Workspace": "other"}).status_code == 403
        assert client.get("/healthz", headers={"Host": "untrusted.example"}).status_code == 400


def test_credential_reload_uses_only_configured_file_and_generation(tmp_path):
    credential = tmp_path / "provider.json"
    secret = "TEST_ONLY_NEVER_IN_RESPONSE"
    credential.write_text(json.dumps({"schema_version": 1, "registration_id": "144d69cd-024d-4a5a-a5c5-55298df06e04", "token": secret}))
    credential.chmod(0o600)
    store = FakeStore()
    with TestClient(create_app(configuration(credential_file=credential), store=store), headers=authorization("operator")) as client:
        response = client.post("/v1/credentials/lichess-explorer/reload", json={"expected_generation": 4})
        assert response.status_code == 200, response.text
        assert response.json()["generation"] == 5
        assert secret not in response.text
        assert ("reload", "144d69cd-024d-4a5a-a5c5-55298df06e04", 4) in store.calls
        assert client.post("/v1/credentials/lichess-explorer/reload", json={"expected_generation": 5, "path": "/private/arbitrary"}).status_code == 422


def test_missing_optional_provider_credential_does_not_block_startup_or_reads():
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store)) as client:
        assert client.get("/v1/evidence/saved", headers=authorization()).status_code == 200
        response = client.post("/v1/credentials/lichess-explorer/reload", json={"expected_generation": 0}, headers=authorization("operator"))
        assert response.status_code == 409
        assert response.json() == {"error": "credential_missing"}


def test_streamed_http_body_limit_does_not_depend_on_content_length():
    store = FakeStore()
    with TestClient(create_app(configuration(), store=store), headers=authorization()) as client:
        def oversized():
            for _ in range(17):
                yield b" " * 65536
        response = client.post("/v1/requests", content=oversized(), headers={"Transfer-Encoding": "chunked"})
        assert response.status_code == 413
        assert response.json() == {"error": "input_size_exceeded"}
        assert store.calls == [("startup",)]


def test_readiness_fails_truthfully_when_database_becomes_unavailable():
    store = FakeStore()
    def lost_database():
        raise DomainError("database_unavailable", 503)
    store.health = lost_database
    with TestClient(create_app(configuration(), store=store)) as client:
        response = client.get("/healthz")
        assert response.status_code == 503
        assert response.json() == {"error": "database_unavailable"}


def test_optional_lifespan_worker_processes_bounded_slices_and_stops_cleanly():
    progressed = threading.Event()
    class LocalWorker:
        async def work_once(self):
            progressed.set()
            return False
    store = FakeStore()
    with TestClient(create_app(configuration(worker_enabled=True), store=store, worker=LocalWorker())) as client:
        assert progressed.wait(timeout=2)
        assert client.get("/healthz").status_code == 200


def test_domain_and_unexpected_errors_retain_safe_public_shapes(caplog):
    with TestClient(create_app(configuration(), store=FakeStore()), headers=authorization()) as client:
        response = client.get("/v1/requests/missing")
        assert response.status_code == 404 and response.json() == {"error": "request_not_found"}
        response = client.get("/v1/requests/unavailable")
        assert response.status_code == 503 and response.json() == {"error": "intelligence_unavailable"}
    assert "SECRET" not in caplog.text


def test_startup_failure_never_rethrows_private_driver_details():
    store = FakeStore()
    def private_failure():
        raise RuntimeError("SECRET_DATABASE_OR_PROVIDER_DETAIL")
    store.validate_startup = private_failure
    with pytest.raises(RuntimeError, match="^intelligence_startup_failed$"):
        with TestClient(create_app(configuration(), store=store)):
            pytest.fail("Invalid database startup succeeded")


def test_configuration_never_reprs_private_values_or_accepts_shared_tokens():
    value = configuration(credential_file=Path("/private/example-provider.json"))
    assert "postgresql" not in repr(value) and "c" * 40 not in repr(value)
    assert "example-provider" not in repr(value)
    with pytest.raises(ValueError, match="distinct_scoped_credentials_required"):
        replace(value, operator_token=value.caller_token)
    with pytest.raises(ValueError, match="credential_file_requires_absolute_path"):
        replace(value, credential_file=Path("provider.json"))


def test_model_profiles_copy_normalized_ratings_and_reject_rebound_model_versions():
    profile = {"model_id": "maia-2", "model_version": "fixture-1", "artifact_sha256": "a" * 64, "ratings": (1500,)}
    value = configuration(model_profiles=(profile,))
    assert value.model_profiles[0]["ratings"] == [1500]
    profile["model_id"] = "changed-external-reference"
    assert value.model_profiles[0]["model_id"] == "maia-2"
    with pytest.raises(ValueError, match="duplicate_model_profile"):
        replace(value, model_profiles=(value.model_profiles[0], {**value.model_profiles[0], "artifact_sha256": "b" * 64}))


@pytest.mark.parametrize("endpoint", ["http://example.com", "https://secret@example.com", "https://example.com/path", "https://example.com?token=secret", "ftp://127.0.0.1"])
def test_provider_endpoint_override_requires_safe_configured_origin(endpoint):
    with pytest.raises(ValueError, match="invalid_provider_endpoint"):
        configuration(provider_base_url=endpoint)


def test_private_configuration_files_reject_shared_permissions_symlinks_and_empty_content(tmp_path):
    private = tmp_path / "private"
    private.write_text("PRIVATE_TEST_VALUE")
    private.chmod(0o644)
    with pytest.raises(ValueError, match="private_file_invalid"):
        private_text(private)
    private.chmod(0o600)
    assert private_text(private) == "PRIVATE_TEST_VALUE"
    linked = tmp_path / "linked"
    linked.symlink_to(private)
    with pytest.raises(ValueError, match="private_file_unavailable"):
        private_text(linked)
    private.write_text("")
    with pytest.raises(ValueError, match="private_file_empty"):
        private_text(private)


def test_installed_public_imports_and_help_need_no_optional_infrastructure():
    guard = r'''
import sys
from importlib.abc import MetaPathFinder
class RejectOptional(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app','celery','redis','torch','onnxruntime','transformers','tempo_learning_runtime','tempo_game_evidence','sqlite3'}:
            raise AssertionError('Forbidden dependency: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
def deny_connection(event, arguments):
    if event in {'socket.connect', 'sqlite3.connect'}:
        raise AssertionError('Unexpected infrastructure connection')
sys.addaudithook(deny_connection)
import tempo_intelligence
assert 'site-packages' in tempo_intelligence.__file__
from tempo_intelligence.cli import parser
assert parser().parse_args(['work','--once']).command == 'work'
'''
    result = subprocess.run([sys.executable, "-I", "-c", guard], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
