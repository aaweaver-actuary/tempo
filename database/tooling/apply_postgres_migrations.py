"""Apply checked-in PostgreSQL migrations during a stopped-writer window."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))

import argparse
from contextlib import nullcontext
import hashlib
from functools import wraps
import re
from importlib import metadata
import json
import os
from pathlib import Path
import sys

import psycopg


MIGRATIONS = Path(__file__).resolve().parents[2] / "database/tempo_legacy_database/ddl/migrations"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database"))
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


MODULE_MIGRATIONS = {
    "050_learning_remediation.sql": (
        "tempo-learning-remediation", "0.1.0",
        "tempo_remediation_database/ddl/migrations/001_remediation.sql",
        "96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d",
    ),
}


def migration_source(path: Path) -> str:
    """Only this owner registers module schema; no runtime core or source fallback."""
    local_source = path.read_text()
    registration = MODULE_MIGRATIONS.get(path.name)
    if registration is None:
        if "tempo-module-schema:" in local_source:
            raise RuntimeError("Unregistered module migration")
        return local_source
    distribution_name, version, resource, checksum = registration
    if (f"-- tempo-module-schema: {resource}\n" not in local_source
            or f"-- sha256: {checksum}\n" not in local_source):
        raise RuntimeError("Module migration registration changed")
    distribution = metadata.distribution(distribution_name)
    if distribution.version != version:
        raise RuntimeError("Module migration distribution version changed")
    content = distribution.locate_file(resource).read_bytes()
    if hashlib.sha256(content).hexdigest() != checksum:
        raise RuntimeError("Module migration checksum changed")
    return "SET LOCAL search_path=public,pg_catalog;\n" + content.decode("utf-8") + "\n" + local_source


def validate_migration_history(
    files: list[Path], applied_versions: list[int], *, expected_version: int,
) -> int:
    file_versions = [int(path.name[:3]) for path in files]
    if file_versions != list(range(1, expected_version + 1)):
        raise RuntimeError("Checked-in PostgreSQL migration files have a gap or duplicate")
    if applied_versions and applied_versions[-1] > expected_version:
        raise RuntimeError("PostgreSQL schema is newer than this image; stop the upgrade")
    if applied_versions != list(range(1, len(applied_versions) + 1)):
        raise RuntimeError("PostgreSQL migration history has a gap or duplicate; stop the upgrade")
    return len(applied_versions)


def safe_postgres_errors(function):
    """Do not expose database-provided detail, row keys, SQL or connection data."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except psycopg.Error as error:
            state = getattr(error, "sqlstate", None)
            state = state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unknown"
            name = type(error).__name__
            name = name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) else "DatabaseError"
            raise RuntimeError(f"PostgreSQL migration operation failed ({name}, SQLSTATE {state}); keep writers stopped.") from None
    return wrapped


# Private capability is passed only by the disposable pinned-session helper.
# The managed production path never supplies it and remains fail-closed.
_ISOLATED_TEST_CAPABILITY = object()
# Review hold: no packaged CLI can activate the unfinished supervisor path.
_ISOLATED_TEST_ENABLED = False


