# Legacy database persistence

The serving database is PostgreSQL. This package still contains concrete
adapters for retained Study, Tactics, repertoire authoring, Game Review and
shared operational state. Its name remains historical; removing the name alone
would not remove the shared storage and transaction dependencies.

| Source | Role |
| --- | --- |
| [adapters](adapters/README.md) | Persistence implementations and read projections. |
| [ddl](ddl/README.md) | Required schema version and preserved PostgreSQL upgrade history. |
| [schema](schema/README.md) | Historical schema diagrams and field dictionaries. |
| [connections.py](connections.py) | PostgreSQL transaction contexts, admission and startup version validation. |
| [capabilities.py](capabilities.py) | Borrowed row, reader and transaction protocols. |

The SQLite backend, startup DDL, local writer, compatibility coordinator and
legacy compose/test launchers are deleted. Existing files and stored rows are
not migrated or deleted by removing those implementations. The API opens reader
contexts and dispatches retained mutations to workers. Worker contexts continue
to own PostgreSQL commits and rollbacks.

## Admission and transaction ownership

[Application composition](../../backend/app/product_composition/database_infrastructure.py)
binds the activity gate and measurement context to
[infrastructure.py](infrastructure.py). Controlled background writes preserve the
same admission, timing and transaction contexts. Foreground writes use their
existing native context directly. Query-only requests select the reader;
authoritative background reads and bounded queue-publication profiles retain
their existing native options.

Reader and borrowed-transaction protocols expose no commit or close operation.
`PostgresConnection.in_transaction` reports the exact PostgreSQL `INTRANS` state
so retained repertoire publication can require the caller's active transaction.
Concrete context owners retain commit, rollback and connection lifetime.

## Remaining coupling

The retained SQL dialect translator and SQLite-shaped query strings still live
in the PostgreSQL adapter. They translate queries sent to PostgreSQL; they do
not start an alternate storage backend. Porting those queries and splitting
shared persistence ownership are remaining work. Shared public-schema tables,
queue state, accepted evidence and operation receipts continue to require
coordinated transactions. Native PostgreSQL qualification is separate from
source, import and injected-boundary test evidence.
