# Database files and source schema

New database-operation files belong under their existing owner in this tree. This organization convention does not require completing all legacy extraction before a bounded feature or repair. Physical placement does not grant cross-owner data access. PR334 relocates dedicated files; follow-up batches have extracted or relocated **155 of 156 baseline candidates**, with one confirmed non-database false positive and **zero original baseline entries pending**. The separately discovered native harnesses, storage-only tests and database benchmark entry points are now physically centralized too. **Complete centralization is still unfinished:** remaining technical composition edges and any further source-audit findings are tracked separately in the [inventory](INVENTORY.md#additional-storage-candidates-found-after-the-baseline). A zero original count is not an all-database-code completion claim. Application/domain logic belongs outside this tree, connected through explicit interfaces and outer composition.

Physical colocation does not grant a component access to another owner's data and does not create a shared runtime module. Each owner retains its own package artifact, migration authority and transaction boundaries.

**Current source schema; live schema/deployment unverified.** Inventory baseline: main `14ad5754` (October 20, 2026). PR334 changes file paths and required build/discovery references; its dependent extraction moves existing frozen-order DDL/queries and separates Study policy without changing behavior. No migrations, data changes, service restarts or deployment were performed. The proposed canonical lines/positions/presentation-history model remains planning-only.

## Find stored data

| Owner | Namespace / scope | Recorded inventory count | Charts and complete field/type dictionary |
| --- | --- | ---: | --- |
| [Legacy backend](tempo_legacy_database/README.md) | `public (existing shared legacy database)` | See current index | [Schema index](tempo_legacy_database/schema/README.md) |
| [Content](tempo_content_database/README.md) | `content` | 20 | [Schema index](tempo_content_database/schema/README.md) |
| [Intelligence](tempo_intelligence_database/README.md) | `intelligence` | 25 | [Schema index](tempo_intelligence_database/schema/README.md) |
| [Game Evidence](tempo_game_evidence_database/README.md) | `public in dedicated Game Evidence database` | 14 | [Schema index](tempo_game_evidence_database/schema/README.md) |
| [Learning runtime](tempo_learning_database/README.md) | `learning` | See current index | [Schema index](tempo_learning_database/schema/README.md) |
| [Learning remediation](tempo_remediation_database/README.md) | `learning or public, chosen by including owner` | 12 | [Schema index](tempo_remediation_database/schema/README.md) |
| [Browser](browser/README.md) | IndexedDB databases and local replay stores | object stores | [Browser storage chart/dictionary](browser/README.md) |
| [Browser intent package](browser-intents/README.md) | Workspace-scoped IndexedDB intent/teaching facts | object stores | [Intent storage chart/dictionary](browser-intents/README.md) |

Counts are separate installation schemas, **not a total of tables in one live database**. The legacy 163 already include 12 remediation tables; Learning runtime's 70 also include its 12 remediation tables. They are included by separate owners, not independent extra copies to add to those totals.

Legacy Redis admission/policy key layouts, JSON fields, expiry and logical generation fences are documented in [Redis control storage](tempo_legacy_database/adapters/redis_control/README.md). They are control keys, not SQL tables or foreign-key constraints.

## Organization and packaging

Each `tempo_*_database/` directory is an owner-specific Python package with `adapters/`, `ddl/` (including ordered SQL `migrations/`) and `schema/` README views. `tempo_legacy_database/connections.py` retains legacy PostgreSQL/SQLite routing; `tempo_learning_database/connections.py` retains Learning's own connection context. Root `database/` has no shared runtime initializer or connection authority.

Service `hatch_build.py` hooks include only their corresponding database package in that service's wheel. Source distributions carry the same owner files under their own `database/` directory and rebuild without a repository checkout. Runtime dependency versions and public service exports remain unchanged. The backend image copies only its own database package; the maintenance image copies its DDL plus the existing installed remediation artifact. Browser intent source still builds into the existing `@tempo/browser-intents` package's `dist` exports.

For legacy source checks use `PYTHONPATH=backend:database`; the existing Make/runner paths set this. Services should be checked from their installed wheels using their existing owning runners. Rebuild/reinstall the relevant artifact after changing its centralized database source.

[Database tooling](tooling/README.md) contains the existing explicit migration/import/backup/restore/provisioning commands. [Database proof scripts](tests/README.md) contains the existing disposable PostgreSQL scenarios and benchmarks; owner-specific native tooling and tests are indexed in each owner's README. They retain their original authority and are not new acceptance gates. Application/contract suites stay with their feature owners; [the inventory](INVENTORY.md) distinguishes the closed original extraction checklist from additional placement and composition review.

For a bounded metadata-only census of one explicitly selected PostgreSQL database, run `psql -X -v ON_ERROR_STOP=1 -f database/tooling/study_census.sql "$DATABASE_URL"` with an authorized read-only role. The report includes estimated row counts and cumulative scan counters; estimates can be unknown, and counters depend on their reset time, so neither establishes that a relation is unused or safe to delete. It reads catalog/statistics metadata only, rolls back its read-only transaction, and does not establish the live deployment's complete schema or migration state.

The [disposable ownership fixture](ownership/README.md) remains the existing cross-owner test infrastructure, now in this same tree.

## Read the relationships

Each bounded Mermaid ER chart renders directly in its directory README on GitHub. Charts show keys; dictionaries list every field/type, primary/composite keys, nullable fields, enforced foreign keys and unique tuples, with links to DDL. SQL views are listed separately as read projections. A similar `card_id`, `game_id`, publication ID or JSON identity is **not** a foreign key unless the DDL declares it. Logical cross-component protocol identities are labeled in each owner's overview. No diagram claims a live constraint was inspected.

The legacy schema still co-locates Games and Study tables, and existing backend composition still couples some feature workflows. This move preserves the schema and behavior; it does not introduce or approve a new Study → Games dependency. Optional producers still must fail independently under [AGENTS.md](../AGENTS.md). Physical file placement alone does not establish that acceptance criterion.

The subsequent [backend organization checklist](../backend/ORGANIZATION.md) separates logical application owners and transport contracts. The [shared timing contract](../backend/app/contracts/runtime_timing.py) now removes one PostgreSQL instrumentation reverse import; admission, writer and other technical composition seams remain pending. This changes no stored schema or resource authority.

## Before merge

Schema/storage relationship changes must keep affected owner diagrams and data dictionaries accurate. Follow the [diagram procedure](../docs/architecture/diagrams/README.md#maintenance-and-review-procedure) for review and [test selection](../docs/testing.md#select-the-smallest-relevant-proof) for proof. This does not require a live census or unrelated full-schema audit. See [the acceptance rule](../AGENTS.md#keep-database-charts-accurate-before-merge).

## Reducing the schema

There is no target live table count. [SIMPLIFICATION.md](SIMPLIFICATION.md) records proposals, not assigned cleanup. [PERSISTENCE-DISPOSITION.md](PERSISTENCE-DISPOSITION.md) records a separately proposed graph-preserving fresh start; it does not waive preservation of the current installation's content, learning history, receipts, or pending work during ordinary feature delivery. Applying a fresh start requires an explicit current assignment, exact target/keep/disposal boundaries, verified recovery protection, and the required authorization for the specific irreversible action. Reuse still-valid explicit authorization; do not demand it again merely because an agent changed. These documents do not authorize execution.

The October 29 full Endgames retirement removes three feature tables from each affected owner and the legacy draw-hold setting. The affected current schema indexes/ER diagrams include these changes. Other numeric counts above remain the dated inventory baseline. Migrations are source-tested only; no live migration or deployment is claimed.