@safe_postgres_errors
def apply_migrations(database_url: str, *, target_version: int | None = None,
                     defense_zero: bool = False, defense_selection: str | None = None,
                     cut62_binding: str | None = None, cut62_witness: str | None = None,
                     expected_selection_sha256: str | None = None, expected_witness_id: str | None = None,
                     _pinned_connection=None, _isolated_test_context=None) -> None:
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No PostgreSQL migrations found")
    # Resolve/check every registered resource before opening a database session.
    sources = {path: migration_source(path) for path in files}
    if _pinned_connection is not None and int(_pinned_connection.info.transaction_status) != 0:
        raise RuntimeError("Pinned migration connection must be idle before migration")
    # An owning launcher may retain this exact connection across admission
    # isolation and post-commit verification. It remains responsible for close.
    connection = nullcontext(_pinned_connection) if _pinned_connection is not None else psycopg.connect(database_url)
    with connection as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        applied_versions = ([row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version"
        ).fetchall()] if exists else [])
        current_version = validate_migration_history(
            files, applied_versions, expected_version=POSTGRES_SCHEMA_VERSION,
        )
        if target_version is not None and (POSTGRES_SCHEMA_VERSION != 62 or target_version != 61 or current_version not in (59, 60)):
            raise RuntimeError("Bounded migration supports only schema 59 or interrupted 60 through 61 on a schema-62 image")
        database.commit()
        for path in files:
            version = int(path.name[:3])
            if target_version is not None and version > target_version:
                break
            if version <= current_version:
                continue
            if version != current_version + 1:
                raise RuntimeError(f"Missing migration before {path.name}")
            with database.transaction():
                if version == 62:
                    database.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    # A crossing must choose an explicit policy. The installed
                    # path reauthenticates the original witness and source in
                    # this very transaction before SQL can mutate anything.
                    from database.tooling.verify_postgres_cli_state import (
                        _cut62_defense_reference_digest, check_captured_cut62_v6,
                        cut62_witness_id, load_cut62_attempt_binding, verify_cut62_v6,
                        load_cut62_defense_selection, load_cut62_witness,
                    )
                    if defense_zero:
                        if any((defense_selection, cut62_binding, cut62_witness, expected_selection_sha256, expected_witness_id)):
                            raise RuntimeError("Conflicting schema 62 Defense policies")
                        count, digest = _cut62_defense_reference_digest(database, [])
                        selection = {'version': 1, 'target': 'explicit-historical-zero', 'cohort': 'defense-no-activity',
                                     'keys': [], 'expected': {'count': count, 'reference_digest': digest}}
                    else:
                        if not all((defense_selection, cut62_binding, cut62_witness, expected_selection_sha256, expected_witness_id)):
                            raise RuntimeError("Schema 62 requires an explicit approved Defense policy")
                        selection, selection_digest = load_cut62_defense_selection(defense_selection)
                        if selection_digest != expected_selection_sha256:
                            raise RuntimeError("Original Defense selection identity differs")
                        if selection['keys']:
                            if (not _ISOLATED_TEST_ENABLED or _pinned_connection is None or not _isolated_test_context or
                                    _isolated_test_context.get('capability') is not _ISOLATED_TEST_CAPABILITY):
                                raise RuntimeError("Nonempty Defense cleanup requires a verified writer and DDL fence")
                            guard = _isolated_test_context['guard']
                            _isolated_test_context['assert_fenced'](database, guard)
                        binding = load_cut62_attempt_binding(cut62_binding, selection, selection_digest)
                        witness, secret = load_cut62_witness(cut62_witness)
                        if (witness.get('version') != 6 or witness.get('selection') != selection or
                                witness.get('binding') != binding or cut62_witness_id(witness, secret) != expected_witness_id):
                            raise RuntimeError("Original Defense witness or binding differs")
                        if selection['keys']:
                            from database.tooling.managed_isolation import verify_originals_in_fenced_transaction
                            _, owner_witness, _, owner_id = verify_originals_in_fenced_transaction(
                                database, guard, _isolated_test_context['expected_backend'],
                                selection_path=defense_selection, binding_path=cut62_binding,
                                witness_path=cut62_witness, pre62_path=_isolated_test_context['pre62_path'])
                            if owner_witness != witness or owner_id != expected_witness_id:
                                raise RuntimeError("Original Defense witness or binding differs")
                        else:
                            check_captured_cut62_v6(database, witness, secret)
                        selection = witness['selection']
                    database.execute("SELECT set_config('tempo.cut62_defense_manifest', %s, true)",
                                     (json.dumps(selection, separators=(',', ':')),))
                database.execute(sources[path], prepare=False)
                applied_version = database.execute(
                    "SELECT COALESCE(MAX(version),0) FROM tempo_schema_migrations"
                ).fetchone()[0]
                if applied_version != version:
                    raise RuntimeError(f"{path.name} did not record version {version}")
                if version == 62 and not defense_zero:
                    verified = verify_cut62_v6(database, witness, secret)
                    if verified.get('witness_id') != expected_witness_id:
                        raise RuntimeError("Post-migration witness identity differs; transaction rolled back")
            current_version = version
            print(f"Applied {path.name}", flush=True)
        print(f"PostgreSQL schema version {current_version}", flush=True)


