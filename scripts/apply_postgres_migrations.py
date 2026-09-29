"""Apply checked-in PostgreSQL migrations during a stopped-writer window."""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_POSTGRES_ADMIN_URL"))
    options = parser.parse_args()
    if not options.dsn:
        parser.error("Provide --dsn or TEMPO_POSTGRES_ADMIN_URL")
    apply_migrations(options.dsn)


if __name__ == "__main__":
    main()
