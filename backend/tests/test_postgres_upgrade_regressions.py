"""Schema upgrades must reject ambiguous history and preserve the live stack."""

from pathlib import Path
import os
import subprocess

import pytest
import yaml

from scripts.apply_postgres_migrations import validate_migration_history


def test_postgres_upgrade_rejects_newer_and_gapped_history():
    migrations = [Path(f"{version:03d}_step.sql") for version in range(1, 18)]
    with pytest.raises(RuntimeError, match="newer than this image"):
        validate_migration_history(migrations, list(range(1, 19)), expected_version=17)
    with pytest.raises(RuntimeError, match="gap"):
        validate_migration_history(migrations, [*range(1, 9), *range(10, 17)], expected_version=17)


def test_postgres_schema_upgrade_does_not_require_sqlite_snapshot():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.postgres-maintenance.yml"
    maintenance = yaml.safe_load(compose_path.read_text())
    migration = maintenance["services"]["migration"]
    assert not any("TEMPO_SQLITE_SNAPSHOT" in str(mount) for mount in migration.get("volumes", []))


def test_failed_postgres_migration_prevents_dependent_rollout(tmp_path):
    command_log = tmp_path / "docker-commands.txt"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$COMMAND_LOG"
if [ "$*" = "compose config --format json" ]; then
  printf '%s\\n' '{"name":"tempo","volumes":{"tempo-postgres-data":{"external":true,"name":"tempo-postgres-data"},"tempo-postgres-backups":{"external":true,"name":"tempo-postgres-backups"},"tempo-redis-data":{"external":true,"name":"tempo-redis-data"},"tempo-engine-operations":{"external":true,"name":"tempo-engine-operations"}}}'
fi
case "$*" in *apply_postgres_migrations.py*) exit 17;; esac
""")
    fake_docker.chmod(0o755)
    repository = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["sh", str(repository / "scripts/upgrade-postgres-schema.sh"), "--apply"],
        cwd=repository, capture_output=True, text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "COMMAND_LOG": str(command_log), "TEMPO_UPGRADE_EXPECTED_PROJECT": "tempo"},
    )
    assert result.returncode == 17
    commands = command_log.read_text()
    assert "apply_postgres_migrations.py" in commands
    assert "compose up -d foreground-worker" not in commands
    assert "compose up -d web defense-engine" not in commands
