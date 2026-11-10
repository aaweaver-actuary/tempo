# Database files and source schema

New database-operation files belong under their existing owner in this tree. This organization convention does not require completing all legacy extraction before a bounded feature or repair. Physical placement does not grant cross-owner data access. PR334 relocates dedicated files; follow-up batches have extracted or relocated **155 of 156 baseline candidates**, with one confirmed non-database false positive and **zero original baseline entries pending**. The separately discovered native harnesses, storage-only tests and database benchmark entry points are now physically centralized too. **Complete centralization is still unfinished:** remaining technical composition edges and any further source-audit findings are tracked separately in the [inventory](INVENTORY.md#additional-storage-candidates-found-after-the-baseline). A zero original count is not an all-database-code completion claim. Application/domain logic belongs outside this tree, connected through explicit interfaces and outer composition.

Physical colocation does not grant a component access to another owner's data and does not create a shared runtime module. Each owner retains its own package artifact, migration authority and transaction boundaries.

**Current source schema; live schema/deployment unverified.** Inventory baseline: main `14ad5754` (October 20, 2026). PR334 changes file paths and required build/discovery references; its dependent extraction moves existing frozen-order DDL/queries and separates Study policy without changing behavior. No migrations, data changes, service restarts or deployment were performed. The proposed canonical lines/positions/presentation-history model remains planning-only.

## Installed database

The schema-62 three-journey release uses the PostgreSQL schema in `tempo_legacy_database` for Study, Tactics and Game Review. The backend retains the review application and remediation libraries. The independent Learning runtime and its separate database are removed from source, along with the earlier experimental Content and Game Evidence services. Retained scheduling/reconciliation SQL now lives in the serving database adapters; this source reduction performs no migration or live data change. [Installation receipt](https://github.com/aaweaver-actuary/tempo/issues/908#issuecomment-6660350033).

## Find stored data

| Owner | Namespace / scope | Tables in source DDL | Charts and complete field/type dictionary |
| --- | --- | ---: | --- |
| [Legacy backend](tempo_legacy_database/README.md) | `public (existing shared legacy database)` | 171 | [Schema index](tempo_legacy_database/schema/README.md) |
| [Learning remediation](tempo_remediation_database/README.md) | `learning or public, chosen by including owner` | 12 | [Schema index](tempo_remediation_database/schema/README.md) |
| [Browser](browser/README.md) | IndexedDB databases and local replay stores | object stores | [Browser storage chart/dictionary](browser/README.md) |
| [Browser intent package](browser-intents/README.md) | Workspace-scoped IndexedDB intent/teaching facts | object stores | [Intent storage chart/dictionary](browser-intents/README.md) |

Counts describe source DDL, **not a live database census**. The legacy 171 already include the 12 remediation tables; do not add the remediation count to that total. Removing the separate Learning package does not drop any table or row from an installation.

Legacy Redis admission/policy key layouts, JSON fields, expiry and logical generation fences are documented in [Redis control storage](tempo_legacy_database/adapters/redis_control/README.md). They are control keys, not SQL tables or foreign-key constraints.

## Organization and packaging

Each `tempo_*_database/` directory is an owner-specific Python package with `adapters/`, `ddl/` (including ordered SQL `migrations/`) and `schema/` README views. `tempo_legacy_database/connections.py` retains the serving PostgreSQL/SQLite routing. Root `database/` has no shared runtime initializer or connection authority.

Service `hatch_build.py` hooks include only their corresponding database package in that service's wheel. Source distributions carry the same owner files under their own `database/` directory and rebuild without a repository checkout. Runtime dependency versions and public service exports remain unchanged. The backend image copies the serving database modules, adapters and DDL; the maintenance image copies its DDL plus the existing installed remediation artifact. Browser intent source still builds into the existing `@tempo/browser-intents` package's `dist` exports.

For legacy source checks use `PYTHONPATH=backend:database`; the existing Make/runner paths set this. Services should be checked from their installed wheels using their existing owning runners. Rebuild/reinstall the relevant artifact after changing its centralized database source.

[Database tooling](tooling/README.md) contains the existing explicit migration/import/backup/restore/provisioning commands. [Database proof scripts](tests/README.md) contains the existing disposable PostgreSQL scenarios and benchmarks; owner-specific native tooling and tests are indexed in each owner's README. They retain their original authority and are not new acceptance gates. Application/contract suites stay with their feature owners; [the inventory](INVENTORY.md) distinguishes the closed original extraction checklist from additional placement and composition review.

For a bounded metadata-only census of one explicitly selected PostgreSQL database, run `psql -X -v ON_ERROR_STOP=1 -f database/tooling/study_census.sql "$DATABASE_URL"` with an authorized read-only role. The report includes estimated row counts and cumulative scan counters; estimates can be unknown, and counters depend on their reset time, so neither establishes that a relation is unused or safe to delete. It reads catalog/statistics metadata only, rolls back its read-only transaction, and does not establish the live deployment's complete schema or migration state.

[Disposable PostgreSQL fixtures](ownership/README.md) support the retained remediation and backup tests.

## Read the relationships

Each bounded Mermaid ER chart renders directly in its directory README on GitHub. Charts show keys; dictionaries list every field/type, primary/composite keys, nullable fields, enforced foreign keys and unique tuples, with links to DDL. SQL views are listed separately as read projections. A similar `card_id`, `game_id`, publication ID or JSON identity is **not** a foreign key unless the DDL declares it. Logical cross-component protocol identities are labeled in each owner's overview. No diagram claims a live constraint was inspected.

The legacy schema still co-locates Games and Study tables, and existing backend composition still couples some feature workflows. This move preserves the schema and behavior; it does not introduce or approve a new Study → Games dependency. Optional producers still must fail independently under [AGENTS.md](../AGENTS.md). Physical file placement alone does not establish that acceptance criterion.

The subsequent [backend organization checklist](../backend/ORGANIZATION.md) separates logical application owners and transport contracts. The [shared timing contract](../backend/app/contracts/runtime_timing.py) now removes one PostgreSQL instrumentation reverse import; admission, writer and other technical composition seams remain pending. This changes no stored schema or resource authority.

The explicit [packaged Games collection qualification](tests/games_collection/README.md)
owns its synthetic SQL proof in this tree and reuses the established disposable
migration/cleanup fixture. It adds no schema or storage relationship.

## Before merge

Schema/storage relationship changes must keep affected owner diagrams and data dictionaries accurate. Follow the [diagram procedure](../docs/architecture/diagrams/README.md#maintenance-and-review-procedure) for review and [test selection](../docs/testing.md#select-the-smallest-relevant-proof) for proof. This does not require a live census or unrelated full-schema audit. See [the acceptance rule](../AGENTS.md#keep-database-charts-accurate-before-merge).

## Reducing the schema

There is no target live table count. [SIMPLIFICATION.md](SIMPLIFICATION.md) records proposals, not assigned cleanup. [PERSISTENCE-DISPOSITION.md](PERSISTENCE-DISPOSITION.md) records a separately proposed graph-preserving fresh start; it does not waive preservation of the current installation's content, learning history, receipts, or pending work during ordinary feature delivery. Applying a fresh start requires an explicit current assignment, exact target/keep/disposal boundaries, verified recovery protection, and the required authorization for the specific irreversible action. Reuse still-valid explicit authorization; do not demand it again merely because an agent changed. These documents do not authorize execution.

## Mixed Defense cutover evidence

The following is a review matrix for the serving `public` schema, not a deletion
list or evidence about an installed database. A current cut must first preserve
exact keys and classify every cross-owner reference; unknown ownership remains held.

| Evidence | Owner / disposition to review |
| --- | --- |
| `cards.content_type='defense'`, `threat_training_candidates.card_id`, original `queue_attempt_origins.content_type` | Retired Defense candidates; contradictory types, shared or missing references are not sufficient for deletion. |
| `daily_queue`, `queue_attempt_origins`, `queue_attempt_abandonments`, `reviews`, `review_attempt_receipts`, `review_schedule_snapshots` | Study-owned queue, issuance and history; snapshot exact IDs and preserve issued/abandoned and unresolved rows until specifically adjudicated. |
| `tactic_captures`, `tactic_progress`, `tactic_discovery_attempts` | Tactics-owned captures, admissions and solve receipts; a defensive puzzle theme is not Defense ownership. |
| `repertoire_lines`, frozen opening ranks, position first responses, `study_game_feedback`, `product_feedback_deliveries` | Retained authored/Study/Games obligations and their identities; an accepted line or effect is not retired with its producer. |
| `operation_receipts`, task and projection JSON | Shared envelopes; require reviewed typed paths and a per-reference disposition, including unknown or mixed payloads. |

The `daily_queue` delete trigger may create or update an original origin, and
abandoned origins and receipts are immutable. A disposable proof must cover
these guards and derived priority/frozen invalidation before any broader cut is
proposed. Existing migration 062's exclusive-store rehearsal does not approve
shared-row deletion.

The `pre62-source-59` witness is a separate, private stopped-writer check for
original schema-59 rows, sequences and catalog through versions 60 and 61. It
allows only the reviewed immutable-evidence objects and an empty new historical
origin table. It does not replace a full verified backup, the separate version-5
cutover witness, or any approval to change an installed database. A captured
witness must persist unchanged across retries and must never be replaced after
migration begins.

The 062 review candidate includes `daily_chess_snapshots`, `daily_chess_insights`,
`daily_statistics_jobs`, and `game_insight_recommendations` as exclusive
retired stores. The version-5 cutover witness captures their exact original
rows before migration and requires their absence afterward; version-4 evidence
is not accepted for this expanded cut. Retained tactic progress, pack identities,
Games sources and authored/Study obligations remain protected. Browser snapshot
mappings remain compatibility metadata and need their own owner review.

Historical browser `syncMetadata` records and their generic backup/restore remain
unchanged, including records from prior daily snapshot imports.
