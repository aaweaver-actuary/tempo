# Database maintenance tooling

Existing explicit maintenance commands, physically relocated from `scripts/`. No tool was run against live data. Moving a file does not authorize a migration, restore, cleanup, credential/grant change or service restart.

| Tool | Purpose |
| --- | --- |
| [apply_postgres_migrations.py](apply_postgres_migrations.py) | Legacy owner's ordered migration discovery and pinned installed remediation resource inclusion. |
| [check-migration-inventory.mjs](check-migration-inventory.mjs) | Read-only source inventory/version correspondence check; run from the repository root. |
| [generate_postgres_schema.py](generate_postgres_schema.py) | Existing reviewed SQLite snapshot-to-PostgreSQL DDL generation utility. |
| [create_sqlite_cutover_snapshot.py](create_sqlite_cutover_snapshot.py) | Existing SQLite snapshot backup/verification utility. |
| [migrate_sqlite_to_postgres.py](migrate_sqlite_to_postgres.py) | Existing stopped-writer snapshot import/fingerprint verification. |
| [verify_postgres_restore.py](verify_postgres_restore.py) | Existing restored database comparison with the SQLite snapshot. |
| [verify_postgres_backup.py](verify_postgres_backup.py) | Existing PostgreSQL backup/restore table/row comparison. |
| [verify_postgres_cli_state.py](verify_postgres_cli_state.py) | Existing CLI migration guard snapshots and comparison. |
| [provision_postgres_roles.py](provision_postgres_roles.py) | Existing explicit owner provisioning utility; no provisioning was run. |
| [repair_verified_game_tactics.py](repair_verified_game_tactics.py) | Existing verified legacy game-tactic cutover repair. |
| [reclaim_priority_storage.py](reclaim_priority_storage.py) | Existing explicit offline derived-priority cleanup utility; no cleanup was run. |
| [upgrade-postgres-schema.sh](upgrade-postgres-schema.sh) | Existing wrapper delegating to the root Tempo CLI. |
| [tempo-schema-update.sh](tempo-schema-update.sh) | Existing operator-specific interactive wrapper; its existing live path/stop behavior is preserved. Do not use for source checks. |

Focused source check: `node database/tooling/check-migration-inventory.mjs`. [Existing disposable proof scripts](../tests/README.md) and [database/source-schema index](../README.md).
