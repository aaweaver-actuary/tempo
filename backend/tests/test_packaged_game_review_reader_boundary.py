"""Bounded guards for the owned packaged-reader proof; no native resources."""

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from urllib import request

import pytest

from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "database/tests/check_postgres_game_review_reader.py"
RELATIONS = sorted(
    [
        "product_feedback_jobs",
        "product_feedback_pages",
        "product_feedback_sources",
        "product_feedback_captures",
        "product_feedback_deliveries",
        "product_feedback_configuration",
        "product_feedback_sync_requests",
        "product_feedback_retry_requests",
        "product_feedback_managed_mode",
        "study_game_feedback",
    ]
)


@pytest.fixture
def reader(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Import or refused target attempted an external effect")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(request, "urlopen", forbidden)
    import psycopg

    monkeypatch.setattr(psycopg, "connect", forbidden)
    spec = importlib.util.spec_from_file_location("packaged_game_review_reader", PROOF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_reader_import_is_inert_and_native_proof_is_outside_generic_backend_pytest(
    reader,
):
    assert reader.ROOT == ROOT
    assert not (
        ROOT / "backend/tests/test_product_game_review_packaged_reader.py"
    ).exists()
    assert reader.POSTGRES_SCHEMA_VERSION == POSTGRES_SCHEMA_VERSION
    for path in (
        "database/tempo_legacy_database/ddl/schema_version.py",
        "database/tempo_legacy_database/ddl/migrations/052_product_game_sync.sql",
        "database/tempo_legacy_database/ddl/migrations/056_managed_game_feedback_mode.sql",
    ):
        assert reader.RUNTIME_FILES[path] == "/app/" + path.removeprefix("database/")
        assert (ROOT / path).is_file()


def test_packaged_reader_passes_explicit_disposable_marker_to_owned_exec(
    reader, monkeypatch
):
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    calls = []

    def docker(*args, source):
        calls.append(args)
        assert source.startswith(reader.COMMON)
        assert '"fixture": "unchanged"' in source
        return '{"proof": "complete"}\n'

    monkeypatch.setattr(reader, "_docker", docker)
    assert reader._exec_python(
        {"Id": "verified-owned-container"}, {"fixture": "unchanged"}, 'print("proof")'
    ) == {"proof": "complete"}
    assert calls == [
        (
            "exec",
            "-i",
            "--env",
            "TEMPO_TEST_INSTANCE=disposable",
            "verified-owned-container",
            "python",
            "-",
        )
    ]


@pytest.mark.parametrize(
    "instance,project,token",
    [
        (None, "tempo-pg-regressions-123-abc", "owned"),
        ("live", "tempo-pg-regressions-123-abc", "owned"),
        ("disposable", "tempo-pg-regressions-123-abc", ""),
        ("disposable", "tempo", "owned"),
        ("disposable", "another-project-123-abc", "owned"),
        ("disposable", "tempo-pg-regressions-123-abc-suffix", "owned"),
    ],
)
def test_packaged_reader_refuses_unowned_or_wrong_project_before_external_effects(
    reader,
    monkeypatch,
    instance,
    project,
    token,
):
    for key, value in (
        ("TEMPO_TEST_INSTANCE", instance),
        ("TEMPO_TEST_COMPOSE_PROJECT", project),
        ("TEMPO_DEVELOPMENT_BUDGET_TOKEN", token),
    ):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(
        pytest.fail.Exception, match="explicit existing disposable product project"
    ):
        reader.check_packaged_game_review_reader_reads_current_receipt_without_write_privileges()


@pytest.fixture
def preflight(reader, monkeypatch):
    """Execute the actual packaged SQL guards against a bounded protocol fake."""

    class InsufficientPrivilege(Exception):
        pass

    state = SimpleNamespace(
        versions=list(range(1, POSTGRES_SCHEMA_VERSION + 1)),
        relations=list(RELATIONS),
        privileges={},
        checked=[],
        closed=False,
    )

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            state.closed = True

        def transaction(self):
            return nullcontext()

        def execute(self, sql, values=()):
            if "FROM pg_database" in sql:
                rows = [
                    (
                        "tempo",
                        "public",
                        "tempo_reader",
                        "owned-address",
                        "tempo-disposable-postgres-test",
                    )
                ]
            elif "SELECT version FROM tempo_schema_migrations" in sql:
                rows = [(version,) for version in state.versions]
            elif "FROM pg_roles" in sql:
                rows = [(False, False, False, False)]
            elif "SHOW transaction_read_only" in sql:
                rows = [("on",)]
            elif "FROM pg_tables" in sql:
                rows = [(name,) for name in state.relations]
            elif "has_table_privilege" in sql:
                state.checked.append(values[0])
                rows = [
                    state.privileges.get(values[0], (True, False, False, False, False))
                ]
            elif sql.startswith("UPDATE"):
                raise InsufficientPrivilege()
            else:
                assert sql == "SET TRANSACTION READ WRITE"
                rows = []
            return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)

    psycopg = ModuleType("psycopg")
    psycopg.connect = lambda *_args, **_kwargs: Database()
    psycopg.errors = SimpleNamespace(InsufficientPrivilege=InsufficientPrivilege)
    conninfo = ModuleType("psycopg.conninfo")
    conninfo.conninfo_to_dict = lambda _dsn: {
        "user": "tempo_reader",
        "dbname": "tempo",
        "host": "postgres",
    }
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.conninfo", conninfo)
    monkeypatch.setenv("TEMPO_TEST_INSTANCE", "disposable")
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", "fixture-reader")
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    namespace = {
        "payload": {"runtime_hashes": {}, "postgres_addresses": ["owned-address"]}
    }

    def run():
        exec(reader.COMMON + reader.PREFLIGHT, namespace)

    return state, run


def test_packaged_reader_requires_current_complete_schema_and_exact_ten_relation_acls(
    preflight, capsys
):
    state, run = preflight
    run()
    assert state.checked == RELATIONS
    assert json.loads(capsys.readouterr().out) == {
        "schema": POSTGRES_SCHEMA_VERSION,
        "role": "tempo_reader",
        "select_grants": 10,
        "update_rejected": True,
    }


@pytest.mark.parametrize(
    "change",
    [
        "missing_migration",
        "extra_migration",
        "missing_relation",
        "extra_relation",
        "write_privilege",
    ],
)
def test_packaged_reader_rejects_incomplete_or_extra_schema_inventory_and_write_acl(
    preflight, change
):
    state, run = preflight
    if change == "missing_migration":
        state.versions.remove(POSTGRES_SCHEMA_VERSION)
    elif change == "extra_migration":
        state.versions.append(POSTGRES_SCHEMA_VERSION + 1)
    elif change == "missing_relation":
        state.relations.remove("product_feedback_managed_mode")
    elif change == "extra_relation":
        state.relations.append("product_feedback_unexpected")
    else:
        state.privileges["product_feedback_managed_mode"] = (
            True,
            True,
            False,
            False,
            False,
        )
    with pytest.raises(AssertionError):
        run()
    assert state.closed
