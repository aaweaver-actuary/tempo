"""D1: storage consumes supplied admission and measurement authorities."""

import os
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar

import pytest

from tempo_legacy_database import connections, infrastructure


@pytest.fixture
def port(monkeypatch):
    events = []
    context = ContextVar("test_database_stage", default=False)

    class Gate:
        current_job = None
        in_background_control = False

        def assert_foreground_connection_allowed(self, background):
            events.append(("admit", background))

        def wait_for_background_sections(self):
            events.append("wait")

        @contextmanager
        def background_database_section(self):
            events.append(("enter_gate", context.get()))
            try:
                yield
            finally:
                events.append(("exit_gate", context.get()))

        def check_background_admission(self):
            events.append("check")

    @contextmanager
    def measure():
        token = context.set(True)
        events.append("enter_measurement")
        try:
            yield
        finally:
            events.append("exit_measurement")
            context.reset(token)

    gate = Gate()
    monkeypatch.setattr(infrastructure, "_admission", None)
    monkeypatch.setattr(infrastructure, "_measurement", None)
    infrastructure.bind_database_infrastructure(gate, measure)
    return gate, events, context


def test_storage_import_does_not_load_application_services():
    code = (
        "import sys; import tempo_legacy_database.connections; "
        "import tempo_legacy_database.adapters.database_executor; "
        "assert 'app.services.activity_gate' not in sys.modules; "
        "assert 'app.services.background_runtime' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())


def test_bound_gate_denial_precedes_connection_open(monkeypatch, port):
    gate, events, _ = port
    monkeypatch.setattr(connections.postgres_store, "configured", lambda: True)

    def deny(background):
        events.append(("admit", background))
        raise RuntimeError("denied")

    monkeypatch.setattr(gate, "assert_foreground_connection_allowed", deny)
    monkeypatch.setattr(
        connections.postgres_store, "connection", lambda **kw: pytest.fail("opened")
    )
    with pytest.raises(RuntimeError, match="denied"):
        with connections.connection(background=True):
            pass
    assert events == [("admit", True)]


def test_background_exception_unwinds_same_measurement_context(monkeypatch, port):
    _, events, context = port
    monkeypatch.setattr(connections.postgres_store, "configured", lambda: True)

    @contextmanager
    def opened(**kwargs):
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")

    monkeypatch.setattr(connections.postgres_store, "connection", opened)
    with pytest.raises(ValueError):
        with connections.connection(background=True):
            assert context.get()
            raise ValueError("failure")
    assert events == [
        ("admit", True),
        "enter_measurement",
        ("enter_gate", True),
        "open",
        "close",
        ("exit_gate", True),
        "exit_measurement",
    ]
    assert not context.get()


def test_query_only_routes_to_reader_without_admission(monkeypatch, port):
    _, events, _ = port

    @contextmanager
    def reader():
        events.append("reader")
        yield "read-only"

    monkeypatch.setattr(connections, "read_connection", reader)
    with (
        connections.query_only_request(),
        connections.connection(background=True) as db,
    ):
        assert db == "read-only"
    assert events == ["reader"]


def test_missing_binding_fails_closed(monkeypatch):
    monkeypatch.setattr(infrastructure, "_admission", None)
    with pytest.raises(RuntimeError, match="not bound"):
        infrastructure.database_admission()


def test_database_first_composition_reuses_real_gate_and_stage(tmp_path):
    code = """
from pathlib import Path
import sys
from tempo_legacy_database import connections, infrastructure
assert 'app.services.activity_gate' not in sys.modules
from app.product_composition.database_infrastructure import bind_database_infrastructure
from app.services.activity_gate import activity_gate
from app.services.background_runtime import database_stage
bind_database_infrastructure()
bind_database_infrastructure()
assert infrastructure.database_admission() is activity_gate
assert infrastructure._measurement is database_stage
connections.DB_PATH = Path(sys.argv[1])
with connections.connection() as db:
    db.execute('CREATE TABLE proof (value INTEGER)')
with activity_gate.background_job('proof', 'one'):
    with connections.background_connection() as db:
        db.execute('INSERT INTO proof VALUES (1)')
with connections.read_connection() as db:
    assert db.execute('SELECT value FROM proof').fetchone()[0] == 1
"""
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "proof.sqlite")],
        check=True,
        env=os.environ.copy(),
    )


def test_composition_helper_import_is_inert():
    code = (
        "import sys; import app.product_composition.database_infrastructure; "
        "assert 'app.services.activity_gate' not in sys.modules; "
        "assert 'app.services.background_runtime' not in sys.modules; "
        "assert 'tempo_legacy_database.connections' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())


@pytest.mark.parametrize("entrypoint", ["app.main", "app.tasks"])
def test_application_entrypoint_binds_database_after_storage_first(
    entrypoint, tmp_path
):
    code = """
import importlib
from pathlib import Path
import sys
from tempo_legacy_database import connections, infrastructure
assert 'app.services.activity_gate' not in sys.modules
importlib.import_module(sys.argv[1])
from app.services.activity_gate import activity_gate
from app.services.background_runtime import database_stage
assert infrastructure.database_admission() is activity_gate
assert infrastructure._measurement is database_stage
connections.DB_PATH = Path(sys.argv[2])
with connections.connection() as database:
    database.execute('CREATE TABLE proof (value INTEGER)')
"""
    subprocess.run(
        [sys.executable, "-c", code, entrypoint, str(tmp_path / "entry.sqlite")],
        check=True,
        env=os.environ.copy(),
    )


def test_frozen_order_fixture_binds_before_controlled_connection():
    code = """
import os, runpy, sys
from contextlib import contextmanager
from tempo_legacy_database import connections, infrastructure
@contextmanager
def controlled():
    from app.services.activity_gate import activity_gate
    assert infrastructure.database_admission() is activity_gate
    raise RuntimeError('fixture reached controlled connection')
    yield
connections.background_connection = controlled
os.environ['TEMPO_TEST_INSTANCE'] = 'disposable'
sys.argv = ['database/tests/frozen-order-browser-fixture.py', 'seed']
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except RuntimeError as error:
    assert str(error) == 'fixture reached controlled connection'
else:
    raise AssertionError('fixture did not reach controlled connection')
"""
    subprocess.run([sys.executable, "-c", code], check=True, env=os.environ.copy())
