# PostgreSQL persistence

The retained runtime uses [tempo_storage](tempo_storage/README.md): concrete PostgreSQL operations for authored repertoire, position learning, attempts/reviews, queue, Tactics, saved games and required operational recovery. Application owners prepare chess sources and decide grades; fixed storage operations borrow the existing transaction.

Historical upgrade SQL remains in `tempo_storage/ddl`. Migrations 001–063 retain their exact bytes; schema 050's former distribution inclusion is bundled with its checksum under `ddl/historical`. Migration 064 adds an inspection ledger for paused retired work without updating or deleting original records.

The old shared runtime, SQLite-shaped SQL translator, alternate-driver branches, recombination and automatic Games feedback persistence interfaces are deleted. Historical content, learning state, saved reviews, accepted effects and generated-card lineage remain in PostgreSQL. The [historical schema dictionary](../docs/architecture/schema-history/README.md) describes preserved tables; it is not a claim that every table still has a runtime reader.

Before installation, use the existing guarded migration/backup tooling and verify the applicable native transaction and assembled Study behavior on disposable resources. This source branch does not run production migrations or authorize data deletion. Update the owning source map and data description when changing persistence, transaction boundaries or lifecycle authority.

Migration 065 records fenced position-introduction history conflicts with the
selected card, conflicting card and revision, and source identity. These rows
are durable observations, not authorization to introduce the position; later
refreshes recheck the original history and source.
