# PostgreSQL schema history

[Database overview](../README.md) · [Ordered source migrations](migrations/README.md)

| Source | Role |
| --- | --- |
| [schema_version.py](schema_version.py) | Required PostgreSQL schema readiness version. |
| [Ordered SQL migrations](migrations/README.md) | Preserved installation and upgrade history. |
| [study_conflicts.sql](study_conflicts.sql) | Shared Study conflict SQL used by native fixtures. |

The SQLite startup installers have been removed. Application startup reads the
PostgreSQL migration ledger and refuses an unsupported version; it performs no
schema installation or migration. Applying a checked-in forward migration is a
separate operator action. Historical migration files remain because installed
databases still need their ordered upgrade history.
