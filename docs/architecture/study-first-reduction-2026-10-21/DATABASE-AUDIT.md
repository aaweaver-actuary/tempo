<div id="title-block-header">

# Database ownership and table audit

</div>

- <a href="#tempo-database-audit--first-source-pass"
  id="toc-tempo-database-audit--first-source-pass">Tempo database audit —
  first source pass</a>
  - <a href="#principal-findings" id="toc-principal-findings">Principal
    findings</a>
  - <a href="#evidence-and-counting-method"
    id="toc-evidence-and-counting-method">Evidence and counting method</a>
  - <a href="#first-file-level-responsibility-findings"
    id="toc-first-file-level-responsibility-findings">First file-level
    responsibility findings</a>
  - <a href="#duplication-versus-parallel-generations"
    id="toc-duplication-versus-parallel-generations">Duplication versus
    parallel generations</a>
  - <a href="#table-families-and-first-review-order"
    id="toc-table-families-and-first-review-order">Table families and first
    review order</a>
  - <a href="#recommended-database-organization"
    id="toc-recommended-database-organization">Recommended database
    organization</a>
  - <a href="#read-only-runtime-evidence-required-next"
    id="toc-read-only-runtime-evidence-required-next">Read-only runtime
    evidence required next</a>
  - <a href="#files-produced" id="toc-files-produced">Files produced</a>

# Tempo database audit — first source pass

**Pinned source:** `7d77ed1b25198dcd320e0265f777cc630f23775e`, current
`main` retrieved October 21, 2026. Read-only source analysis. No
services, migrations, application imports, live database queries, data
writes or tests were run.

## Principal findings

1.  The database sprawl is real in source, but “half the live tables are
    unused” is not established. The ordered migration census
    independently reproduces **163 legacy tables, 70 Learning tables, 20
    Content tables, 25 Intelligence tables and 14 Game Evidence
    tables**. These describe separate installation schemas. Adding them
    does not reveal the table count of any actual deployed database. The
    12-table remediation definition is included in both the legacy and
    Learning counts; it is not another 12 to add. The inventory
    therefore has 292 **owner-qualified source rows**, not a claim of
    292 live tables.
2.  The largest potential reduction is retiring the legacy runtime and
    its optional producers after identifying the actual Study
    deployment. `database/tempo_legacy_database` contains **22,389 of
    31,238 runtime/DDL physical lines (71.7%)** under `database/`.
    Learning's corresponding count is 3,889. Cutting repeated decorators
    or query helpers alone will not approach 50%.
3.  **57 table names occur in both the legacy and Learning source
    schemas.** They represent different owner installations and may
    legitimately hold separate state during cutover. They are evidence
    of parallel generations to resolve, not tables that can be
    deduplicated in place.
4.  All **seven `_legacy` physical tables** remain referenced by current
    source SQL view definitions, selecting generation-zero data until
    newer generations are published. They also remain import targets in
    `database/tooling/migrate_sqlite_to_postgres.py:42–50`. A search
    limited to Python SQL calls would incorrectly flag them as unused.
5.  Logical separation has already created many short files. The legacy
    owner has 279 runtime/DDL files, 120 of them with 1–30 physical
    lines; its nonempty median is 45.5 lines. Adding a file per query
    would worsen navigation. Cohesive modules should group one use case
    or storage capability, not one SQL statement.
6.  Two Learning tables deserve a focused follow-up:
    `learning_obligations` and `queue_projection_diagnostics` have no
    direct own-runtime SQL matches in this census.
    `services/learning/runtime/tests/test_remediation.py:317–339`
    explicitly verifies that V2 uses `remediation_contributions` while
    leaving the older `learning_obligations` and `evidence_deliveries`
    paths empty for that scenario. This supports investigating old
    paths; it does not prove every legacy protocol, snapshot or live
    deployment can discard them.

## Evidence and counting method

