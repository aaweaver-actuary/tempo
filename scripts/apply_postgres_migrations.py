"""Apply checked-in PostgreSQL migrations during a stopped-writer window."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

import psycopg


MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.schema_version import POSTGRES_SCHEMA_VERSION


def apply_migrations(database_url: str) -> None:
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No PostgreSQL migrations found")
    latest_file_version = int(files[-1].name[:3])
    if latest_file_version != POSTGRES_SCHEMA_VERSION:
        raise RuntimeError(
            f"Latest PostgreSQL migration is {latest_file_version}; "
            f"API expects {POSTGRES_SCHEMA_VERSION}"
        )
    with psycopg.connect(database_url) as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        current_version = (
            database.execute("SELECT COALESCE(MAX(version),0) FROM tempo_schema_migrations").fetchone()[0]
            if exists else 0
        )
        database.commit()
        for path in files:
            match = re.match(r"^(\d{3})_", path.name)
            assert match is not None
            version = int(match.group(1))
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
