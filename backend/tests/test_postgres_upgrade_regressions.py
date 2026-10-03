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


@pytest.fixture
def cli_history_fixture(monkeypatch):
    import hashlib
    import json
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from scripts import verify_postgres_cli_state as verification
    records = {
        "reviews": {"id": 1, "card_id": "legacy-tactic", "rating": "correct", "reviewed_at": "2000-01-01",
                    "previous_interval": 1, "next_interval": 2, "internal_rating": "good", "guided": 0,
                    "source_kind": "study", "source_ref": None, "invalidated_at": None, "invalidation_reason": None},
        "daily_queue": {"id": 1, "queue_date": "2000-01-01", "card_id": "legacy-tactic", "cycle": 0,
                        "position": 1, "status": "complete", "attempt_state": "clean", "review_result_json": None,
                        "attempt_failed": 0, "card_bucket": "tactics", "admission_kind": "new",
                        "gameplay_priority_reason": "derived reason", "admission_repertoire_id": "__game_tactics__",
                        "admission_source": "fixture"},
        "operation_receipts": {"operation_id": "fixture-receipt", "command_name": "fixture", "request_hash": "hash",
                               "state": "complete", "response_json": '{"ok":true}', "error_json": None,
                               "created_at": "2000-01-01", "updated_at": "2000-01-01", "attempt_count": 1,
                               "cycle_attempt_count": 0, "attempt_token": None},
    }
    layout = {table: (("operation_id",) if table == "operation_receipts" else ("id",),
                      tuple((column, "text", False) for column in row)) for table, row in records.items()}
    monkeypatch.setattr(verification, "table_layout", lambda _database: layout)

    def fingerprint(_database, table, columns, _primary_key, _text_keys):
        projected = [records[table][column] for column in columns]
        return 1, hashlib.sha256(json.dumps(projected).encode()).hexdigest()

    monkeypatch.setattr(verification, "destination_fingerprint", fingerprint)
    return verification, records


def test_postgres_cli_history_allows_migration_025_queue_bucket_normalization(cli_history_fixture):
    verification, records = cli_history_fixture
    before = verification.study_fingerprints(None)
    records["daily_queue"]["card_bucket"] = "tactic"
    after = verification.study_fingerprints(None, before)
    assert before == after
    assert "card_bucket" not in before["daily_queue"]["columns"]


@pytest.mark.parametrize("table,column", [
    ("daily_queue", "id"), ("daily_queue", "card_id"), ("daily_queue", "cycle"),
    ("daily_queue", "position"), ("daily_queue", "status"), ("daily_queue", "attempt_state"),
    ("daily_queue", "review_result_json"), ("daily_queue", "admission_source"),
    ("reviews", "rating"), ("reviews", "next_interval"), ("reviews", "invalidated_at"),
    ("operation_receipts", "request_hash"), ("operation_receipts", "response_json"),
])
def test_postgres_cli_history_rejects_identity_result_and_provenance_changes(cli_history_fixture, table, column):
    verification, records = cli_history_fixture
    before = verification.study_fingerprints(None)
    records[table][column] = "unexpected mutation"
    assert verification.study_fingerprints(None, before) != before