`build_database_inventory.py` parses checked-in ordered SQL migrations
for `CREATE TABLE`, table renames and table drops; it adds the
remediation DDL using the explicit Learning manifest and legacy
migration-050 inclusion. No SQL is executed. Resulting table names match
all five source-schema README indexes. It also records final
`CREATE OR REPLACE VIEW` definitions, current view dependencies, static
SQL reader/writer matches, literal table-name mentions, creation paths
and line numbers. Read/write matches are navigation evidence only:
dynamic identifiers, views, triggers, reflection, imported SQL and
external consumers can be missed. DDL includes current source
definitions, not proof that deployed migrations are current.

Physical lines include blank lines and comments. The runtime/DDL
denominator includes `.py`, `.sql`, `.ts`, `.js`, `.mjs`, `.mts`, `.sh`
under database owners/browser trees, excludes tests/tooling, and
includes migration history and rollback SQL. JSON, lockfiles, package
metadata and prose are separate. This metric is reproducible, but it is
not executable-statement count or complexity.

| Database category                     | Physical lines |
|---------------------------------------|---------------:|
| Runtime adapters, connections and DDL |         31,238 |
| Tests, fixtures and harnesses         |         30,380 |
| Operational/migration tooling         |          5,522 |
| Documentation                         |         13,533 |

| Runtime/DDL owner                     | Files | Physical lines |
|---------------------------------------|------:|---------------:|
| Legacy                                |   279 |         22,389 |
| Learning                              |    39 |          3,889 |
| Intelligence                          |    21 |          1,344 |
| Content                               |    10 |            829 |
| Game Evidence                         |    10 |            646 |
| Shared remediation definition/adapter |     4 |            456 |
| Browser storage                       |    44 |          1,466 |
| Browser intents                       |     1 |            219 |

The census mechanically indexes **all 705 files under `database/`** with
role, size, Python symbols/imports and docstrings. It does not mean all
705 files received a semantic line-by-line review.
`database-reviewed.json` records the individually inspected files and
exact review depth; unreviewed files remain explicitly indexed-only.

## First file-level responsibility findings

