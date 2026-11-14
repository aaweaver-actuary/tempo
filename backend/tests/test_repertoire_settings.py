"""Daily opening overrides and unused-allowance regression protection."""

import re
from pathlib import Path

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_repertoire_limit_migration_has_unique_number_and_matches_schema_readiness():
    from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

    migration_directory = (
        Path(__file__).resolve().parents[2] / "database/tempo_storage/ddl/migrations"
    )
    migration_paths = sorted(migration_directory.glob("[0-9][0-9][0-9]_*.sql"))
    migration_numbers = [int(path.name[:3]) for path in migration_paths]
    assert migration_numbers == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
    (limit_migration,) = migration_directory.glob("*_repertoire_daily_limits.sql")
    recorded_version = re.search(
        r"INSERT INTO tempo_schema_migrations\(version\) VALUES \((\d+)\)",
        limit_migration.read_text(),
    )
    assert recorded_version is not None
    assert int(recorded_version.group(1)) == int(limit_migration.name[:3])
