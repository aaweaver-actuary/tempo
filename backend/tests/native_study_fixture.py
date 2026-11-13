"""Disposable native PostgreSQL fixture shared by retained Study tests."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import time
import uuid
import psycopg
import pytest
from tempo_storage.postgres import PostgresConnection, tempo_row_factory
ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:18.6-trixie"
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=20
    )

def postgres_fixture_profile(profile):
    """Finite fixture allocations; the default native proof is unchanged."""
    if profile == "default":
        return dict(memory_mib=384, storage="tmpfs")
    if profile == "study-recovery":
        return dict(memory_mib=1024, storage="owned-volume")
    if profile == "study-recovery-assembly":
        return dict(memory_mib=448, storage="owned-volume")
    raise ValueError("unknown_owned_postgres_fixture_profile")

def study_recovery_assembly_allocations(configuration):
    """One explicit disposable assembly, within the unchanged host budget."""
    roles = (
        "postgres",
        "redis",
        "api",
        "foreground-worker",
        "background-worker",
        "game-feedback-worker",
        "web",
    )
    allocation = {
        role: dict(configuration["developmentServices"][role]) for role in roles
    }
    allocation["postgres"]["memoryMiB"] = postgres_fixture_profile(
        "study-recovery-assembly"
    )["memory_mib"]
    allocation["foreground-worker"]["memoryMiB"] = 384
    allocation["background-worker"] = dict(
        configuration["optionalServices"]["background-worker"]
    )
    # Transfer measured Redis/web headroom to the explicit full-scope capture
    # process, retaining the same aggregate disposable assembly budget.
    allocation["redis"]["memoryMiB"] = 32
    allocation["web"]["memoryMiB"] = 32
    allocation["background-worker"]["memoryMiB"] = 512
    # Preserve the 2 GiB task-owned fixture; it does not claim the live
    # historical comparison's larger production worker allocation.
    allocation["game-feedback-worker"] = dict(
        configuration["developmentServices"]["game-feedback-worker"]
    )
    allocation["game-feedback-worker"]["memoryMiB"] = 448
    assert all(
        type(value["memoryMiB"]) is int and value["memoryMiB"] > 0
        for value in allocation.values()
    )
    assert (
        sum(value["memoryMiB"] for value in allocation.values())
        <= configuration["development"]["memoryMiB"]
    ), "study_recovery_assembly_exceeds_development_budget"
    return allocation

@pytest.fixture(scope="module")
def owned_postgres(tmp_path_factory, *, additional_ports=(), profile="default"):
    # The packaged browser journey reuses this exact owned network namespace;
    # only its existing web service may request an extra loopback-only port.
    assert additional_ports in ((), (80,))
    bridge_dsn = os.environ.get('TEMPO_NATIVE_FROZEN_BRIDGE_DSN')
    if bridge_dsn is not None:
        from psycopg.conninfo import conninfo_to_dict
        from database.tooling.apply_postgres_migrations import apply_migrations
        project = os.environ.get('TEMPO_TEST_COMPOSE_PROJECT', '')
        revision = os.environ.get('TEMPO_NATIVE_CANDIDATE_REVISION', '')
        info = conninfo_to_dict(bridge_dsn)
        if (additional_ports or profile != 'default' or os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable'
                or not re.fullmatch(r'tempo-pg-regressions-[0-9]+-[0-9a-f]{8}', project)
                or not re.fullmatch(r'[0-9a-f]{40}', revision)
                or info.get('host') != 'postgres' or info.get('port') != '5432' or info.get('user') != 'postgres'
                or not re.fullmatch(r'tempo_position_test_[a-f0-9]{32}', info.get('dbname', ''))):
            raise RuntimeError('Invalid owned frozen bridge identity')
        with psycopg.connect(bridge_dsn, autocommit=True) as raw:
            row = raw.execute("SELECT current_database(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
            if row != (info['dbname'], project): raise RuntimeError('Owned frozen bridge database marker differs')
            if raw.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]:
                raise RuntimeError('Owned frozen bridge database is not empty')
        apply_migrations(bridge_dsn)
        with psycopg.connect(bridge_dsn) as raw:
            raw.execute('INSERT INTO settings(id) VALUES(1)')
            raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('white','Synthetic','fixture',%s)", (NOW.isoformat(),))
        @contextmanager
        def transaction():
            with psycopg.connect(bridge_dsn, row_factory=tempo_row_factory) as raw:
                raw.execute("SET LOCAL lock_timeout='3s'")
                raw.execute("SET LOCAL statement_timeout='5s'")
                if raw.execute('SELECT current_database()').fetchone()[0] != info['dbname']:
                    raise RuntimeError('Owned frozen bridge database changed')
                yield PostgresConnection(raw)
        yield transaction
        return
    published = [
        value
        for port in additional_ports
        for value in ("--publish", f"127.0.0.1::{port}")
    ]
    allocation = postgres_fixture_profile(profile)
    if profile == "study-recovery-assembly":
        assert os.environ.get("TEMPO_DEVELOPMENT_BUDGET_TOKEN"), (
            "recovery_assembly_requires_managed_component_budget"
        )
    identity = "tempo-game-feedback-" + uuid.uuid4().hex
    container = identity + "-postgres"
    volume = identity + "-data" if allocation["storage"] == "owned-volume" else None
    password = secrets.token_urlsafe(32)
    evidence_root = Path(
        os.environ.get(
            "TEMPO_GAME_FEEDBACK_EVIDENCE_ROOT",
            ROOT / "test-results/game-feedback-native",
        )
    )
    evidence = evidence_root / identity
    evidence.mkdir(parents=True)
    report = {
        "owner": identity,
        "container": container,
        "cleanup_verified": False,
        "profile": profile,
        "storage": allocation["storage"],
    }
    secret = tmp_path_factory.mktemp("game-feedback-secret") / "postgres.env"
    secret.write_text(
        "POSTGRES_PASSWORD="
        + password
        + "\nPOSTGRES_DB="
        + identity.replace("-", "_")
        + "\n"
    )
    secret.chmod(0o600)
    database_name = identity.replace("-", "_")
    try:
        assert not docker(
            "container",
            "ls",
            "--all",
            "--filter",
            "name=^/" + container + "$",
            "--format",
            "{{.ID}}",
        ).stdout.strip()
        if volume:
            assert not docker(
                "volume",
                "ls",
                "--filter",
                "name=^" + volume + "$",
                "--format",
                "{{.Name}}",
            ).stdout.strip()
            docker(
                "volume",
                "create",
                "--label",
                "tempo.owner=" + identity,
                "--label",
                "tempo.disposable=true",
                volume,
            )
            stored = json.loads(docker("volume", "inspect", volume).stdout)[0]
            assert stored["Name"] == volume and stored["Driver"] == "local"
            assert (
                stored["Labels"]["tempo.owner"] == identity
                and stored["Labels"]["tempo.disposable"] == "true"
            )
            report["volume"] = volume
        storage = (
            [
                "--mount",
                "type=volume,source=" + volume + ",destination=/var/lib/postgresql",
            ]
            if volume
            else ["--tmpfs", "/var/lib/postgresql:rw,size=512m"]
        )
        image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}").stdout.strip()
        report["image_id"] = image
        # The seven-role assembly inherits the pinned image's production
        # connection default; the smaller native owner keeps its explicit cap.
        connection_limit = (
            [] if profile == "study-recovery-assembly" else ["-c", "max_connections=16"]
        )
        report["container_id"] = docker(
            "run",
            "--detach",
            "--name",
            container,
            "--label",
            "tempo.owner=" + identity,
            "--label",
            "tempo.disposable=true",
            "--memory",
            str(allocation["memory_mib"]) + "m",
            "--memory-swap",
            str(allocation["memory_mib"]) + "m",
            "--pids-limit",
            "128",
            *storage,
            "--publish",
            "127.0.0.1::5432",
            *published,
            "--env-file",
            str(secret),
            image,
            "postgres",
            "-c",
            "shared_buffers=32MB",
            *connection_limit,
        ).stdout.strip()
        inventory = json.loads(docker("inspect", container).stdout)[0]
        if profile == "study-recovery-assembly":
            labels = inventory["Config"]["Labels"]
            assert labels["tempo.resource-class"] == "development"
            assert (
                labels["tempo.resource-owner"]
                == os.environ["TEMPO_DEVELOPMENT_BUDGET_TOKEN"]
            )
            report["managed_resource_labels"] = {
                key: labels[key]
                for key in ("tempo.resource-class", "tempo.resource-owner")
            }
        report["mounts"] = inventory["Mounts"]
        report["tmpfs"] = inventory["HostConfig"].get("Tmpfs", {})
        report["memory_bytes"] = inventory["HostConfig"]["Memory"]
        report["pids_limit"] = inventory["HostConfig"]["PidsLimit"]
        report["published_ports"] = inventory["NetworkSettings"]["Ports"]
        assert (
            report["memory_bytes"] == allocation["memory_mib"] * 1024 * 1024
            and report["pids_limit"] == 128
        )
        binding = inventory["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert binding["HostIp"] == "127.0.0.1"
        if volume:
            assert not inventory["HostConfig"].get("Tmpfs", {})
            assert len(inventory["Mounts"]) == 1
            (mount,) = inventory["Mounts"]
            assert mount["Type"] == "volume" and mount["Name"] == volume
            assert mount["Destination"] == "/var/lib/postgresql" and mount["RW"] is True
        else:
            assert inventory["HostConfig"]["Tmpfs"] == {
                "/var/lib/postgresql": "rw,size=512m"
            }
            assert all(mount["Type"] == "tmpfs" for mount in inventory["Mounts"])
        port = int(binding["HostPort"])
        report.update(port=port, database=database_name)
        options = dict(
            host="127.0.0.1",
            port=port,
            dbname=database_name,
            user="postgres",
            password=password,
            sslmode="disable",
            connect_timeout=2,
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with psycopg.connect(**options) as probe:
                    assert (
                        probe.execute("SELECT current_database()").fetchone()[0]
                        == database_name
                    )
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "owned PostgreSQL TCP endpoint did not become ready"
                    )
                docker(
                    "exec",
                    container,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    database_name,
                    "-t",
                    "1",
                    check=False,
                )
        spec = importlib.util.spec_from_file_location(
            "game_feedback_migration_owner",
            ROOT / "database/tooling/apply_postgres_migrations.py",
        )
        migration_owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_owner)
        # This is a source/native contract, not installed-artifact qualification.
        # Reuse the owner's ordered history and pinned module registration. Read
        # that exact checked-in module resource, verifying the registered digest;
        # never replace absent package metadata with a claimed installed pass.
        migrations = sorted(migration_owner.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        migration_owner.validate_migration_history(
            migrations, [], expected_version=migration_owner.POSTGRES_SCHEMA_VERSION
        )
        with psycopg.connect(**options) as raw:
            for path in migrations:
                raw.execute(migration_owner.migration_source(path), prepare=False)
                assert raw.execute(
                    "SELECT MAX(version) FROM tempo_schema_migrations"
                ).fetchone()[0] == int(path.name[:3])
        with psycopg.connect(**options) as raw:
            report["system_identifier"] = str(
                raw.execute(
                    "SELECT system_identifier FROM pg_control_system()"
                ).fetchone()[0]
            )
            report["database_oid"] = raw.execute(
                "SELECT oid FROM pg_database WHERE datname=current_database()"
            ).fetchone()[0]
            limits = raw.execute(
                "SELECT current_setting('max_connections')::int, "
                "current_setting('superuser_reserved_connections')::int, "
                "current_setting('reserved_connections')::int"
            ).fetchone()
            report["connection_settings"] = dict(
                zip(
                    (
                        "max_connections",
                        "superuser_reserved_connections",
                        "reserved_connections",
                    ),
                    limits,
                )
            )
            report["connection_limit_source"] = (
                "pinned image production default"
                if profile == "study-recovery-assembly"
                else "explicit native owner limit"
            )
            if profile != "study-recovery-assembly":
                assert limits[0] == 16
            raw.execute("INSERT INTO settings(id) VALUES(1)")
            raw.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('white','Synthetic','fixture',%s)",
                (NOW.isoformat(),),
            )
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))

        @contextmanager
        def transaction():
            with psycopg.connect(**options, row_factory=tempo_row_factory) as raw:
                raw.execute("SET LOCAL lock_timeout='3s'")
                raw.execute("SET LOCAL statement_timeout='5s'")
                assert (
                    raw.execute("SELECT current_database()").fetchone()[0]
                    == database_name
                )
                yield PostgresConnection(raw)

        transaction.connection_options = dict(options)
        yield transaction
    finally:
        # Check uncertain startup outcomes by exact name; never prune or touch
        # a different task resource. Preserve identity evidence even on failure.
        found = docker(
            "container",
            "ls",
            "--all",
            "--filter",
            "name=^/" + container + "$",
            "--format",
            "{{.ID}}",
        ).stdout.strip()
        if found:
            current = json.loads(docker("inspect", container).stdout)[0]
            assert current["Config"]["Labels"]["tempo.owner"] == identity
            assert current["Config"]["Labels"]["tempo.disposable"] == "true"
            # Evidence is best effort; a failed observation must never prevent
            # the exact verified fixture from reaching its existing teardown.
            report["memory_observations"] = {}
            for metric in ("memory.events", "memory.peak"):
                try:
                    observed = docker(
                        "exec",
                        current["Id"],
                        "cat",
                        "/sys/fs/cgroup/" + metric,
                        check=False,
                    )
                    report["memory_observations"][metric] = {
                        "exit_code": observed.returncode,
                        "value": observed.stdout[:4096],
                    }
                except Exception as error:
                    report["memory_observations"][metric] = {
                        "error_class": type(error).__name__
                    }
            logs = docker("logs", container, check=False)
            (evidence / "postgres.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "--force", current["Id"])
        assert not docker(
            "container",
            "ls",
            "--all",
            "--filter",
            "name=^/" + container + "$",
            "--format",
            "{{.ID}}",
        ).stdout.strip()
        if volume:
            found_volume = docker("volume", "inspect", volume, check=False)
            if found_volume.returncode == 0:
                stored = json.loads(found_volume.stdout)[0]
                assert (
                    stored["Name"] == volume
                    and stored["Labels"]["tempo.owner"] == identity
                )
                assert stored["Labels"]["tempo.disposable"] == "true"
                docker("volume", "rm", volume)
            absent = docker("volume", "inspect", volume, check=False)
            assert absent.returncode != 0 and "no such volume" in absent.stderr.lower()
            report["volume_cleanup_verified"] = True
        report["cleanup_verified"] = True
        (evidence / "fixture.json").write_text(json.dumps(report, indent=2))
        secret.unlink(missing_ok=True)

@pytest.fixture
def case(owned_postgres):
    """Seed one mature authored exercise and its original human review."""
    from datetime import timedelta
    from types import SimpleNamespace
    from fsrs import Card
    card_id = "study-fixture-" + uuid.uuid4().hex
    fsrs_json = Card(card_id=100, due=NOW + timedelta(days=20),
                     last_review=NOW - timedelta(days=1), stability=20, difficulty=5).to_json()
    with owned_postgres() as database:
        database.execute_native(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
            introduced_at,revision,interval_days,fsrs_card_json,first_correct_at,stability,content_type)
            VALUES(%s,'white','prefix',%s,'["e2e4"]','mature',%s,%s,1,20,%s,%s,20,'opening')""",
            (card_id, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
             "2026-11-20", (NOW - timedelta(days=6)).date().isoformat(), fsrs_json,
             "2026-10-01T12:00:00Z"))
        database.execute_native(
            """INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
            internal_rating,guided,source_kind,source_ref)
            VALUES(%s,'correct',%s,10,20,'good',0,'study',%s)""",
            (card_id, (NOW-timedelta(days=3)).isoformat(), "original-history:"+card_id))
        initial = tuple(database.execute_native(
            "SELECT interval_days,fsrs_card_json,state,due_date FROM cards WHERE id=%s",
            (card_id,)).fetchone())
    yield SimpleNamespace(transaction=owned_postgres, card_id=card_id, initial=initial)
