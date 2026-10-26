"""T14 real transaction contract on one capped, labelled disposable PostgreSQL.

Uses an existing postgres:18.6-trixie image only. No pull/install/external DSN,
live data or service operation. Small SQL fixture owner exercises the application
port and existing PostgresConnection; normal-product wiring remains #501-owned.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import secrets
import subprocess
from threading import Barrier
import time
import uuid

import psycopg
import pytest

from app.study.introductions.application import Introductions
from app.study.introductions.ports import OwnerFacts
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_legacy_database.adapters.study.product_introductions import PostgresIntroductionStore, SCHEMA_SQL
from test_product_introductions import CONTEXT, DAY, NEXT_DAY, candidate, ranking

ROOT = Path(__file__).resolve().parents[2]


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check, timeout=20)


@pytest.fixture(scope="module")
def owned_postgres(tmp_path_factory):
    identity = "tempo-introductions-" + uuid.uuid4().hex
    container = identity + "-postgres"
    report = {"owner": identity, "container": container, "cleanup_verified": False}
    evidence = ROOT / "test-results/product-introductions-native" / identity
    evidence.mkdir(parents=True)
    password = secrets.token_urlsafe(32)
    secret = tmp_path_factory.mktemp("introductions-secret") / "postgres.env"
    secret.write_text("POSTGRES_PASSWORD=" + password + "\nPOSTGRES_DB=synthetic_introductions\n")
    secret.chmod(0o600)
    try:
        image = docker("image", "inspect", "postgres:18.6-trixie", "--format", "{{.Id}}").stdout.strip()
        report["image_id"] = image
        report["container_id"] = docker("run", "--detach", "--name", container,
            "--label", "tempo.owner=" + identity, "--label", "tempo.disposable=true",
            "--memory", "384m", "--memory-swap", "384m", "--cpus", "1", "--pids-limit", "128",
            "--tmpfs", "/var/lib/postgresql:rw,size=512m", "--publish", "127.0.0.1::5432",
            "--env-file", str(secret), image, "postgres", "-c", "shared_buffers=32MB",
            "-c", "max_connections=16").stdout.strip()
        current = json.loads(docker("inspect", container).stdout)[0]
        assert current["HostConfig"]["Tmpfs"] == {"/var/lib/postgresql": "rw,size=512m"}
        assert current["HostConfig"]["Memory"] == 384 * 1024 * 1024
        assert current["HostConfig"]["PidsLimit"] == 128
        assert all(mount["Type"] == "tmpfs" for mount in current["Mounts"])
        binding = current["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        options = dict(host="127.0.0.1", port=int(binding["HostPort"]), user="postgres",
                       dbname="synthetic_introductions", password=password,
                       sslmode="disable", connect_timeout=2)
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(**options) as probe:
                    assert probe.execute("SELECT current_database()").fetchone()[0] == options["dbname"]
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("owned PostgreSQL fixture did not become ready")
                docker("exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", "-t", "1", check=False)
        report["loopback_port"] = int(binding["HostPort"])
        yield options
    finally:
        found = docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        if found:
            current = json.loads(docker("inspect", container).stdout)[0]
            assert current["Config"]["Labels"]["tempo.owner"] == identity
            assert current["Config"]["Labels"]["tempo.disposable"] == "true"
            logs = docker("logs", container, check=False)
            (evidence / "postgres.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", current["Id"])
        assert not docker("container", "ls", "--all", "--filter", "name=^/" + container + "$", "--format", "{{.ID}}").stdout.strip()
        report["cleanup_verified"] = True
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))
        secret.unlink(missing_ok=True)


@pytest.fixture
def transaction(owned_postgres):
    schema = "case_" + uuid.uuid4().hex
    with psycopg.connect(**owned_postgres) as raw:
        raw.execute(f'CREATE SCHEMA "{schema}"')  # task-generated identifier only
        raw.execute(f'SET search_path TO "{schema}"')
        raw.execute(SCHEMA_SQL, prepare=False)
        raw.execute("""CREATE TABLE fixture_introduced(
            presentation_id TEXT PRIMARY KEY, study_day TEXT NOT NULL, repertoire_id TEXT NOT NULL);
            CREATE TABLE fixture_queue(presentation_id TEXT PRIMARY KEY);
            CREATE TABLE fixture_active(attempt TEXT PRIMARY KEY);
            INSERT INTO fixture_active VALUES('unrelated-issued-attempt');""", prepare=False)

    @contextmanager
    def open_transaction():
        with psycopg.connect(**owned_postgres, row_factory=tempo_row_factory) as raw:
            raw.execute(f'SET LOCAL search_path TO "{schema}"')
            raw.execute("SET LOCAL lock_timeout='3s'")
            raw.execute("SET LOCAL statement_timeout='5s'")
            yield PostgresConnection(raw)

    yield open_transaction
    with psycopg.connect(**owned_postgres) as raw:
        raw.execute(f'DROP SCHEMA "{schema}" CASCADE')


class SqlFixtureOwner:
    """Only task-owned synthetic tables; this is a typed port contract fixture."""
    def __init__(self, database, *, context=CONTEXT, budget=1):
        self.database, self.context, self.budget = database, context, budget

    def facts(self, day):
        rows = self.database.execute("SELECT presentation_id,study_day,repertoire_id FROM fixture_introduced", ()).fetchall()
        return OwnerFacts(self.context, (candidate("a1"), candidate("a2"), candidate("b1", "b"), candidate("b2", "b")),
                          self.budget, (("a", 1), ("b", 1)),
                          tuple((row[0], row[2]) for row in rows if row[1] == day),
                          frozenset(row[0] for row in rows), enabled=True)

    def introduce(self, day, choice):
        if self.database.execute("SELECT 1 FROM fixture_introduced WHERE presentation_id=?", (choice.presentation_id,)).fetchone():
            return "already_introduced"
        self.database.execute("INSERT INTO fixture_introduced VALUES(?,?,?)", (choice.presentation_id, day, choice.repertoire_id))
        self.database.execute("INSERT INTO fixture_queue VALUES(?)", (choice.presentation_id,))
        return "introduced"


def service(database, **changes):
    return Introductions(PostgresIntroductionStore(database), SqlFixtureOwner(database, **changes))


def test_postgres_atomic_introduction_budget_and_fairness_rollback(transaction):
    class FailingStore(PostgresIntroductionStore):
        def record_introduction(self, receipt, *, period_rotation_after):
            super().record_introduction(receipt, period_rotation_after=period_rotation_after)
            raise RuntimeError("synthetic failure after all effects")

    with pytest.raises(RuntimeError, match="after all effects"):
        with transaction() as database:
            app = Introductions(FailingStore(database), SqlFixtureOwner(database))
            choice = app.preview(DAY).introductions[0]
            app.commit(DAY, choice, CONTEXT)
    with transaction() as database:
        app = service(database)
        assert not app.store.state(DAY).charges and not app.store.state(DAY).period_started
        assert app.store.state(DAY).rotation_after is None
        assert database.execute("SELECT COUNT(*) FROM fixture_queue", ()).fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM fixture_introduced", ()).fetchone()[0] == 0
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"


def test_postgres_concurrent_admission_and_retry_charge_once(transaction):
    barrier = Barrier(2)
    with transaction() as database:
        choice = service(database).preview(DAY).introductions[0]

    def attempt():
        with transaction() as database:
            app = service(database)
            barrier.wait(timeout=5)
            return app.commit(DAY, choice, CONTEXT).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["introduced", "replayed"]
    with transaction() as database:
        app = service(database)
        assert len(app.store.state(DAY).charges) == 1
        assert database.execute("SELECT COUNT(*) FROM fixture_queue", ()).fetchone()[0] == 1
        assert app.commit(NEXT_DAY, choice, CONTEXT).status == "replayed"


def test_postgres_multiday_fairness_rebuild_and_persisted_reload(transaction):
    for day, expected in ((DAY, "a"), (NEXT_DAY, "b")):
        for _ in range(4):
            with transaction() as database:
                app = service(database)
                before = app.store.state(day)
                assert app.preview(day).introductions[0].repertoire_id == expected
                assert app.store.state(day) == before
        with transaction() as database:
            app = service(database)
            choice = app.preview(day).introductions[0]
            assert app.commit(day, choice, CONTEXT).status == "introduced"
        for _ in range(3):
            with transaction() as database:
                app = service(database)
                before = app.store.state(day)
                assert app.commit(day, choice, CONTEXT).status == "replayed"
                assert not app.preview(day).introductions
                assert app.store.state(day) == before
    with transaction() as database:
        rows = database.execute("SELECT study_day,repertoire_id FROM study_product_introduction_charges ORDER BY study_day", ()).fetchall()
        assert [tuple(row) for row in rows] == [(DAY, "a"), (NEXT_DAY, "b")]
        assert database.execute("SELECT COUNT(*) FROM fixture_active WHERE attempt='unrelated-issued-attempt'", ()).fetchone()[0] == 1


def test_postgres_saved_ranking_offline_settings_fence_and_active_attempt(transaction):
    value = ranking(("a2", "a", .2), ("a1", "a", .02))
    with transaction() as database:
        assert service(database).accept_ranking(DAY, CONTEXT, value) == "accepted"
    with transaction() as database:
        app = service(database)
        assert app.preview(DAY).introductions[0].decision_id == "a2"
        assert app.accept_ranking(DAY, CONTEXT, value.model_copy(update={"unavailable_reason": "offline"})) == "rejected"
    with transaction() as database:
        assert service(database).preview(DAY).introductions[0].decision_id == "a2"
        changed = replace(CONTEXT, settings_revision="explicit-settings-2")
        assert service(database, context=changed).preview(DAY).introductions[0].decision_id == "a1"
        assert service(database, context=changed).accept_ranking(DAY, CONTEXT, value) == "rejected"
        assert not PostgresIntroductionStore(database).state(DAY).charges
        assert database.execute("SELECT COUNT(*) FROM fixture_active", ()).fetchone()[0] == 1


def test_postgres_existing_owner_facts_and_ledger_are_not_double_counted(transaction):
    with transaction() as database:
        app = service(database, budget=2)
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"
    with transaction() as database:
        app = service(database, budget=2)
        assert len(app.preview(DAY).introductions) == 1
        assert app.commit(DAY, app.preview(DAY).introductions[0], CONTEXT).status == "introduced"
    with transaction() as database:
        assert not service(database, budget=2).preview(DAY).introductions
        assert len(service(database, budget=2).store.state(DAY).charges) == 2


def test_postgres_invalid_optional_cache_preserves_safe_fallback(transaction):
    with transaction() as database:
        assert service(database).accept_ranking(DAY, CONTEXT, ranking(("a2", "a", .2))) == "accepted"
        database.execute("UPDATE study_product_introduction_rankings SET ranking_json=?", ("obsolete-json",))
    with transaction() as database:
        assert service(database).preview(DAY).introductions[0].decision_id == "a1"
        assert not service(database).store.state(DAY).charges
