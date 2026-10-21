# Database inventory and scope

[Root index](README.md). Dedicated DDL, connection managers, concrete persistence adapters, SQL-only helpers, PostgreSQL maintenance/proof scripts, IndexedDB connection/record stores and backup adapters were moved into this single tree. Owner public APIs continue to wire their own implementations; no component imports another database package's private adapters as a new dependency.

The original PR334 checklist contains **156 mixed application/SQL candidates**. Completed batches cover **62 files**; one candidate is confirmed non-database application code, and **93 remain for review/extraction**. Complete centralization is unfinished while database operations remain outside the root tree or application policy remains inside it. This is a working checklist, not a permanent exception. Preserve one implementation and the owning component's interfaces, packaging and transaction authority. Application-facing tests and wire schemas retain their owners. No table is declared unused from its name or lack of a text match.

Baseline mixed source files found by a conservative database-call/IndexedDB text scan: **156**. False positives (SQL examples/comments) are possible; this is a navigation inventory, not dependency proof.

<details>
<summary>Paths for follow-up extraction review</summary>

- Completed browser storage split: [app/lib/study-connection.ts](../app/lib/study-connection.ts) retains application connection/authority/transport decisions; [database/browser/study-connection-storage.ts](browser/study-connection-storage.ts) owns the existing IndexedDB inspection and Web Storage operations.
- Completed commands SQL split: [backend/app/account_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/activity_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/annotation_commands.py](tempo_legacy_database/adapters/commands/README.md).
- [backend/app/branch_commands.py](../backend/app/branch_commands.py)
- [backend/app/canonical_prefix_api.py](../backend/app/canonical_prefix_api.py)
- [backend/app/card_commands.py](../backend/app/card_commands.py)
- [backend/app/card_deletion.py](../backend/app/card_deletion.py)
- [backend/app/command_gateway.py](../backend/app/command_gateway.py)
- [backend/app/coverage_maia_commands.py](../backend/app/coverage_maia_commands.py)
- Completed commands SQL split: [backend/app/endgame_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed legacy Games command SQL split: [backend/app/finding_card_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/finding_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_analysis_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_analysis_publication.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_commands.py](tempo_legacy_database/adapters/games/README.md).
- [backend/app/game_evidence_commands.py](../backend/app/game_evidence_commands.py)
- Completed legacy Games command SQL split: [backend/app/game_sync_commands.py](tempo_legacy_database/adapters/games/README.md).
- [backend/app/guided_review_commands.py](../backend/app/guided_review_commands.py)
- [backend/app/integrity_repair_commands.py](../backend/app/integrity_repair_commands.py)
- [backend/app/main.py](../backend/app/main.py)
- Completed controls SQL split: [backend/app/opening_evidence_api.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/opening_segmentation_api.py](../backend/app/opening_segmentation_api.py)
- [backend/app/opportunity_commands.py](../backend/app/opportunity_commands.py)
- [backend/app/pgn_import_commands.py](../backend/app/pgn_import_commands.py)
- [backend/app/prefix_diagnostics_api.py](../backend/app/prefix_diagnostics_api.py)
- [backend/app/prefix_evaluation_api.py](../backend/app/prefix_evaluation_api.py)
- [backend/app/prefix_split_commands.py](../backend/app/prefix_split_commands.py)
- [backend/app/prefix_transition_api.py](../backend/app/prefix_transition_api.py)
- Completed queue/lifecycle storage split: [backend/app/queue_attempt_origins.py](tempo_legacy_database/adapters/lifecycle/README.md).
- [backend/app/queue_commands.py](../backend/app/queue_commands.py)
- [backend/app/repertoire_commands.py](../backend/app/repertoire_commands.py)
- Completed controls SQL split: [backend/app/repertoire_settings.py](tempo_legacy_database/adapters/controls/README.md).
- Completed controls SQL split: [backend/app/review_commands.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/services/analysis_paste.py](../backend/app/services/analysis_paste.py)
- [backend/app/services/background_activity.py](../backend/app/services/background_activity.py)
- [backend/app/services/background_diagnostics.py](../backend/app/services/background_diagnostics.py)
- Completed queue/lifecycle storage split: [backend/app/services/background_metrics.py](tempo_legacy_database/adapters/lifecycle/README.md).
- [backend/app/services/canonical_prefix.py](../backend/app/services/canonical_prefix.py)
- [backend/app/services/canonical_prefix_preview.py](../backend/app/services/canonical_prefix_preview.py)
- [backend/app/services/canonical_scope_freshness.py](../backend/app/services/canonical_scope_freshness.py)
- Completed defensive settings/predicate storage split: [backend/app/services/defensive_analysis.py](tempo_legacy_database/adapters/defensive_analysis.py).
- [backend/app/services/discovery_admission.py](../backend/app/services/discovery_admission.py)
- [backend/app/services/durable_scheduling.py](../backend/app/services/durable_scheduling.py)
- [backend/app/services/durable_tasks.py](../backend/app/services/durable_tasks.py)
- [backend/app/services/engine_scheduling.py](../backend/app/services/engine_scheduling.py)
- Completed SQL/DDL split: [backend/app/services/frozen_opening_order.py](../backend/app/services/frozen_opening_order.py) now only binds [Study policy](../backend/app/study/frozen_order/README.md) and [database storage](tempo_legacy_database/adapters/frozen_order.py). Legacy caller composition migration remains.
- [backend/app/services/game_analysis_worker.py](../backend/app/services/game_analysis_worker.py)
- [backend/app/services/game_findings.py](../backend/app/services/game_findings.py)
- Completed legacy Games imports storage split: [backend/app/services/game_sync.py](tempo_legacy_database/adapters/games/imports/README.md).
- [backend/app/services/game_sync_coordinator.py](../backend/app/services/game_sync_coordinator.py)
- Completed legacy Games evidence storage split: [backend/app/services/gameplay_events.py](tempo_legacy_database/adapters/games/evidence/README.md).
- [backend/app/services/guided_review.py](../backend/app/services/guided_review.py)
- [backend/app/services/introduction_priorities.py](../backend/app/services/introduction_priorities.py)
- [backend/app/services/learning_review_adapter.py](../backend/app/services/learning_review_adapter.py)
- [backend/app/services/legacy_game_authority.py](../backend/app/services/legacy_game_authority.py)
- [backend/app/services/legacy_study_remediation.py](../backend/app/services/legacy_study_remediation.py)
- [backend/app/services/opening_capacity_admission.py](../backend/app/services/opening_capacity_admission.py)
- [backend/app/services/opening_graph.py](../backend/app/services/opening_graph.py)
- [backend/app/services/opening_graph_recovery.py](../backend/app/services/opening_graph_recovery.py)
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/opening_progression.py](tempo_legacy_database/adapters/opening_progression.py).
- [backend/app/services/opening_ranking_snapshot.py](../backend/app/services/opening_ranking_snapshot.py)
- Completed controls SQL split: [backend/app/services/postgres_coverage_candidates.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/services/postgres_coverage_explorer.py](../backend/app/services/postgres_coverage_explorer.py)
- Completed controls SQL split: [backend/app/services/postgres_coverage_recovery.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/services/postgres_coverage_seed.py](../backend/app/services/postgres_coverage_seed.py)
- [backend/app/services/postgres_daily_statistics.py](../backend/app/services/postgres_daily_statistics.py)
- Completed Games derivation storage split: [backend/app/services/postgres_game_derivation.py](tempo_legacy_database/adapters/games/derivation/README.md).
- Completed Games derivation storage split: [backend/app/services/postgres_game_events.py](tempo_legacy_database/adapters/games/derivation/README.md).
- Completed Games derivation storage split: [backend/app/services/postgres_game_features.py](tempo_legacy_database/adapters/games/derivation/README.md).
- Completed legacy Games evidence storage split: [backend/app/services/postgres_game_findings.py](tempo_legacy_database/adapters/games/evidence/README.md).
- Completed Games derivation storage split: [backend/app/services/postgres_game_misses.py](tempo_legacy_database/adapters/games/derivation/README.md).
- Completed Games derivation storage split: [backend/app/services/postgres_game_priorities.py](tempo_legacy_database/adapters/games/derivation/README.md).
- Completed legacy Games repertoire storage split: [backend/app/services/postgres_game_repertoire.py](tempo_legacy_database/adapters/games/repertoire/README.md).
- Completed legacy Games sync storage split: [backend/app/services/postgres_game_sync.py](tempo_legacy_database/adapters/games/sync/README.md).
- Completed legacy Games sync storage split: [backend/app/services/postgres_game_sync_completion.py](tempo_legacy_database/adapters/games/sync/README.md).
- Completed legacy Games sync storage split: [backend/app/services/postgres_game_sync_windows.py](tempo_legacy_database/adapters/games/sync/README.md).
- [backend/app/services/postgres_integrity.py](../backend/app/services/postgres_integrity.py)
- [backend/app/services/postgres_next_opponent.py](../backend/app/services/postgres_next_opponent.py)
- [backend/app/services/postgres_opening_evidence.py](../backend/app/services/postgres_opening_evidence.py)
- [backend/app/services/postgres_opening_graph.py](../backend/app/services/postgres_opening_graph.py)
- [backend/app/services/postgres_opening_segmentation.py](../backend/app/services/postgres_opening_segmentation.py)
- [backend/app/services/postgres_priority.py](../backend/app/services/postgres_priority.py)
- [backend/app/services/postgres_queue_refresh.py](../backend/app/services/postgres_queue_refresh.py)
- [backend/app/services/prefix_split.py](../backend/app/services/prefix_split.py)
- [backend/app/services/prefix_transition_application.py](../backend/app/services/prefix_transition_application.py)
- [backend/app/services/priority_retention.py](../backend/app/services/priority_retention.py)
- [backend/app/services/queue_admission_adapter.py](../backend/app/services/queue_admission_adapter.py)
- [backend/app/services/queue_application.py](../backend/app/services/queue_application.py)
- [backend/app/services/redis_admission_gate.py](../backend/app/services/redis_admission_gate.py)
- Completed queue/lifecycle storage split: [backend/app/services/refresh_requests.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed legacy read/publication projection split: [backend/app/services/repertoire_comparison.py](tempo_legacy_database/adapters/projections/README.md).
- Completed authored-Studies policy / database line-projection split: [backend/app/services/repertoire_conflicts.py](../backend/app/studies/README.md).
- [backend/app/services/repertoire_coverage.py](../backend/app/services/repertoire_coverage.py)
- [backend/app/services/repertoire_game_refresh.py](../backend/app/services/repertoire_game_refresh.py)
- [backend/app/services/repertoire_integrity.py](../backend/app/services/repertoire_integrity.py)
- [backend/app/services/repertoire_move_verification.py](../backend/app/services/repertoire_move_verification.py)
- [backend/app/services/repertoire_opportunities.py](../backend/app/services/repertoire_opportunities.py)
- Completed legacy read/publication projection split: [backend/app/services/repertoire_statistics.py](tempo_legacy_database/adapters/projections/README.md).
- [backend/app/services/review_service.py](../backend/app/services/review_service.py)
- [backend/app/services/statistics.py](../backend/app/services/statistics.py)
- [backend/app/services/study_attempts.py](../backend/app/services/study_attempts.py)
- Completed controls SQL split: [backend/app/services/study_mode.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/services/study_portable.py](../backend/app/services/study_portable.py)
- [backend/app/services/study_priority.py](../backend/app/services/study_priority.py)
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/tactic_admission.py](tempo_legacy_database/adapters/tactic_admission.py).
- [backend/app/services/tactic_capture.py](../backend/app/services/tactic_capture.py)
- [backend/app/services/tactical_catalog.py](../backend/app/services/tactical_catalog.py)
- Completed legacy read/publication projection split: [backend/app/services/tactical_opportunities.py](tempo_legacy_database/adapters/projections/README.md).
- [backend/app/services/threat_pipeline.py](../backend/app/services/threat_pipeline.py)
- [backend/app/services/threat_training.py](../backend/app/services/threat_training.py)
- [backend/app/settings_commands.py](../backend/app/settings_commands.py)
- Completed intact database snapshot/evidence reader move: [backend/app/snapshot_reads.py](tempo_legacy_database/adapters/snapshot_reads.py).
- Completed commands SQL split: [backend/app/statistics_commands.py](tempo_legacy_database/adapters/commands/README.md).
- [backend/app/study_attempt_commands.py](../backend/app/study_attempt_commands.py)
- [backend/app/study_commands.py](../backend/app/study_commands.py)
- [backend/app/study_routes.py](../backend/app/study_routes.py)
- [backend/app/tactic_commands.py](../backend/app/tactic_commands.py)
- [backend/app/tasks.py](../backend/app/tasks.py)
- Completed commands SQL split: [backend/app/teaching_commands.py](tempo_legacy_database/adapters/commands/README.md).
- [backend/app/threat_analysis_commands.py](../backend/app/threat_analysis_commands.py)
- Completed Content bridge split: [application/public composition](../services/content/src/tempo_content/bridge/README.md) and [database operations](tempo_content_database/adapters/bridge.py).
- Completed Content CLI readiness split: [cli.py](../services/content/src/tempo_content/cli.py) retains parsing/commands; [readiness connection](tempo_content_database/ddl/migrate.py) stays with DDL.
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/analysis_export.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/api.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/delivery.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/providers.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/reconciliation.py](tempo_game_evidence_database/adapters/README.md).
- Completed Intelligence read-projection split: [games_reader.py](../services/intelligence/src/tempo_intelligence/analysis/games_reader.py) retains cursor/limit/envelope/read-transaction composition; [complete owner queries](tempo_intelligence_database/adapters/games_read_projection.py) live in database.
- Reviewed non-database false positive: [Intelligence analysis worker](../services/intelligence/src/tempo_intelligence/analysis/worker.py). Its only `execute` call is `client.execute(claim["job"])`, an engine-client operation; the file has no SQL/driver/cursor operations. Keep this application dispatcher with Analysis.
- [services/learning/runtime/src/tempo_learning_runtime/admission.py](../services/learning/runtime/src/tempo_learning_runtime/admission.py)
- [services/learning/runtime/src/tempo_learning_runtime/api.py](../services/learning/runtime/src/tempo_learning_runtime/api.py)
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/card_deletion.py](tempo_learning_database/adapters/README.md).
- [services/learning/runtime/src/tempo_learning_runtime/defense.py](../services/learning/runtime/src/tempo_learning_runtime/defense.py)
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/durable_tasks.py](tempo_learning_database/adapters/essential-work/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/frozen_opening_order.py](tempo_learning_database/adapters/README.md).
- [services/learning/runtime/src/tempo_learning_runtime/gateway.py](../services/learning/runtime/src/tempo_learning_runtime/gateway.py)
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/maintenance.py](tempo_learning_database/adapters/essential-work/README.md).
- Completed intact Learning-owned SQL-only helper move: [database/tempo_learning_database/adapters/opening_progression.py](tempo_learning_database/adapters/opening_progression.py).
- [services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py](../services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py)
- [services/learning/runtime/src/tempo_learning_runtime/publications.py](../services/learning/runtime/src/tempo_learning_runtime/publications.py)
- [services/learning/runtime/src/tempo_learning_runtime/queue_application.py](../services/learning/runtime/src/tempo_learning_runtime/queue_application.py)
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/queue_attempt_origins.py](tempo_learning_database/adapters/README.md).
- [services/learning/runtime/src/tempo_learning_runtime/queue_commands.py](../services/learning/runtime/src/tempo_learning_runtime/queue_commands.py)
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/real_game_feedback.py](tempo_learning_database/adapters/README.md).
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/receivers.py](tempo_learning_database/adapters/essential-work/README.md).
- [services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py](../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_adapter.py](../services/learning/runtime/src/tempo_learning_runtime/review_adapter.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_reconciliation.py](../services/learning/runtime/src/tempo_learning_runtime/review_reconciliation.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_service.py](../services/learning/runtime/src/tempo_learning_runtime/review_service.py)
- [services/learning/runtime/src/tempo_learning_runtime/study_attempt_commands.py](../services/learning/runtime/src/tempo_learning_runtime/study_attempt_commands.py)
- [services/learning/runtime/src/tempo_learning_runtime/study_attempts.py](../services/learning/runtime/src/tempo_learning_runtime/study_attempts.py)
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/study_bootstrap.py](tempo_learning_database/adapters/README.md).
- Completed intact Learning-owned SQL-only helper move: [database/tempo_learning_database/adapters/tactic_admission.py](tempo_learning_database/adapters/tactic_admission.py).
- [services/learning/runtime/src/tempo_learning_runtime/tactic_commands.py](../services/learning/runtime/src/tempo_learning_runtime/tactic_commands.py)
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/teaching_commands.py](tempo_learning_database/adapters/README.md).

