"""Actual registered owning migration, independent of direct-DDL copy fixture."""
import hashlib
from pathlib import Path

from tempo_learning_database.ddl import migrate as migration_owner


def test_retained_position_registered_migration_and_tables(case):
    content = (Path(migration_owner.__file__).parent / "migrations/0004_retained_position.sql").read_bytes()
    rows = case.query("SELECT checksum FROM migration_history WHERE id=%s", ("learning.retained_position/0001",))
    assert len(rows) == 1 and rows[0]["checksum"] == hashlib.sha256(content).hexdigest()
    for table in ("retained_position_batches", "retained_position_states", "retained_position_facts"):
        assert case.query(f"SELECT count(*) AS n FROM {table}")[0]["n"] == 0
