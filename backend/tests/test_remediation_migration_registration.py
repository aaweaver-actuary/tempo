"""Historical schema inclusion remains checksum-pinned without retired packages."""

import pytest
from database.tooling import apply_postgres_migrations as migration


def test_historical_remediation_schema_is_bundled_and_checksum_verified():
    wrapper = migration.MIGRATIONS / "050_learning_remediation.sql"
    source = migration.migration_source(wrapper)
    historical = migration.MIGRATIONS.parent / "historical" / "050_remediation.sql"
    assert historical.read_text() in source
    assert source.count("CREATE TABLE ") == 13
    assert "CREATE TABLE legacy_learning_identity" in source
    assert "INSERT INTO tempo_schema_migrations(version) VALUES(50)" in source
    assert "CREATE TABLE cards" not in source
    assert "CREATE TABLE reviews" not in source


@pytest.mark.parametrize("fault", ["checksum", "registration"])
def test_changed_historical_schema_fails_before_database_access(
    monkeypatch, tmp_path, fault
):
    migrations = tmp_path / "migrations"
    historical = tmp_path / "historical"
    migrations.mkdir()
    historical.mkdir()
    wrapper = migrations / "050_learning_remediation.sql"
    wrapper.write_text((migration.MIGRATIONS / wrapper.name).read_text())
    bundled = historical / "050_remediation.sql"
    bundled.write_bytes(
        (migration.MIGRATIONS.parent / "historical" / bundled.name).read_bytes()
    )
    if fault == "checksum":
        bundled.write_text("CREATE TABLE changed(value text);")
    else:
        wrapper.write_text(wrapper.read_text().replace("96907361", "00000000"))
    monkeypatch.setattr(migration, "MIGRATIONS", migrations)
    with pytest.raises(RuntimeError, match="changed"):
        migration.migration_source(wrapper)


def test_undeclared_historical_inclusion_fails_closed(tmp_path):
    wrapper = tmp_path / "999_unknown.sql"
    wrapper.write_text("-- tempo-module-schema: unexpected/schema.sql\n")
    with pytest.raises(RuntimeError, match="Unregistered"):
        migration.migration_source(wrapper)
