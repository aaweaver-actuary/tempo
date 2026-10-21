# CLI lifecycle storage fixtures

[`lifecycle-storage.mjs`](lifecycle-storage.mjs) contains the existing complete PostgreSQL fixture commands from [`check-tempo-cli.mjs`](../../../scripts/check-tempo-cli.mjs): change the synthetic `cli-history` review rating in either direction, read the latest migration version and remove the existing test's migration-ledger suffix. No query text, parameter, flag or effect order changes.

The CLI proof retains its disposable target construction, lifecycle/receipt/history assertions, fault selection and error handling. These helpers borrow that existing runner's Compose capability; they create no connection/transaction authority or generic SQL interface. `reviews(card_id,rating)` and operational `tempo_schema_migrations(version)` are only referenced by the already-existing fixture commands. Native execution was not performed for this relocation.