| Current path                                                                                        | Purpose/responsibilities found                                                                                                                                                                                                                                                                                                   | Proposal and removal conditions                                                                                                                                                                                                                                                                                                                                                                              |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `database/tempo_legacy_database/connections.py` — 1,766 lines                                       | Connection routing; query-only context; foreground/background admission and telemetry; SQLite transaction locking; PostgreSQL version check; SQLite initialization, historical upgrades, backfills, triggers and task seeding. `initialize()` spans lines 193–1766; PostgreSQL returns after its version check at lines 194–205. | Decide whether SQLite runtime support is still required. If PostgreSQL-only is confirmed, retire the SQLite serving/upgrade implementation and preserve any necessary old-snapshot converter as explicit offline tooling. If retained, split connection acquisition, legacy SQLite migration and outer activity-gate composition. Do not change Study's connection authority as part of a code-size cleanup. |
| `database/tempo_legacy_database/adapters/postgres_store.py` — 331 lines                             | Row compatibility, SQLite-to-PostgreSQL SQL translation, timing instrumentation, connection pools, timeouts, metrics buffering and commits.                                                                                                                                                                                      | Separate compatibility translation from connection/pool authority only if this runtime survives. The corresponding Learning adapter deliberately has no commit authority. Do not merge them into a shared singleton database package.                                                                                                                                                                        |
| `database/tempo_learning_database/adapters/postgres_store.py` — 174 lines                           | Owner-local row interface and SQL translation; borrowed `PostgresConnection`; forwards acquisition to Learning's connection owner.                                                                                                                                                                                               | Preserve while Study uses SQLite-shaped SQL. A PostgreSQL-only adapter simplification requires translating callers and proving the same transactions; simply deleting SQLGlot breaks real calls.                                                                                                                                                                                                             |
| `database/tempo_learning_database/connections.py` — 109 lines                                       | Three bounded pools, per-workspace context, read-only and timeout setup, restricted-role/workspace verification, transaction and close ownership.                                                                                                                                                                                | Protected Study infrastructure. Its short code is consequential. Keep one owner and explicit context managers. No generic cross-domain transaction decorator.                                                                                                                                                                                                                                                |
| `database/tempo_legacy_database/adapters/authored_repertoires/opportunities/storage.py` — 625 lines | Opportunity feed/decision summaries; graph/card facts; game-derived gap queries; coverage facts; slice progress; dismissal/snooze; explicit training admission.                                                                                                                                                                  | If the feature survives, use capability groups `opportunity_queries`, `opportunity_commands`, `refresh_progress`, and injected facts for Games/coverage. Otherwise retire the entire feature slice including producers, contracts and tests. A whole-file rename would leave mixed ownership intact. Function boundaries mechanically enumerated; selected bodies sampled, not a complete semantic proof.    |
| `database/tempo_legacy_database/adapters/authored_repertoires/integrity/storage.py` — 502 lines     | Stored integrity scans, observations, issue publication, card blocks and source/version facts (from AST/docstring/index; not fully read).                                                                                                                                                                                        | Trace which serving card blocks Study still needs; retained published blocks can outlive optional scanners. Review one integrity capability at a time before removing a producer.                                                                                                                                                                                                                            |
| `database/tempo_legacy_database/adapters/durable_tasks/storage.py` — 472 lines                      | Task/event SQL; dynamic kind filtering; lease/reclaim/retry writes; PostgreSQL/SQLite differences; task-query warmup; queue refresh status SQL.                                                                                                                                                                                  | Separate owner-local task persistence from queue-specific updates if retained. The bigger simplification is deleting obsolete job kinds and their scheduling mechanisms after owner retirement. Retry/fencing/receipt semantics cannot be hidden behind a universal decorator.                                                                                                                               |
| `database/tempo_legacy_database/adapters/commands/studies/storage.py` — 398 lines                   | Authored Studies/chapter/exercise CRUD, revisions, enrollment, and effects on cards/queues.                                                                                                                                                                                                                                      | Keep authored Studies distinct from daily Study. Review direct card/queue effects and replace with the surviving Learning interface if authoring remains. The file's namespace alone does not create that boundary.                                                                                                                                                                                          |
| `database/tempo_legacy_database/adapters/authored_repertoires/card_deletion/storage.py` — 193 lines | Tombstones, task cancellation, card/queue/attempt/evidence cleanup, repertoire retention/reassignment, Games references and graph reparenting.                                                                                                                                                                                   | This is an actual cross-feature workflow. Keep the atomic command owner explicit. Split capability implementations by owner only after preserving retained-card history and deletion behavior; do not give each helper an independent commit. Dynamic table lists also demonstrate why text-only “unused” results are unreliable.                                                                            |
| `database/tempo_content_database/adapters/store.py` — 607 lines                                     | `ContentService` owns pool/transaction scopes, source staging/admission, inventory lifecycle, route/exercise/binding commits, garbage collection, publication storage and paginated inspection. It calls application helpers in `tempo_content` as well as SQL.                                                                  | If Content remains the canonical importer, organize source persistence, inventory persistence, publication persistence and connection owner with a thin outer service. Do not duplicate its contract in Learning just to avoid a Content process. Selected source and symbol map reviewed; not every body read.                                                                                              |
| `database/tempo_content_database/adapters/bridge.py` — 89 lines                                     | Locked source reservation; draft/operation reads and state mutations; export/provenance lookups borrowing caller transaction.                                                                                                                                                                                                    | Cohesive bridge persistence worth keeping if the bridge is retained. Avoid an abstract universal approval/receipt engine; its source lock is owner-specific.                                                                                                                                                                                                                                                 |
| `database/tempo_game_evidence_database/adapters/store.py` — 214 lines                               | Transaction owner, migrations, game import, snapshot loads, comparison jobs, leasing/rescope, publication/reconciliation, report/query projection.                                                                                                                                                                               | If Games is retained, separate import storage, snapshot storage, comparison job storage and report reads; the existing provider/delivery/reconciliation adapters are reusable. Keep one transaction authority around publication. Complete file read.                                                                                                                                                        |
| `database/tempo_learning_database/adapters/publications.py` — 299 lines                             | Acceptance/activation identity and receipts, publication/repertoire/card locks, accepted definition persistence, serving card materialization.                                                                                                                                                                                   | Protect activated content required by Study. The importer can be optional at serve time, but its accepted cards and serving generation cannot be deleted. Admission/activation can later be organized into owner-local capabilities with one atomic commit.                                                                                                                                                  |
| `database/tempo_learning_database/adapters/gateway.py` — 75 lines                                   | Command intent/digest binding, operation locks, logical request identity, savepoints, completion/failure receipts.                                                                                                                                                                                                               | Protected retry/idempotency capability. Keep savepoint and receipt completion visibly inside the caller transaction. Complete read.                                                                                                                                                                                                                                                                          |
| `database/tempo_legacy_database/adapters/commands/receipts/storage.py` — 146 lines                  | More elaborate legacy queued/executing/retrying/blocked command lifecycle with leases, retry cycles, background admission, recovery and transition progress.                                                                                                                                                                     | A specific legacy-runtime retirement target if all callers move off it. Not interchangeable with Learning's smaller gateway just because both use `operation_receipts`. Complete read.                                                                                                                                                                                                                       |
| `database/tempo_legacy_database/adapters/http_composition/snapshots.py` — 74 lines                  | Fixed allowlist of portable snapshot tables plus dynamic `SELECT * FROM {name}` export.                                                                                                                                                                                                                                          | Include export/migration support when deciding tables are unused. Removing retired feature tables requires changing the snapshot contract or preserving a read-only archived exporter. Complete read.                                                                                                                                                                                                        |
| `database/tooling/migrate_sqlite_to_postgres.py`                                                    | Offline import/verify including seven historical-table mappings.                                                                                                                                                                                                                                                                 | Keep only the supported import window/version set. After an explicit support retirement, preserve converter in Git history or a documented offline artifact; do not carry old runtime compatibility forever. Initial 150 lines and mapping reviewed.                                                                                                                                                         |

