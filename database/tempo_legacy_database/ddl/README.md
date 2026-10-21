# Legacy backend ddl

[Database overview](../README.md) · [Source schema charts and dictionary](../schema/README.md)

| File | Role |
| --- | --- |
| [background_metrics_schema.py](background_metrics_schema.py) | SQLite compatibility schema for bounded background diagnostics. |
| [canonical_scope_schema.py](canonical_scope_schema.py) | SQLite compatibility migrations for the canonical publication version model. |
| [frozen_opening_schema.py](frozen_opening_schema.py) | Existing SQLite frozen-order schema and invalidation triggers; [relationships and fields](frozen-opening/README.md). |
| [queue_attempt_origins.py](queue_attempt_origins.py) | Existing SQLite queue-origin installer and atomic compatibility savepoint, moved intact; [operation/source map](../adapters/lifecycle/README.md). |
| [schema_version.py](schema_version.py) | Authoritative PostgreSQL schema readiness version. |
| [study_migration.py](study_migration.py) | Add study-owned cards without changing existing scheduling identities. |
| [tactic_capture_migration.py](tactic_capture_migration.py) | Narrow compatibility repair, also available after verified legacy import. |

[Ordered SQL migrations](migrations/README.md). Moving these files does not apply them. Migration bytes/checksums and order are preserved; the legacy wrapper’s installed-resource path changes only.


[canonical_prefix_preview_schema.py](canonical_prefix_preview_schema.py) contains the existing three SQLite compatibility preview/result/position definitions and conditional initializer. Its canonical full field/key/relationship source references are in the [prefix storage README](../adapters/authored_repertoires/canonical_prefix/README.md). PostgreSQL migrations remain unchanged; no initializer or live schema was run by this move.
