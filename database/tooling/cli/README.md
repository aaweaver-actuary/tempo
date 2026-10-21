# CLI database operations

Fixed native operations used by Tempo's existing CLI composition live here. [`probes.mjs`](probes.mjs) owns the read-only ordered migration-ledger query and Redis PING commands. [`backup.mjs`](backup.mjs) owns the existing dump, archive-list verification, temporary restore database creation, single-transaction restore and restore database removal commands.

[`tempo-cli.mjs`](../../../scripts/tempo-cli.mjs) keeps diagnostic display and ledger/recovery classification. [`tempo-runtime.mjs`](../../../scripts/tempo-runtime.mjs) keeps lifecycle decisions, readiness deadline/retry policy, backup names/timestamps and journal updates between effects. Callers lend their existing Compose capability; these fixed operations add no scheduler, generic query/command interface or connection owner. Existing flags, command arguments and call order are preserved.

The only queried table is operational `tempo_schema_migrations(version)`, ordered ascending. Backup operations copy the existing PostgreSQL database without introducing tables, migrations, permission changes or runtime data transformations. Actual deployment, backup and live schema remain unverified by this mechanical move.