## Duplication versus parallel generations

An AST comparison found **10 exactly matching function groups** across
the legacy and Learning database packages (functions at least seven
physical lines; formatting ignored). Examples include `postgres_sql`,
frozen-order annotation and stale-queue helpers, progression unlock and
review-card integrity reads. Depending on retained formatting,
collapsing those exact matches would remove roughly **165–235 physical
lines**, less than 1% of database runtime/DDL. This is not an exhaustive
semantic clone analysis, but it is enough to reject decorators as the
main route to 50%.

The substantial cost is keeping two serving models, two lifecycle
implementations, compatibility import paths and optional feature
families. Sharing a runtime adapter just to deduplicate a few helpers
can make the surviving Study service depend on the runtime being
retired. Prefer deleting a redundant generation over introducing a
shared framework.

## Table families and first review order

| Review slice                     | Concrete current source                                                                                                                                                                                 | First question and disposition                                                                                                                                                   |
|----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Study authoritative facts        | Cards, memberships, accepted/serving publications, daily queue/day membership, attempts, review history, scheduling snapshots, teaching, frozen ranks/activation, command receipts, deletion tombstones | Which installation actually serves phone/desktop, and which exact facts are needed for new introductions and review replay? Protect first.                                       |
| Parallel legacy Study            | 57 same-named legacy/Learning tables; legacy queue/admission/review adapters                                                                                                                            | Is all serving on Learning? If yes, extract required historical exports, stop old writers, remove old runtime code independently of physical data deletion.                      |
| Historical game generations      | Seven `_legacy` / seven `_staged` pairs and seven visible views                                                                                                                                         | Do any generation-zero rows still serve? Is the old importer still required? Retire view fallback and writers before table retirement.                                           |
| Optional Games/analysis pipeline | Legacy game projections, feature rows, reports, findings, game jobs, scope generations; independent Game Evidence and Intelligence schemas                                                              | Choose the one intended future Games implementation. Keep known useful raw game source data; remove whole duplicate producers and route surfaces after Study independence proof. |
| Optional adaptive discovery      | Coverage, opportunity, introduction-priority, segmentation, canonical prefix preview and transition families                                                                                            | Is each producing an actually used output? Frozen ranks may replace online ranking but graph ancestry may still govern new introductions. Trace both before deleting a family.   |
| Older Learning obligations       | `learning_obligations`; older `evidence_deliveries` protocol                                                                                                                                            | Current V2 acceptance explicitly avoids these in one regression. Determine supported V1 callers/imports and historical retention before removing the older protocol.             |
| Metadata and receipts            | Migration history, workspace identity, command/evidence receipts, tombstones                                                                                                                            | Few lines, high safety value. Retain unless their whole owner or protocol is retired. Empty or low-write tables can be essential.                                                |