</details>

The scan excludes the centralized tree and tests. Historical evidence/qualification receipts retain their recorded source paths; current active references were relocated. Redis orchestration and host resource coordination remain in their existing infrastructure owners.

## Handoff for the separately coordinated domain move

| Proposed owner | Current source to sort (examples, not a move in this PR) | Boundary to preserve |
| --- | --- | --- |
| Study practice / queue / review | `backend/app/study_*`, `queue_*`, `review_*`, corresponding `services/learning/runtime` application modules and `app` training/queue views | Learning-owned issuance/settlement ports; no Games dependency. |
| Authored/imported studies | `backend/app/services/study_portable.py`, `study_contracts.py`, authored study/exercise controllers | Distinct from practice execution; inject the public practice capability. |
| Games | `backend/app/game_*`, `game_sync_*`, legacy evidence/comparison services and game views | Public evidence/read interfaces; no private queue/table access. |
| Analysis | `backend/app/analysis_*`, analysis orchestration, `services/intelligence` analysis modules | Public jobs/results and report interfaces; provider lifecycle stays outside Study. |
| Tactics / defense / endgames | Their existing commands, policy/grading modules and feature views | Family-specific policy interfaces; inject owner database capabilities. |
| Composition | Application/CLI entry points and dependency wiring | Instantiate concrete adapters from the centralized database tree and pass public interfaces to domains. |

