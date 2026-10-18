"""Backend owner includes the pinned installed module schema, never runtime core."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import apply_postgres_migrations as migration


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "services/learning/remediation/migrations/001_remediation.sql"
WRAPPER = ROOT / "backend/migrations/050_learning_remediation.sql"


def distribution(version="0.1.0", resource=MODULE):
    def locate_file(path):
        assert path == "tempo_learning_remediation/migrations/001_remediation.sql"
        return resource
    return SimpleNamespace(version=version, locate_file=locate_file)


def test_backend_migration_50_registers_exact_12_table_module_without_runtime_core(monkeypatch):
    monkeypatch.setattr(migration.metadata, "distribution", lambda name: distribution())
    source = migration.migration_source(WRAPPER)
    assert MODULE.read_text() in source
    assert MODULE.read_text().count("CREATE TABLE ") == 12
    assert source.count("CREATE TABLE ") == 13  # 12 module tables + backend target identity.
    assert "CREATE TABLE legacy_learning_identity" in source
    assert "gen_random_uuid()" in source and "workspace_id='legacy'" in source
    assert "SET LOCAL search_path=public,pg_catalog;" in source
    assert "INSERT INTO tempo_schema_migrations(version) VALUES(50)" in source
    assert "CREATE TABLE cards" not in source and "CREATE TABLE reviews" not in source
    assert "CREATE TABLE settings" not in source and "CREATE TABLE instance" not in source
    files = sorted(migration.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert migration.validate_migration_history(files, list(range(1, 50)), expected_version=50) == 49


@pytest.mark.parametrize("fault", ["version", "checksum", "registration"])
def test_invalid_module_registration_fails_before_database_open(monkeypatch, tmp_path, fault):
    wrapper = tmp_path / WRAPPER.name
    wrapper.write_text(WRAPPER.read_text())
    module = tmp_path / "module.sql"
    module.write_bytes(MODULE.read_bytes())
    if fault == "checksum": module.write_text("CREATE TABLE changed(value text);")
    if fault == "registration": wrapper.write_text(wrapper.read_text().replace("96907361", "00000000"))
    monkeypatch.setattr(migration.metadata, "distribution", lambda name: distribution(
        version="9.0.0" if fault == "version" else "0.1.0", resource=module))
    monkeypatch.setattr(migration, "MIGRATIONS", tmp_path)
    monkeypatch.setattr(migration.psycopg, "connect", lambda *_args, **_kwargs: pytest.fail("database opened"))
    with pytest.raises(RuntimeError, match="changed"):
        migration.apply_migrations("synthetic-unusable")


def test_undeclared_module_inclusion_and_direct_partial_registration_fail_closed(tmp_path):
    wrapper = tmp_path / "999_unknown.sql"
    wrapper.write_text("-- tempo-module-schema: unexpected/schema.sql\n")
    with pytest.raises(RuntimeError, match="Unregistered"):
        migration.migration_source(wrapper)
    source = WRAPPER.read_text()
    for table in ("remediation_deliveries", "remediation_attempts", "remediation_settlements"):
        assert table in source
    assert source.index("RAISE EXCEPTION") < source.index("INSERT INTO tempo_schema_migrations")