@safe_postgres_errors
def migration_status(database_url: str, *, reader_passfile=None, writer_passfile=None) -> dict[str, object]:
    """Validate both histories using an explicitly read-only database session."""
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(database_url, options="-c default_transaction_read_only=on") as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        applied = [row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version"
        ).fetchall()] if exists else []
        current = validate_migration_history(files, applied, expected_version=POSTGRES_SCHEMA_VERSION)
        settings_exist = database.execute("SELECT to_regclass('public.settings') IS NOT NULL").fetchone()[0]
        initialized = bool(settings_exist and database.execute("SELECT 1 FROM settings WHERE id=1").fetchone())
        roles = {row[0] for row in database.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('tempo_reader','tempo_writer')"
        ).fetchall()}
        roles_ready = roles == {"tempo_reader", "tempo_writer"}
        if roles_ready:
            roles_ready = bool(database.execute("""
                SELECT COALESCE(bool_and(
                    has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'SELECT')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'INSERT')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'UPDATE')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'DELETE')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'SELECT')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'INSERT')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'UPDATE')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'DELETE')
                ),false) FROM information_schema.tables
                WHERE table_schema='public' AND table_type='BASE TABLE'
            """).fetchone()[0])
            roles_ready = roles_ready and bool(database.execute("""
                SELECT COALESCE(bool_and(
                    has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'USAGE')
                    AND has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'SELECT')
                    AND has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'UPDATE')
                ),true) FROM information_schema.sequences WHERE sequence_schema='public'
            """).fetchone()[0])
            roles_ready = roles_ready and bool(database.execute("""
                SELECT bool_and(CASE WHEN rolname='tempo_reader'
                    THEN COALESCE(rolconfig @> ARRAY['default_transaction_read_only=on'],false)
                    ELSE NOT COALESCE(rolconfig @> ARRAY['default_transaction_read_only=on'],false) END)
                FROM pg_roles WHERE rolname IN ('tempo_reader','tempo_writer')
            """).fetchone()[0])
    credentials_ready = None
    if reader_passfile and writer_passfile:
        credentials_ready = True
        for role, passfile in (("tempo_reader", reader_passfile), ("tempo_writer", writer_passfile)):
            with psycopg.connect(database_url, user=role, passfile=str(passfile),
                                 options="-c default_transaction_read_only=on") as database:
                if database.execute("SELECT current_user").fetchone()[0] != role:
                    credentials_ready = False
    return {"expected_version": POSTGRES_SCHEMA_VERSION, "applied_versions": applied,
            "pending_versions": list(range(current + 1, POSTGRES_SCHEMA_VERSION + 1)),
            "initialized": initialized, "roles_ready": roles_ready, "credentials_ready": credentials_ready}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_POSTGRES_ADMIN_URL"))
    parser.add_argument("--check", action="store_true", help="Read-only history and initialization check")
    parser.add_argument("--json", action="store_true", help="Print --check as JSON")
    parser.add_argument("--target-version", type=int, help="Bound an approved schema-59/60 to 61 migration")
    parser.add_argument("--defense-zero", action="store_true", help="Explicitly select no Defense cleanup for a fresh or historical fixture")
    parser.add_argument("--defense-selection")
    parser.add_argument("--cut62-binding")
    parser.add_argument("--cut62-witness")
    parser.add_argument("--expected-selection-sha256")
    parser.add_argument("--expected-witness-id")
    parser.add_argument("--reader-passfile", type=Path, help="Verify reader authentication during --check")
    parser.add_argument("--writer-passfile", type=Path, help="Verify writer authentication during --check")
    options = parser.parse_args()
    if not options.dsn:
        parser.error("Provide --dsn or TEMPO_POSTGRES_ADMIN_URL")
    if options.json and not options.check:
        parser.error("--json requires --check")
    if options.check and options.target_version is not None:
        parser.error("--target-version cannot be combined with --check")
    if options.check:
        status = migration_status(options.dsn, reader_passfile=options.reader_passfile, writer_passfile=options.writer_passfile)
        print(json.dumps(status) if options.json else f"Schema {len(status['applied_versions'])}; expected {status['expected_version']}; pending {status['pending_versions']}")
    else:
        apply_migrations(options.dsn, target_version=options.target_version, defense_zero=options.defense_zero,
                         defense_selection=options.defense_selection, cut62_binding=options.cut62_binding,
                         cut62_witness=options.cut62_witness, expected_selection_sha256=options.expected_selection_sha256,
                         expected_witness_id=options.expected_witness_id)


if __name__ == "__main__":
    main()
