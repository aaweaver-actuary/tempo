"""Record a narrow destination repair after exact SQLite import verification.

Stop destination writers first. The original snapshot is opened read-only by the
verifier and is never initialized or repaired. Replays require the same bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys

import psycopg

from migrate_sqlite_to_postgres import migrate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.services.tactic_capture_migration import normalize_game_tactic_cards


def repair_verified_import(source_path: Path, destination_dsn: str) -> int:
    with source_path.open("rb") as snapshot:
        snapshot_digest = hashlib.file_digest(snapshot, "sha256").hexdigest()
    repair_name = f"tactic-capture-post-import-v1:{snapshot_digest}"
    with psycopg.connect(destination_dsn) as destination:
        if destination.execute("SELECT 1 FROM internal_migrations WHERE name=%s", (repair_name,)).fetchone():
            return 0
    # Do not change or bypass the exact historical importer comparison.
    migrate(source_path, destination_dsn, verify_only=True)
    with psycopg.connect(destination_dsn) as destination:
        destination.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:tactic-capture-post-import",))
        if destination.execute("SELECT 1 FROM internal_migrations WHERE name=%s", (repair_name,)).fetchone():
            return 0
        changed = normalize_game_tactic_cards(destination)
        destination.execute("INSERT INTO internal_migrations(name,applied_at) VALUES(%s,%s)",
                            (repair_name, datetime.now(timezone.utc).isoformat()))
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_MIGRATION_DATABASE_URL"))
    arguments = parser.parse_args()
    if not arguments.dsn:
        parser.error("Set --dsn or TEMPO_MIGRATION_DATABASE_URL")
    print(f"Repaired {repair_verified_import(arguments.sqlite_path, arguments.dsn)} known game tactic cards")


if __name__ == "__main__":
    main()
