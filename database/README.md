# Database files and source schema

This tree is the required single home for database operations. PR334 moves the dedicated database files here, but **complete centralization is unfinished: 156 mixed SQL/application modules still contain database operations outside this tree**. Their [inventory](INVENTORY.md) is the extraction checklist, not a permanent exception. Application/domain logic belongs in logical folders outside `database/`; database operations belong here, connected through explicit interfaces and outer composition.

Physical colocation does not grant a component access to another owner's data and does not create a shared runtime module. Each owner retains its own package artifact, migration authority and transaction boundaries.

**Current source schema; live schema/deployment unverified.** Inventory baseline: main `14ad5754` (October 20, 2026). This reorganization changes file paths and required build/discovery references only. No migrations, data changes, service restarts or deployment were performed. The proposed canonical lines/positions/presentation-history model remains planning-only.

## Find stored data

| Owner | Namespace / scope | Tables in source DDL | Charts and complete field/type dictionary |
| --- | --- | ---: | --- |
| [Legacy backend](tempo_legacy_database/README.md) | `public (existing shared legacy database)` | 163 | [Schema index](tempo_legacy_database/schema/README.md) |
| [Content](tempo_content_database/README.md) | `content` | 20 | [Schema index](tempo_content_database/schema/README.md) |
| [Intelligence](tempo_intelligence_database/README.md) | `intelligence` | 25 | [Schema index](tempo_intelligence_database/schema/README.md) |
| [Game Evidence](tempo_game_evidence_database/README.md) | `public in dedicated Game Evidence database` | 14 | [Schema index](tempo_game_evidence_database/schema/README.md) |
| [Learning runtime](tempo_learning_database/README.md) | `learning` | 70 | [Schema index](tempo_learning_database/schema/README.md) |
| [Learning remediation](tempo_remediation_database/README.md) | `learning or public, chosen by including owner` | 12 | [Schema index](tempo_remediation_database/schema/README.md) |
| [Browser](browser/README.md) | IndexedDB databases and local replay stores | object stores | [Browser storage chart/dictionary](browser/README.md) |
| [Browser intent package](browser-intents/README.md) | Workspace-scoped IndexedDB intent/teaching facts | object stores | [Intent storage chart/dictionary](browser-intents/README.md) |

Counts are separate installation schemas, **not a total of tables in one live database**. The legacy 163 already include 12 remediation tables; Learning runtime's 70 also include its 12 remediation tables. They are included by separate owners, not independent extra copies to add to those totals.

## Organization and packaging

Each `tempo_*_database/` directory is an owner-specific Python package with `adapters/`, `ddl/` (including ordered SQL `migrations/`) and `schema/` README views. `tempo_legacy_database/connections.py` retains legacy PostgreSQL/SQLite routing; `tempo_learning_database/connections.py` retains Learning's own connection context. Root `database/` has no shared runtime initializer or connection authority.

Service `hatch_build.py` hooks include only their corresponding database package in that service's wheel. Source distributions carry the same owner files under their own `database/` directory and rebuild without a repository checkout. Runtime dependency versions and public service exports remain unchanged. The backend image copies only its own database package; the maintenance image copies its DDL plus the existing installed remediation artifact. Browser intent source still builds into the existing `@tempo/browser-intents` package's `dist` exports.

For legacy source checks use `PYTHONPATH=backend:database`; the existing Make/runner paths set this. Services should be checked from their installed wheels using their existing owning runners. Rebuild/reinstall the relevant artifact after changing its centralized database source.

[Database tooling](tooling/README.md) contains the existing explicit migration/import/backup/restore/provisioning commands. [Database proof scripts](tests/README.md) contains the existing disposable PostgreSQL scenarios and benchmarks. They retain their original authority and are not new acceptance gates. Application/contract suites stay with their feature owners; [the inventory](INVENTORY.md) explicitly records mixed application/SQL files rather than claiming they have been extracted.

The [disposable ownership fixture](ownership/README.md) remains the existing cross-owner test infrastructure, now in this same tree.

## Read the relationships

Each bounded Mermaid ER chart renders directly in its directory README on GitHub. Charts show keys; dictionaries list every field/type, primary/composite keys, nullable fields, enforced foreign keys and unique tuples, with links to DDL. SQL views are listed separately as read projections. A similar `card_id`, `game_id`, publication ID or JSON identity is **not** a foreign key unless the DDL declares it. Logical cross-component protocol identities are labeled in each owner's overview. No diagram claims a live constraint was inspected.

The legacy source still co-locates Games and Study tables and application SQL. This move preserves that existing limitation; it does not introduce or approve a new Study → Games dependency. Optional producers still must fail independently under [AGENTS.md](../AGENTS.md).

## Before merge

Schema/storage relationship changes must update the affected directory README diagrams and descriptions accurately in the same PR. Reviewers compare the charts/dictionary with actual DDL, migrations and persistence code before merge. Update only affected views; no new CI job, live database check, full-system gate or qualification framework is added. See [the acceptance rule](../AGENTS.md#keep-database-charts-accurate-before-merge).

## Reducing the schema

The [initial 163-table breakdown and simplification recommendation](SIMPLIFICATION.md) separates feature storage, legacy generations and operational state. Andy's 20–30 table goal is a future design target, not permission to drop tables, migrate live data or implement the pending canonical model here.