Start the next phase from this PR's merged file map, with one owner per move. A minimal static AST/TypeScript import scan for the specifically moved folders should reject imports into other domains' internal files, permit only explicit interface/contract definitions and outer composition wiring; domain implementation must not import sibling implementation even via its public entry point, and keep Study → Games forbidden. Record existing exceptions explicitly; do not add a new architecture framework or treat a text scan as runtime failure-isolation proof. This branch performs none of those broad domain moves.

### Confirmed source edges to address in that next phase

The parent’s read-only map of main `14ad5754` distinguishes **Study = daily practice** from **Studies = authored/imported PGN chapters and exercises**. Analysis currently exports `BuilderView`.

- `GamesView` imports engine-broker and tactical-opportunities internals; `BuilderView` imports Study hooks/computation and Comparison types.
- Home composes `TacticsView` and `EndgamesView`; move that wiring outside Study. `LegacyWorkspace` reaches into `useTrainingStore` and queue invalidation; outer composition should inject a narrow public Study capability.
- `study-computation.ts` mixes queue/card, repertoire, comparison and builder concerns. Moving it wholesale will not create a domain boundary. Authored Studies imports `StudyExerciseRunner`; inject a public practice capability instead.
- Backend `game_findings` imports tactical/comparison modules; `tactic_capture` imports `review_service`; `study_routes` imports `main.enqueue_daily_queue_refresh`; `study_commands` imports `queue_commands`.
- `app/types.ts`, `domain/index.ts` and `views/index.ts` wildcard barrels are not clean domain interfaces.

