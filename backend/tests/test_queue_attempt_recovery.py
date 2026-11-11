"""Completed reviews survive mutable daily-queue projections without fabricated origins."""


def test_queue_origin_migration_follows_current_main_without_renumbering_published_versions():
    from pathlib import Path
    from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

    root = Path(__file__).resolve().parents[2] / "database/tempo_storage/ddl"
    migrations = sorted((root / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    assert [int(path.name[:3]) for path in migrations] == list(
        range(1, POSTGRES_SCHEMA_VERSION + 1)
    )
    assert (root / "migrations/030_opening_decision_evidence.sql").is_file()
    assert (root / "migrations/031_defensive_analysis_pause.sql").is_file()
    queue_migration = root / "migrations/032_queue_attempt_origins.sql"
    assert (
        "INSERT INTO tempo_schema_migrations(version) VALUES (32);"
        in queue_migration.read_text()
    )
    assert POSTGRES_SCHEMA_VERSION >= 39
    assert (root / "migrations/037_card_deletion.sql").is_file()
    assert (root / "migrations/038_prefix_transition_application.sql").is_file()
    assert (root / "migrations/039_prefix_transition_lock_budget.sql").is_file()