Seven legacy compatibility tables are only **4.3%** of the 163-table
legacy schema; dropping them alone would barely move the target and
could damage fallback reads. Conversely, a proven whole legacy-owner
retirement would remove 71.7% of this database-code denominator. Neither
is an approved deletion list. The 50% target is plausible as a
feature/runtime-retirement program; it is not yet a measured safe cut.

## Recommended database organization

Keep the existing single physical `database/` home required by current
working rules. Group adapters by the surviving owner and logical
capability. Within Learning, coherent groups are queue
admission/selection, review/attempt persistence, accepted content
activation, frozen ordering and evidence, and the connection/transaction
owner. Retain service-local interfaces in the logical domain package;
outer composition wires the database implementations. The database
directory should not choose product policy or orchestrate another
domain. Small query helpers may live together when they share one
transaction/capability; neither a mega `store.py` nor a file per query
is the goal.

Do not move protected Study files as part of the first deletion batches.
Keep source moves distinct from schema changes. Establish one surviving
writer per concept before consolidating tables. A module split is useful
only if a future agent can understand a capability by reading its
interface, one implementation, and the small owning tests.

## Read-only runtime evidence required next

`database-runtime-census.sql` is prepared but **not executed**. Run it
separately against each actual installation under an approved reader. It
inventories actual tables and estimated sizes, usage counters and reset
time, active application names, foreign keys, view dependencies and
triggers. Do not enable extensions or reset counters for this audit.

Take two snapshots around representative use: desktop and phone
bootstrap, a due review, a new introduction, a failed/corrected attempt,
reload/retry after a completed command, and the next day boundary. Usage
deltas identify candidates for closer review, not proof of unused
status; infrequent recovery/import paths and views may remain essential.
`n_live_tup` is an estimate, scan counts reset, and SELECT/UPDATE
counters alone do not establish reachability. Compare deployed version
and actual database identity with the pinned source before interpreting
results.

For each proposed retirement record: owning feature, source
readers/writers, indirect view/trigger/foreign-key dependencies,
deployed consumer/process, last required user workflow, data export or
retained history, reversible code-disable result, Study/replay
acceptance, and eventual data-migration decision. Remove runtime code
first when the old writer is proven inactive; keep durable data intact
until the retention/migration step is approved.

## Files produced

- `database-tables.csv` and `.json`: 292 owner-qualified current-source
  tables, creation/rename evidence, family, literal and SQL references,
  current view consumers, explicit live-status uncertainty.
- `database-file-index.json`: all 705 database-tree files mechanically
  indexed, including Python symbol boundaries and imports.
- `database-reviewed.json`: selected semantic/source inspections with
  precise review depth and proposed next action.
- `database-exact-duplicates.json`: ten exact AST function-match groups
  across legacy and Learning adapters.
- `database-runtime-census.sql`: read-only next-step query, not
  executed.
- `build_database_inventory.py`: reproducible static inventory
  generator.

The complete line-by-line semantic audit remains a running task. This
first pass establishes counts, source ownership, high-leverage cuts and
concrete dependency traps; it does not certify all lines, all live
usage, or any destructive migration.