Preserve `scripts/study-composition.ts`'s loaded-graph guard (including changed database/browser paths). Reuse existing ESLint/importlinter infrastructure for local sibling-import checks that cover aliases, type imports, re-exports and dynamic imports. Domain implementation must not import sibling implementation even via a public entry point; outer composition connects public interfaces and injects implementations. External libraries are not sibling domains. Keep CI disabled. None of these non-database domain moves is performed in this PR.

## Active extraction work and review boundary

PR334 retains the physical moves, schema documentation and direct-run discovery repairs. A dependent follow-up separates the mixed implementations so this large file-map diff remains reviewable. The checklist contains 155 Python files (46,247 lines), 2,574 database calls across 828 functions, plus one browser file. These are static workload counts, not proof that every function is database-only or that its transaction can be split safely.

Completed first slice: `backend/app/services/frozen_opening_order.py` now contains legacy call-site composition only. Its six-entry SQLite `SCHEMA` list (five tables plus activation seed), 17 guarded table names and 51 generated trigger definitions moved unchanged to [database-owned DDL](tempo_legacy_database/ddl/frozen-opening/README.md). Both original initialization call sites import that DDL directly. All query/write operations moved to [the borrowed-transaction adapter](tempo_legacy_database/adapters/frozen_order.py); preference/eligibility/slot-order decisions moved to [the Study logical folder](../backend/app/study/frozen_order/README.md). Existing function signatures remain at the composition seam. There are no duplicated implementations or generic SQL execution ports.

