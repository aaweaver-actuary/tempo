# Persistence-family disposition for #362

Source preparation at `1a0fd5c582c13f9d0de428d2f66d7e7cb358c4bf`, 2026-10-25.
**No family is approved for migration, DROP, DELETE or retirement execution.**
There is no target live table count. This document supplies the family-specific
release prerequisites missing from the earlier count-oriented recommendation;
it does not repeat the source-consumer inventories or issued-work truth tables.
Actual installation versions, schemas, roles, pending work and private restore
evidence remain UNKNOWN here. No application, database, broker or browser was
inspected. Source DDL names below are not an actual-version mapping.

The disposition owner is the `persistence_362` task. No migration implementer is
released: [#362](https://github.com/aaweaver-actuary/tempo/issues/362) approves no
family. A later approval must name its exact family and give one owner exclusive
migration paths and expected fixtures. Other component owners retain their own
connections, packaging, transactions and migration authority.

## Existing evidence and its limits

- [#400 / #387](https://github.com/aaweaver-actuary/tempo/pull/400) already supply
  the [metadata-only census](tooling/study_census.sql) and its focused tests.
  Reuse them after the authorized operator selects the actual target. Metadata
  estimates, zero scans and source-search absence do not establish disuse.
- [#414](https://github.com/aaweaver-actuary/tempo/pull/414) supplies the existing
  [eight-group migration map](../docs/architecture/study-first-reduction-2026-10-21/MIGRATION-MAP.md).
  [#389](https://github.com/aaweaver-actuary/tempo/issues/389) remains open; that
  source map and synthetic comparisons do not release the four converters in
  [#267](https://github.com/aaweaver-actuary/tempo/issues/267).
- [#423](https://github.com/aaweaver-actuary/tempo/pull/423) is a merged candidate
  manifest. [#427](https://github.com/aaweaver-actuary/tempo/pull/427),
  [#428](https://github.com/aaweaver-actuary/tempo/pull/428) and
  [#429](https://github.com/aaweaver-actuary/tempo/pull/429) remain draft source
  packets. Reuse their exact consumers, protected interfaces and transition
  tables at the pinned heads below; none approves physical storage retirement.
- [#431](https://github.com/aaweaver-actuary/tempo/pull/431) merged only the legacy
  review adapter's optional priority hook change. Saved priorities, jobs, holds,
  independent Learning notifications and the other six withdrawal hunks are
  unaffected. A merged manifest is not evidence that its capability was removed.

## Named families and exact next prerequisites

These scopes overlap only through explicitly retained shared infrastructure.
They are not disjoint counts and must not be combined into one migration.

| Family and source scope | Current disposition / retained public boundary | Exact additional gate before releasing a family migration owner |
| --- | --- | --- |
| **Games guided review:** `public.guided_review_sessions`, `public.guided_review_attempts`; [DDL](tempo_legacy_database/ddl/migrations/001_initial.sql), [dictionary](tempo_legacy_database/schema/study-queue-review-1/README.md). | Retain both tables, session/finding/game identity, all attempts and saved status. The [#423 manifest](https://github.com/aaweaver-actuary/tempo/blob/fb63771d475d608fc65f998d53a2d879f5c71d36/docs/architecture/study-first-reduction-2026-10-21/RETIREMENT-BATCHES.json) owns exact server consumers/protected paths. `migration_snapshot`, browser table mapping, original command status/retry and replay remain protected. Browser-client retirement did not settle cold v1/v2 pending keys. | Resolve `games.guided_review.start` and `.attempt` receipts, broker deliveries, active/completed sessions and original-authority browser envelopes under #358/#267/#389. Supply a golden fixture preserving session order/index/status, attempt `(session_id,finding_id)` identity and original operation payload/result; prove the retained export/readback can restore them. Retain the existing handler/path if an obligation still needs execution; if that conflicts with removal, reject or re-scope the #390 manifest before execution. Do not add a placeholder, shim or framework under this task, or approve a table drop because server source is removed. |
| **Adaptive priority publications and work:** `public.repertoire_card_introduction_priorities`, `repertoire_card_priority_generations`, `repertoire_priority_publications`, `repertoire_priority_jobs`, `repertoire_priority_preparations`, `repertoire_priority_prepared_rows`; [DDL 001](tempo_legacy_database/ddl/migrations/001_initial.sql), [021](tempo_legacy_database/ddl/migrations/021_priority_preparations.sql). Shared `priority_source_epoch`, `priority_repertoire_source_epochs` and refresh identities are dependencies, not exclusive retirement targets. | Retain saved current-view published-first/legacy fallback, score/line/frontier facts, `priority_status`, frozen ranks, active generation/preparation, errors and holds. Reuse [#427's exact contract](https://github.com/aaweaver-actuary/tempo/blob/575f65fdf9bdc82dc8d58a9321d19797e8710da6/docs/architecture/study-first-reduction-2026-10-21/adaptive-priority-2026-10-25/STOP-NEW-RECOMPUTATION.md). Shared scoring/source-version locks and all four opportunity callbacks remain. | Resolve #427 D1 existing recovery versus existing hold, D2 old saved source-operation optional effects, and D3 opportunity tail. Do not enable optional work to drain it or promise a finite drain: recovery can create another generation. A fixture must preserve pointer visibility, per-card fallback, current-view rejection of stale rows, pending generation/cursor/lease and truthful failure/hold state; #431 settles only its single hook. Any later removal of saved-reader storage needs the actual serving-authority and #389 map, not an inferred switch to independent Learning. |
| **Opportunity, recommendation and admission facts:** `public.repertoire_opportunities`, `discovery_admission_intents`, `discovery_recommendation_requests`, `coverage_discovery_recommendation_requests`; `current_repertoire_opportunities` is a **view**. [Dictionary](tempo_legacy_database/schema/discovery-priority/README.md), [coverage dictionary](tempo_legacy_database/schema/authored-content-1/README.md), [revisioned admission DDL](tempo_legacy_database/ddl/migrations/028_revisioned_discovery_admissions.sql), [view DDL](tempo_legacy_database/ddl/migrations/035_canonical_publication_freshness.sql). | Retain ordinary queue eligibility from current non-analysis weak-known-decision rows, explicit train/state-action APIs, `discovery.accept`, original intent identity, mounted outbox status/retry and existing graph/coverage/integrity/queue owners. Reuse [#429's 23-row truth table](https://github.com/aaweaver-actuary/tempo/blob/26544be0a8e3adf18701996efc9649ffd146288d/docs/architecture/study-first-reduction-2026-10-21/RETIREMENT-BATCHES.json). | Accept the exact refresh/client cessation boundary and reconcile priority callbacks; classify refresh, recommendation and admission tails separately. Golden cases must retain dismissed/handled evidence snapshots, revision/fingerprint identity, missing-intent browser submissions, queued versus preparing intents and inconsistent complete-task/preparing-intent states. Preserve the distinct PostgreSQL atomic settlement and legacy coordinator/readmission rules. Stopping feed refresh does not remove stored eligibility or accepted materialization. |
| **Daily statistics:** `public.daily_chess_snapshots`, `daily_chess_insights`, `daily_statistics_jobs`; [DDL](tempo_legacy_database/ddl/migrations/001_initial.sql), [statistics adapter](tempo_legacy_database/adapters/statistics/README.md). | Retain history/status/version/timestamps and existing `migration_snapshot`/browser mappings. `statistics.daily.refresh`, original receipt/status/retry, native task and legacy day-job execution/control remain while obligations are unsettled. Reuse [#428's exact consumers and eight examples](https://github.com/aaweaver-actuary/tempo/blob/e4b6c557a368fdc6d4b627dc9e228f41031499a5/docs/architecture/study-first-reduction-2026-10-21/daily-statistics-retirement-2026-10-25/README.md). `game_feature_rows` and its canonical writer → phase 6 → priority handoff remain outside retirement. | Approve target/cutoff and the disposition of command/broker envelopes, native tasks, legacy jobs, pauses and abandoned running work. Coordinate cessation of the named game-sync history invalidators before freezing results; game import and feature derivation must continue. Golden expected state preserves `waiting-sync`/`waiting-analysis` even when its materialization job is complete, insight evidence and original errors. A complete admission receipt or complete job does not certify complete metrics. |
| **Seven compatibility import targets:** `public.game_move_analysis_legacy`, `game_move_analysis_candidates_legacy`, `game_position_occurrences_legacy`, `game_repertoire_matches_legacy`, `repertoire_decision_events_legacy`, `repertoire_comparisons_legacy`, `gameplay_events_legacy`. | Retain the exact [COPY_TARGETS](tooling/migrate_sqlite_to_postgres.py) mappings and historical source-version support. These names are versioned compatibility storage, not an unused-family certificate. Retain staged/publication/view relationships and supporting exporters. | #358 must establish the actual source and destination migration identities; #389 must classify each fact as mapped, explicitly retained cold or blocking. Specify supported snapshot versions and a restore fixture that exercises the actual compatibility names, identities and dependencies. Review versioned FKs/views/triggers and import/export consumers before naming any independently removable subfamily. Do not migrate all seven because their suffix matches. |
| **Previously named investigation candidates:** `learning.learning_obligations` and `public.queue_projection_diagnostics`. | No approved retirement. These belong to different owners. Learning's [core DDL](tempo_learning_database/ddl/migrations/0001_core.sql) retains obligations. Legacy [diagnostic reads](tempo_legacy_database/adapters/http_composition/queue_projection.py) and [publication writes](tempo_legacy_database/adapters/queue_refresh/application/publication.py) remain current consumers; [dictionary](tempo_legacy_database/schema/study-queue-review-1/README.md) records the diagnostic shape. | Investigate each separately only against its actual owner/version and required semantics. An obligation's evidence/revision/card/decision/publication/kind/satisfaction/payload cannot be dropped as a projection without #389 disposition. Diagnostics still have read/write/cleanup behavior; source absence and safe-drop status are not established. Neither candidate can inherit approval from an optional Games source cohort. |

All other owner schemas, browser stores, Learning remediation, saved exercise
execution, frozen rank/activation, receipts and pending recovery remain retained.
Similar names across the 57 legacy/Learning overlaps recorded in #362 do not
establish duplicate authority. No blanket consolidation or owner reassignment is
released.

## Required release input for one specifically approved family

1. **Actual authority:** #358's timestamped installation/build, database/schema,
   migration identity and roles, plus reviewed census dependencies. The operator
   retains private evidence; GitHub receives only a sanitized receipt. Source
   `main` and synthetic fixtures are not deployment evidence.
2. **Fact and dependency disposition:** approved #389 mapping for required history
   groups; exact table/field/key/source-version scope; every source reader/writer,
   FK/view/trigger and import/export consumer resolved. Preserve unknown provenance
   as unknown and pending identity/payload/results through public interfaces.
3. **Family-specific policy:** the row's distinct cessation/settlement/hold/frozen
   history decisions, plus explicit cold-retention and restore/readback ownership.
   Failed or paused work remains failed or paused until separately resolved.
4. **Reviewable source task:** one owner, bounded migration/up/down or restore
   procedure, exact writable files and versioned synthetic golden input/output.
   Reuse the existing normalized-snapshot comparator and owning tests; it cannot
   detect facts an exporter omitted. A separately authorized disposable rehearsal
   must compare identities, history and pending semantics, with repeat/import,
   interruption, conflicts and restoration covered—not counts alone.
5. **Separate execution decision:** private backup/restore proof, final writer
   fence and latest-state/pending reconciliation; applicable #359 launcher and
   #369 activation/forward-repair gates. Only the designated backup executor may
   prepare actual backup evidence. No live execution follows from this source PR.

Launcher planner, fake-lock lifecycle and readiness preparation do not adopt the
new default CLI/Compose path. The current normal launcher still depends on the
legacy root services and rejects v2 registration; #371 §6 remains the source
removal gate. The history contract remains `accepted-transfer-contract-pending`.
Neither preparation fixture closes actual/latest-history, authority or separate
cutover acceptance. Preserve those launcher/runtime/Compose/backend dependencies
until the accepted public receipts establish the applicable gate.

Cold retention can satisfy a disposition when the retired producer/installation
has no live source dependency and saved facts remain recoverable through the
accepted public interfaces. An unresolved pending obligation that still requires
execution prevents treating its handler as cold. Keep that owner or resolve the
obligation first. Cold storage need not be rewritten into another component or
physically deleted. A composition root may wire public contracts; no family
migration may introduce cross-domain internal imports, shared mutable ownership,
cycles or a hidden service dependency. Optional plugin failure must leave
unrelated Study and Content workflows usable.

Architecture impact: none. This records prerequisites and corrects planning;
implemented interfaces, diagrams, schemas, transactions and lifecycle ownership
are unchanged. It authorizes no data observation, migration, source withdrawal,
merge, deployment or host change.
