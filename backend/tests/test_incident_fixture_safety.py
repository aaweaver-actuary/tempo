"""The incident rehearsal must refuse unverified databases before any mutation."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def incident_fixture_script():
    script_path = Path(__file__).resolve().parents[2] / "scripts/check_postgres_graph_retention.py"
    specification = spec_from_file_location("incident_fixture_safety_script", script_path)
    module = module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class ReadOnlyMetadataConnection:
    def __init__(self, metadata: dict):
        self.metadata = metadata
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_arguments):
        return None

    def execute(self, statement, _parameters=()):
        self.statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT")
        return self

    def fetchone(self):
        return self.metadata


def verify_guard_refusal(monkeypatch, tmp_path, script, *, marker, schema, error_match):
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    connection = ReadOnlyMetadataConnection({
        "database_name": "tempo", "database_user": "postgres", "postgres_version": "18.6",
        "postgres_version_number": 180006, "disposable_marker": marker, "schema_version": schema,
    })
    connect = Mock(return_value=connection)
    seed = Mock()
    cleanup = Mock()
    create_owned_database = Mock()
    write_report = Mock()
    monkeypatch.setattr(script.psycopg, "connect", connect)
    monkeypatch.setattr(script.IncidentFixture, "seed", seed)
    monkeypatch.setattr(script.IncidentFixture, "cleanup", cleanup)
    monkeypatch.setattr(script, "owned_fixture_database", create_owned_database)
    monkeypatch.setattr(Path, "write_text", write_report)
    with pytest.raises(RuntimeError, match=error_match):
        script.run(mode="baseline", report_path=str(tmp_path / "refused.json"))
    assert connect.call_count == 1
    assert connect.call_args.kwargs["options"] == "-c default_transaction_read_only=on"
    assert len(connection.statements) == 1
    seed.assert_not_called()
    cleanup.assert_not_called()
    create_owned_database.assert_not_called()
    write_report.assert_not_called()


def test_incident_fixture_refuses_unmarked_database_without_writes_or_cleanup(
    monkeypatch, tmp_path, incident_fixture_script,
):
    schema = max(int(path.name.split("_", 1)[0]) for path in
                 (Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql"))
    verify_guard_refusal(monkeypatch, tmp_path, incident_fixture_script, marker=None, schema=schema,
                         error_match="lacks the disposable bootstrap marker")


def test_incident_fixture_refuses_schema_mismatch_without_writes_or_cleanup(
    monkeypatch, tmp_path, incident_fixture_script,
):
    verify_guard_refusal(monkeypatch, tmp_path, incident_fixture_script,
                         marker=incident_fixture_script.DISPOSABLE_DATABASE_MARKER, schema=20,
                         error_match="current disposable schema required")