This removes 29 database-call sites from one 315-line application module. All SQL literals and the three DDL/guard assignment ASTs match the baseline exactly. PostgreSQL migration files, transaction authority, lock order and reader/fingerprint formats are unchanged. Existing legacy callers still import the composition seam: that dependency remains a next-phase domain move, not a claim of complete strict isolation. Two more baseline paths, `opening_progression.py` and `tactic_admission.py`, contain only persistence queries/locks. They moved intact into this owner’s `adapters/` directory; imports, native proof references and existing runner source maps follow their new paths. Their source bytes are unchanged and they add no application policy or shared runtime. The independent Learning runtime versions of those two SQL-only helpers also moved intact to its own `database/tempo_learning_database/adapters/` package, without sharing a runtime implementation with the backend. Its sdist-to-wheel build, installed deferred-import/authentication guard and artifact path/byte checks pass. Browser connection inspection and session/authority storage operations now live in `database/browser/study-connection-storage.ts`; its application wrapper supplies registered database names at the original point in inspection. Intelligence’s analysis worker was a confirmed engine-client `execute` false positive and needs no database move. The subsequent command-reader/repertoire/defensive batch moves the SQL-only snapshot reader intact, separates authored Studies conflict algorithms from their line query, and moves defensive setting/predicate SQL while leaving task policy and HTTP errors outside database. Intelligence’s Games reader also delegates its four complete parameter-bound queries to its own database projection, retaining its original single owning read transaction and cursor decisions. Two six-file legacy commands/controls batches move complete queries, locks and savepoint operations to their owner adapter subdirectories, while application guards, clocks, callback order, JSON decode timing and math stay outside database. Six Games command/publication files also delegate complete queries to `adapters/games/`, retaining existing lease/generation decisions and legacy queue/feedback orchestration. Twenty-eight baseline files are extracted/relocated and one is cleared as non-database; the 127 remaining baseline paths are still pending; false positives require inspection rather than blind movement.

