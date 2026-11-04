"""F13: synthetic role/session denial on the runner-owned PostgreSQL cluster only."""
import os
import uuid
import runpy
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

_fence = runpy.run_path(str(Path(__file__).resolve().parents[4] /
    "database/tempo_learning_database/tooling/disposable_writer_fence.py"))
DisposableSource = _fence["DisposableSource"]
FenceHeld = _fence["FenceHeld"]
fence_disposable_source = _fence["fence_disposable_source"]


@pytest.fixture
def allocated():
    if not os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-'):
        raise RuntimeError('The owned disposable PostgreSQL runner is required')
    admin = make_conninfo(os.environ['TEMPO_LEARNING_TEST_ADMIN'], dbname='postgres')
    name = 'learning_' + uuid.uuid4().hex[:12]
    role = name + '_writer'
    password, token = uuid.uuid4().hex, uuid.uuid4().hex
    marker = 'tempo-disposable-fence:' + token
    with psycopg.connect(admin, autocommit=True) as control:
        original = control.execute("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname='postgres'").fetchone()[0]
        control.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            for db in ('postgres', name):
                control.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(db), sql.Literal(marker)))
            oid = control.execute('SELECT oid FROM pg_database WHERE datname=%s', (name,)).fetchone()[0]
            system = control.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
            control.execute("SET statement_timeout='1s'")
            control.execute("SET lock_timeout='1s'")
            source = DisposableSource(name, oid, system, token)
            writer = make_conninfo(admin, dbname=name, user=role, password=password)
            with psycopg.connect(make_conninfo(admin, dbname=name), autocommit=True) as setup:
                setup.execute('CREATE TABLE synthetic_history (id integer PRIMARY KEY, value text NOT NULL)')
                setup.execute(sql.SQL('GRANT SELECT, INSERT ON synthetic_history TO {}').format(sql.Identifier(role)))
            yield control, source, writer, admin
        finally:
            control.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS true').format(sql.Identifier(name)))
            control.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))
            control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            control.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            control.execute(sql.SQL('COMMENT ON DATABASE postgres IS {}').format(sql.Literal(original)))


def test_f13_disposable_fence_drains_old_writer_and_denies_restart(allocated):
    control, source, writer, admin = allocated
    with psycopg.connect(writer, autocommit=True) as initial:
        initial.execute("INSERT INTO synthetic_history VALUES (1, 'committed')")
    old = psycopg.connect(writer)
    old.execute("INSERT INTO synthetic_history VALUES (2, 'uncommitted')")
    receipt = fence_disposable_source(control, source)
    assert receipt.remaining_sessions == 0
    with pytest.raises(psycopg.Error):
        old.commit()
    old.close()
    for _path in ('fresh-direct-sql', 'worker-restart', 'cli-restart'):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(writer, connect_timeout=2)
    # Independently held administrative authority can reopen: this is a limit,
    # not evidence that installed credentials or restart paths are fenced.
    control.execute(sql.SQL('ALTER DATABASE {} ALLOW_CONNECTIONS true').format(sql.Identifier(source.database)))
    with psycopg.connect(make_conninfo(admin, dbname=source.database)) as readback:
        assert readback.execute('SELECT id, value FROM synthetic_history ORDER BY id').fetchall() == [(1, 'committed')]


def test_f13_rejects_wrong_allocation_before_fence(allocated):
    control, source, writer, _admin = allocated
    bad = DisposableSource(source.database, source.database_oid, source.cluster_system_identifier, '0' * 32)
    with pytest.raises(FenceHeld):
        fence_disposable_source(control, bad)
    with psycopg.connect(writer) as conn:
        assert conn.execute('SELECT 1').fetchone() == (1,)
