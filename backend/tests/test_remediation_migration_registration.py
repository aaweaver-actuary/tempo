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


def test_supported_images_install_registered_module_and_contract_from_their_own_artifacts():
    for name in ("Dockerfile.postgres-maintenance", "backend/Dockerfile"):
        source = (ROOT / name).read_text()
        assert "COPY services/learning/remediation /services/learning/remediation" in source
        assert "COPY contracts/game-evidence /contracts/game-evidence" in source
        install = next(line for line in source.splitlines() if line.startswith("RUN pip install"))
        assert "/services/learning/remediation" in install and "/contracts/game-evidence" in install
    maintenance = (ROOT / "Dockerfile.postgres-maintenance").read_text()
    assert "/services/learning/runtime" not in maintenance
    assert '"migrations" = "tempo_learning_remediation/migrations"' in (
        ROOT / "services/learning/remediation/pyproject.toml").read_text()


def test_noop_migration_still_verifies_installed_module_before_database_access(monkeypatch):
    events = []
    def installed(name):
        events.append("artifact verified")
        assert name == "tempo-learning-remediation"
        return distribution()
    class Database:
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def commit(self): events.append("read committed")
        def execute(self, statement):
            assert statement.lstrip().startswith("SELECT")
            rows = [(True,)] if "to_regclass" in statement else [(i,) for i in range(1, 51)]
            return SimpleNamespace(fetchone=lambda: rows[0], fetchall=lambda: rows)
    def connect(*args):
        events.append("database opened")
        return Database()
    monkeypatch.setattr(migration.metadata, "distribution", installed)
    monkeypatch.setattr(migration.psycopg, "connect", connect)
    migration.apply_migrations("synthetic-noop")
    assert events == ["artifact verified", "database opened", "read committed"]
    monkeypatch.setattr(migration.metadata, "distribution", lambda name: (_ for _ in ()).throw(
        migration.metadata.PackageNotFoundError(name)))
    monkeypatch.setattr(migration.psycopg, "connect", lambda *_args: pytest.fail("database opened without artifact"))
    with pytest.raises(migration.metadata.PackageNotFoundError):
        migration.apply_migrations("synthetic-noop")