Other checklist families include legacy queue/review/attempt commands, Games/analysis projections, authored Studies/PGN operations, Content bridge orchestration and Learning runtime application paths. Close and verify a bounded owning slice at a time. Inline SQL/DDL is not accepted as a permanent exception, and moving a whole mixed module into `database/` will not by itself separate application policy.


Learning’s six-file deletion/miss/frozen-order and recovery/bootstrap/teaching batches preserve their original caller-owned transactions. Frozen preference computation lives in the Learning application `study/frozen_order/` directory; the other existing application modules remain composition seams. Eighteen Learning candidates and further legacy/domain coupling remain unfinished.


Content’s additional five existing source/exercise/binding/publication/coverage decisions now live in its application `policy/` folder. Worker dispatch, route traversal and publication orchestration still inside the database store are further extraction work beyond the original mixed-file checklist, not permanent exceptions.


Game Evidence’s root store additionally delegates import preparation, snapshot-cache choice and response shaping to its own application helpers. Snapshot staleness/affected-position computation, worker dispatch/retry, comparison computation/receipt/reconciliation orchestration and list-page validation remain unfinished extraction candidates inside that store. Persistence replay guards, lock/lease fences, atomic writes and cursor batches remain database operations.


Intelligence’s existing finding merge/ranking/proposal block now lives in its application `analysis/findings.py`. Request/cache/credential/retry decisions in `Store`, and admission/command/publication/report/export/engine transitions in `AnalysisStore`, remain identified extraction work inside their root adapters. No baseline candidate count changes for these additional moved-adapter internals.


Game Evidence’s import-status response tail and comparison report dictionary have also moved to its application `reporting.py`; snapshot preparation/staleness, worker dispatch/retry and comparison computation/receipt/reconciliation orchestration still need separation at their original call points.


Learning’s durable tasks, essential maintenance and producer receiver persistence now join the earlier six application/storage splits. Fifteen Learning baseline candidates remain, including admission, gateways, publication/review/queue composition and their current helpers; no full-runtime isolation claim is implied.
