"""Schema upgrades must reject ambiguous history and preserve the live stack."""

from pathlib import Path
import subprocess

import pytest
import yaml

from scripts.apply_postgres_migrations import validate_migration_history


def test_postgres_upgrade_rejects_newer_and_gapped_history():
    migrations = [Path(f"{version:03d}_step.sql") for version in range(1, 18)]
    with pytest.raises(RuntimeError, match="newer than this image"):
        validate_migration_history(migrations, list(range(1, 19)), expected_version=17)
    with pytest.raises(RuntimeError, match="gap"):
        validate_migration_history(migrations, [*range(1, 9), *range(10, 17)], expected_version=17)


def test_postgres_schema_upgrade_does_not_require_sqlite_snapshot():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.postgres-maintenance.yml"
    maintenance = yaml.safe_load(compose_path.read_text())
    migration = maintenance["services"]["migration"]
    assert not any("TEMPO_SQLITE_SNAPSHOT" in str(mount) for mount in migration.get("volumes", []))


def test_failed_postgres_migration_prevents_dependent_rollout(tmp_path):
    # The shell helper now delegates to the shared CLI lifecycle. Exercise that
    # same failure boundary without a product Docker target or network access.
    command_log = tmp_path / "commands.json"
    repository = Path(__file__).resolve().parents[2]
    script = """
import { executeLifecycle } from './scripts/tempo-runtime.mjs';
import { writeFileSync } from 'node:fs';
const calls=[];
const actions=Object.fromEntries(['ensureImages','ensureDatabase','stopApplications','backup','startServices','verifyReady','commitDeployment','recordFailure'].map(name=>[name,async()=>calls.push(name)]));
actions.checkSchema=async()=>({pending_versions:[29]});
actions.migrate=async()=>{calls.push('migrate');throw new Error('migration rejected');};
try {await executeLifecycle({recreate:true},actions);process.exitCode=0;}
catch {process.exitCode=17;}
writeFileSync(process.argv[1],JSON.stringify(calls));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(command_log)],
                            cwd=repository, capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    import json
    commands = json.loads(command_log.read_text())
    assert "backup" in commands and "migrate" in commands
    assert "startServices" not in commands and "commitDeployment" not in commands
    assert commands[-1] == "recordFailure"


def test_postgres_cli_status_is_read_only_and_reports_pending_versions(monkeypatch):
    from scripts import apply_postgres_migrations as migration
    calls = []

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class Database:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, statement):
            calls.append(statement)
            if "SELECT version FROM" in statement:
                return Cursor([(version,) for version in range(1, migration.POSTGRES_SCHEMA_VERSION)])
            if "SELECT rolname" in statement:
                return Cursor([("tempo_reader",), ("tempo_writer",)])
            return Cursor([(True,)])

    def connect(_dsn, **options):
        assert options["options"] == "-c default_transaction_read_only=on"
        return Database()

    monkeypatch.setattr(migration.psycopg, "connect", connect)
    status = migration.migration_status("postgresql://administrator@fixture/tempo")
    assert status["pending_versions"] == [migration.POSTGRES_SCHEMA_VERSION]
    assert status["initialized"] and status["roles_ready"]
    assert all(statement.lstrip().startswith("SELECT") for statement in calls)


def test_postgres_cli_status_rejects_uninitialized_data_and_missing_writer_role(monkeypatch):
    from scripts import apply_postgres_migrations as migration

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class Database:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def execute(self, statement):
            if "SELECT version FROM" in statement:
                return Cursor([(version,) for version in range(1, migration.POSTGRES_SCHEMA_VERSION + 1)])
            if "SELECT rolname" in statement: return Cursor([("tempo_reader",)])
            if "public.settings" in statement: return Cursor([(False,)])
            return Cursor([(True,)])

    monkeypatch.setattr(migration.psycopg, "connect", lambda *_args, **_kwargs: Database())
    status = migration.migration_status("postgresql://administrator@fixture/tempo")
    assert status["pending_versions"] == []
    assert not status["initialized"] and not status["roles_ready"]
