"""Local command dispatch, bounded inputs and sanitized failures."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tempo_intelligence import Configuration
from tempo_intelligence import cli
from tempo_intelligence.errors import DomainError


class FakeStore:
    instances = []

    def __init__(self, configuration):
        self.calls = []
        self.closed = False
        self.__class__.instances.append(self)

    def validate_startup(self):
        self.calls.append(("startup",))

    def close(self):
        self.closed = True

    def inspect(self, identity):
        if identity == "missing":
            raise DomainError("request_not_found", 404)
        if identity == "private-error":
            raise RuntimeError("SECRET_DSN_MUST_NOT_ESCAPE")
        return {"request_id": identity}

    def submit(self, payload):
        return {"request_id": payload.request_id}

    def accept_maia(self, payload):
        return {"job_id": payload.job_id, "status": "accepted"}

    def maia_status(self, payload):
        return {"job_id": payload.job_id, "status": payload.status}

    def evidence(self, identity):
        return {"evidence_id": identity}

    def maia_jobs(self, identity):
        return {"request_id": identity, "jobs": []}

    def refresh(self, identity, command_id, expected_revision):
        self.calls.append(("refresh", identity, command_id, expected_revision))
        return {"request_id": identity, "requested": True}

    def retry(self, identity, command_id, expected_revision):
        self.calls.append(("retry", identity, command_id, expected_revision))
        return {"request_id": identity, "requested": True}

    def credential_revoke(self, expected_generation):
        return {"generation": expected_generation + 1, "status": "revoked"}


@pytest.fixture
def local_store(monkeypatch):
    import tempo_intelligence.store as store_module

    configured = Configuration("fixture", "postgresql://unused/intelligence", "c" * 40, "o" * 40, "w" * 40, worker_enabled=False)
    monkeypatch.setattr(Configuration, "environment", classmethod(lambda cls: configured))
    monkeypatch.setattr(store_module, "Store", FakeStore)
    FakeStore.instances.clear()
    return FakeStore


def test_installed_cli_help_needs_no_configuration_or_services():
    result = subprocess.run([sys.executable, "-I", "-m", "tempo_intelligence", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert all(command in result.stdout for command in ("migrate", "request", "maia-jobs", "accept-maia", "credentials"))


@pytest.mark.parametrize("arguments,expected", [(["inspect", "request-one"], {"request_id": "request-one"}), (["evidence", "evidence-one"], {"evidence_id": "evidence-one"}), (["maia-jobs", "request-one"], {"request_id": "request-one", "jobs": []})])
def test_local_reads_validate_startup_and_close_owned_store(local_store, capsys, arguments, expected):
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert local_store.instances[-1].calls == [("startup",)]
    assert local_store.instances[-1].closed


@pytest.mark.parametrize("operation", ["refresh", "retry"])
def test_bounded_demand_uses_strict_command_document(local_store, tmp_path, capsys, operation):
    document = tmp_path / "command.json"
    document.write_text(json.dumps({"command_id": "command-one", "expected_revision": 4}))
    assert cli.main([operation, "request-one", "--file", str(document)]) == 0
    assert json.loads(capsys.readouterr().out)["requested"]
    assert (operation, "request-one", "command-one", 4) in local_store.instances[-1].calls


@pytest.mark.parametrize("operation,fixture", [("request", "request-v1.json"), ("accept-maia", "maia-result-v1.json")])
def test_versioned_fixture_commands_validate_and_dispatch_installed_models(local_store, capsys, operation, fixture):
    document = Path(__file__).resolve().parents[1] / "fixtures" / fixture
    assert cli.main([operation, "--file", str(document)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response.get("request_id") or response.get("status") == "accepted"
    assert local_store.instances[-1].closed


def test_unknown_command_input_never_echoes_private_material(local_store, tmp_path, capsys):
    document = tmp_path / "command.json"
    document.write_text(json.dumps({"command_id": "command-one", "token": "SECRET_INPUT_MUST_NOT_ESCAPE"}))
    assert cli.main(["refresh", "request-one", "--file", str(document)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "invalid_request"}
    assert local_store.instances[-1].closed


@pytest.mark.parametrize("identity,expected", [("missing", "request_not_found"), ("private-error", "intelligence_operation_failed")])
def test_cli_errors_emit_fixed_codes_and_close_store(local_store, capsys, identity, expected):
    assert cli.main(["inspect", identity]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err) == {"error": expected}
    assert local_store.instances[-1].closed


def test_owner_migration_does_not_load_runtime_configuration(monkeypatch, tmp_path, capsys):
    import tempo_intelligence.migrate as migrations

    owner_file = tmp_path / "owner-url"
    owner_file.write_text("postgresql://OWNER_SECRET_ONLY_TEST/intelligence")
    owner_file.chmod(0o600)
    calls = []
    monkeypatch.setattr(migrations, "migrate", lambda *values: calls.append(values))
    monkeypatch.setattr(Configuration, "environment", classmethod(lambda cls: pytest.fail("migration read runtime configuration")))
    assert cli.main(["migrate", "--owner-url-file", str(owner_file), "--workspace", "fixture", "--runtime-role", "intelligence_runtime"]) == 0
    assert calls == [("postgresql://OWNER_SECRET_ONLY_TEST/intelligence", "fixture", "intelligence_runtime")]
    assert "OWNER_SECRET" not in capsys.readouterr().out


def test_input_size_bound_fails_before_json_parsing(local_store, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "MAX_INPUT_BYTES", 16)
    document = tmp_path / "large.json"
    document.write_text(" " * 17)
    assert cli.main(["refresh", "one", "--file", str(document)]) == 1
    assert json.loads(capsys.readouterr().err) == {"error": "intelligence_operation_failed"}


def test_work_requires_explicit_once():
    with pytest.raises(SystemExit) as result:
        cli.parser().parse_args(["work"])
    assert result.value.code == 2
