"""Apply checked-in PostgreSQL migrations during a stopped-writer window."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import psycopg


MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.schema_version import POSTGRES_SCHEMA_VERSION


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


def apply_migrations(database_url: str) -> None:
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No PostgreSQL migrations found")
    with psycopg.connect(database_url) as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        applied_versions = ([row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version"
        ).fetchall()] if exists else [])
        current_version = validate_migration_history(
            files, applied_versions, expected_version=POSTGRES_SCHEMA_VERSION,
        )
        database.commit()
        for path in files:
            version = int(path.name[:3])
            if version <= current_version:
                continue
            if version != current_version + 1:
                raise RuntimeError(f"Missing migration before {path.name}")
            with database.transaction():
                database.execute(path.read_text(), prepare=False)
                applied_version = database.execute(
                    "SELECT COALESCE(MAX(version),0) FROM tempo_schema_migrations"
                ).fetchone()[0]
                if applied_version != version:
                    raise RuntimeError(f"{path.name} did not record version {version}")
            current_version = version
            print(f"Applied {path.name}", flush=True)
        print(f"PostgreSQL schema version {current_version}", flush=True)


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
    parser.add_argument("--reader-passfile", type=Path, help="Verify reader authentication during --check")
    parser.add_argument("--writer-passfile", type=Path, help="Verify writer authentication during --check")
    options = parser.parse_args()
    if not options.dsn:
        parser.error("Provide --dsn or TEMPO_POSTGRES_ADMIN_URL")
    if options.json and not options.check:
        parser.error("--json requires --check")
    if options.check:
        status = migration_status(options.dsn, reader_passfile=options.reader_passfile, writer_passfile=options.writer_passfile)
        print(json.dumps(status) if options.json else f"Schema {len(status['applied_versions'])}; expected {status['expected_version']}; pending {status['pending_versions']}")
    else:
        apply_migrations(options.dsn)


if __name__ == "__main__":
    main()
