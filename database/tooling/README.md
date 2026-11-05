# Database maintenance tooling

Existing explicit maintenance commands, physically relocated from `scripts/`. No tool was run against live data. Moving a file does not authorize a migration, restore, cleanup, credential/grant change or service restart.

| Tool | Purpose |
| --- | --- |
| [Learning-owned bootstrap tooling](../tempo_learning_database/tooling/README.md#persistent-target-bootstrap) | Explicit disposable/persistent target preparation contracts; persistent quarantine is separate from serving admission and cutover. |
| [apply_postgres_migrations.py](apply_postgres_migrations.py) | Legacy owner's ordered migration discovery and pinned installed remediation resource inclusion. |
| [check-migration-inventory.mjs](check-migration-inventory.mjs) | Read-only source inventory/version correspondence check; run from the repository root. |
| [verify_postgres_backup.py](verify_postgres_backup.py) | Existing PostgreSQL backup/restore table/row comparison. |
| [verify_postgres_cli_state.py](verify_postgres_cli_state.py) | Existing CLI migration guard snapshots and comparison. |
| [provision_postgres_roles.py](provision_postgres_roles.py) | Existing explicit owner provisioning utility; no provisioning was run. |
| [upgrade-postgres-schema.sh](upgrade-postgres-schema.sh) | Existing wrapper delegating to the root Tempo CLI. |
| [tempo-schema-update.sh](tempo-schema-update.sh) | Existing operator-specific interactive wrapper; its existing live path/stop behavior is preserved. Do not use for source checks. |

Focused source check: `node database/tooling/check-migration-inventory.mjs`. [Existing disposable proof scripts](../tests/README.md) and [database/source-schema index](../README.md).

[Fixed CLI probes and backup operations](cli/README.md) contain the existing
migration-ledger read, Redis readiness/diagnostic probes and PostgreSQL
backup/restore commands. The caller retains target selection, ownership,
lifecycle, deadlines, reporting and cleanup decisions. Commands/arguments and
credential handling are unchanged; importing the fixed helpers runs no probe or
backup. No command in this category was executed against a service in this task.
