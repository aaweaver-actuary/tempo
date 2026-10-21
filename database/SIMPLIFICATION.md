# Initial schema simplification review

**Source-only inventory; no live data or usage was queried.** The current legacy PostgreSQL migration sequence creates **163 tables**: 151 directly in backend migrations plus 12 supplied by the unchanged Learning remediation DDL. These are the source tables after ordered renames/additions through migration 050; SQL views do not add storage tables. Separate Content, Intelligence, Game Evidence and Learning installation schemas are indexed in the [root README](README.md), not added to 163 as one live database count.

| Initial category | Evidence and implication |
| --- | --- |
| Current feature storage | Cards/exercises, repertoire source/membership, ordinary queue/review/attempt/teaching facts are directly represented in current application code and the [Study chart index](tempo_legacy_database/schema/README.md). Their continued source references establish consumers, not live usage frequency. |
| Legacy compatibility candidates | **7** tables retain `_legacy` names after migrations 009/011/012/014. The import tooling still explicitly maps historical snapshot tables to these targets (`COPY_TARGETS` in [migrate_sqlite_to_postgres.py](tooling/migrate_sqlite_to_postgres.py)); retaining a migration/import consumer prevents calling them unused. |
| Versioned derived/projection candidates | **7** `_staged` tables coexist with publication heads/current SQL views. Graph, coverage, priority, opening evidence and game projections add further families in the dictionary. These support current generation/recovery behavior; replacing them with fewer structures requires identifying canonical writers, readers, replay and retention invariants first. |
| Operational/infrastructure storage | **13** initial tables match explicit schema/import progress, durable operation/background work, engine scheduling, recovery metadata, settings or backend identity families. This count is a conservative navigation grouping, not proof that all other tables are feature data. Receipts and durable task state cannot be removed simply to hit a count target. |
| Learning remediation | **12** tables preserve delivery idempotency, versioned contributions, accepted obligation heads, attempt issuance and settlement. Some overlap in domain nouns with older obligations/miss projections, but their authority and replay semantics differ; consolidation needs a reviewed migration design. |

The seven compatibility, seven staged, thirteen operational and twelve remediation tables leave **124 other feature/derived tables** in this initial disjoint grouping. Their current-feature versus replaceable-projection roles must be reviewed by owner. Name-based categories are not a final deletion plan.

## Recommendation toward 20–30

First define a bounded MVP **authoritative core**: authored lines/positions/exercises and memberships; accepted execution definitions; queue/presentation; attempts/reviews/scheduling; annotations/settings; command/replay receipts. Count derived caches, optional Games/analysis storage and migration metadata separately so the target is measurable. The proposed canonical model remains a design input only; this PR does not implement it.

Start the reduction review with the seven compatibility generations and staged/current publication pairs. For each family, trace all current readers/writers and old-snapshot import support, choose the canonical owner, state the data-retention/export requirement and prove normal study plus replay/recovery from the surviving store. Retire a family only in a separate approved schema/data migration. Avoid merging tables across owners merely to lower the visible number: ordinary Study must continue when Games is absent.

Open questions before any reduction: which legacy snapshot import versions must remain supported; which historical generations Andy wants retained/exportable; which projections can be rebuilt cheaply without blocking Study; which optional modules are actually deployed; and what the 20–30 target includes. None can be settled from names alone or from this source-only file move.
