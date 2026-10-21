# Database inventory and scope

[Root index](README.md). Dedicated DDL, connection managers, concrete persistence adapters, SQL-only helpers, PostgreSQL maintenance/proof scripts, IndexedDB connection/record stores and backup adapters were moved into this single tree. Owner public APIs continue to wire their own implementations; no component imports another database package's private adapters as a new dependency.

The original PR334 checklist contains **156 mixed application/SQL candidates**. Completed batches cover **127 files**; one candidate is confirmed non-database application code, and **28 remain for review/extraction**. Complete centralization is unfinished while database operations remain outside the root tree or application policy remains inside it. This is a working checklist, not a permanent exception. Preserve one implementation and the owning component's interfaces, packaging and transaction authority. Application-facing tests and wire schemas retain their owners. No table is declared unused from its name or lack of a text match.

Baseline mixed source files found by a conservative database-call/IndexedDB text scan: **156**. False positives (SQL examples/comments) are possible; this is a navigation inventory, not dependency proof.

<details>
<summary>Paths for follow-up extraction review</summary>

- Completed browser storage split: [app/lib/study-connection.ts](../app/lib/study-connection.ts) retains application connection/authority/transport decisions; [database/browser/study-connection-storage.ts](browser/study-connection-storage.ts) owns the existing IndexedDB inspection and Web Storage operations.
- Completed commands SQL split: [backend/app/account_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/activity_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/annotation_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed authored-repertoire command storage split: [backend/app/branch_commands.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- Completed authored-repertoire command storage split: [backend/app/canonical_prefix_api.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- [backend/app/card_commands.py](../backend/app/card_commands.py)
- [backend/app/card_deletion.py](../backend/app/card_deletion.py)
- [backend/app/command_gateway.py](../backend/app/command_gateway.py)
- Completed legacy coverage Maia command storage split: [backend/app/coverage_maia_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/endgame_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed legacy Games command SQL split: [backend/app/finding_card_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/finding_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_analysis_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_analysis_publication.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy Games command SQL split: [backend/app/game_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed legacy authority/evidence storage split: [backend/app/game_evidence_commands.py](tempo_legacy_database/adapters/games/authority/README.md).
- Completed legacy Games command SQL split: [backend/app/game_sync_commands.py](tempo_legacy_database/adapters/games/README.md).
- Completed guided game-review storage split: [backend/app/guided_review_commands.py](tempo_legacy_database/adapters/games/guided_review/README.md).
- Completed authored discovery/integrity command storage split: [backend/app/integrity_repair_commands.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- [backend/app/main.py](../backend/app/main.py)
- Completed controls SQL split: [backend/app/opening_evidence_api.py](tempo_legacy_database/adapters/controls/README.md).
- Completed existing opening advisory API storage split: [backend/app/opening_segmentation_api.py](tempo_legacy_database/adapters/authored_repertoires/opening_advisory/README.md).
- Completed authored discovery/integrity command storage split: [backend/app/opportunity_commands.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- [backend/app/pgn_import_commands.py](../backend/app/pgn_import_commands.py)
- Completed legacy bounded prefix-read projection split: [backend/app/prefix_diagnostics_api.py](tempo_legacy_database/adapters/prefix_reads/README.md).
- Completed existing opening advisory API storage split: [backend/app/prefix_evaluation_api.py](tempo_legacy_database/adapters/authored_repertoires/opening_advisory/README.md).
- Completed foreground control storage split: [backend/app/prefix_split_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed legacy bounded prefix-read projection split: [backend/app/prefix_transition_api.py](tempo_legacy_database/adapters/prefix_reads/README.md).
- Completed queue/lifecycle storage split: [backend/app/queue_attempt_origins.py](tempo_legacy_database/adapters/lifecycle/README.md).
- [backend/app/queue_commands.py](../backend/app/queue_commands.py)
- Completed foreground control storage split: [backend/app/repertoire_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed controls SQL split: [backend/app/repertoire_settings.py](tempo_legacy_database/adapters/controls/README.md).
- Completed controls SQL split: [backend/app/review_commands.py](tempo_legacy_database/adapters/controls/README.md).
- Completed pasted-analysis storage split: [backend/app/services/analysis_paste.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- [backend/app/services/background_activity.py](../backend/app/services/background_activity.py)
- [backend/app/services/background_diagnostics.py](../backend/app/services/background_diagnostics.py)
- Completed queue/lifecycle storage split: [backend/app/services/background_metrics.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed existing canonical-prefix implementation storage split: [backend/app/services/canonical_prefix.py](tempo_legacy_database/adapters/authored_repertoires/canonical_prefix/README.md).
- Completed existing canonical-prefix implementation storage split: [backend/app/services/canonical_prefix_preview.py](tempo_legacy_database/adapters/authored_repertoires/canonical_prefix/README.md).
- Completed repertoire-scope storage split: [backend/app/services/canonical_scope_freshness.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- Completed defensive settings/predicate storage split: [backend/app/services/defensive_analysis.py](tempo_legacy_database/adapters/defensive_analysis.py).
- [backend/app/services/discovery_admission.py](../backend/app/services/discovery_admission.py)
- Completed legacy scheduling storage split: [backend/app/services/durable_scheduling.py](tempo_legacy_database/adapters/scheduling/README.md).
- [backend/app/services/durable_tasks.py](../backend/app/services/durable_tasks.py)
- Completed legacy scheduling storage split: [backend/app/services/engine_scheduling.py](tempo_legacy_database/adapters/scheduling/README.md).
- Completed SQL/DDL split: [backend/app/services/frozen_opening_order.py](../backend/app/services/frozen_opening_order.py) now only binds [Study policy](../backend/app/study/frozen_order/README.md) and [database storage](tempo_legacy_database/adapters/frozen_order.py). Legacy caller composition migration remains.
- Completed legacy analysis/finding storage split: [backend/app/services/game_analysis_worker.py](tempo_legacy_database/adapters/games/analysis_worker/README.md).
- Completed legacy analysis/finding storage split: [backend/app/services/game_findings.py](tempo_legacy_database/adapters/games/finding_projections/README.md).
- Completed legacy Games imports storage split: [backend/app/services/game_sync.py](tempo_legacy_database/adapters/games/imports/README.md).
- Completed legacy Games coordinator storage split: [backend/app/services/game_sync_coordinator.py](tempo_legacy_database/adapters/games/coordinator/README.md).
- Completed legacy Games evidence storage split: [backend/app/services/gameplay_events.py](tempo_legacy_database/adapters/games/evidence/README.md).
- Completed guided game-review storage split: [backend/app/services/guided_review.py](tempo_legacy_database/adapters/games/guided_review/README.md).
- Completed legacy introduction-priority storage split: [backend/app/services/introduction_priorities.py](tempo_legacy_database/adapters/introduction_priorities/README.md).
- [backend/app/services/learning_review_adapter.py](../backend/app/services/learning_review_adapter.py)
- Completed legacy authority/evidence storage split: [backend/app/services/legacy_game_authority.py](tempo_legacy_database/adapters/games/authority/README.md).
- [backend/app/services/legacy_study_remediation.py](../backend/app/services/legacy_study_remediation.py)
- [backend/app/services/opening_capacity_admission.py](../backend/app/services/opening_capacity_admission.py)
- Completed existing compatibility graph storage split: [backend/app/services/opening_graph.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed existing legacy opening graph recovery storage split: [backend/app/services/opening_graph_recovery.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/opening_progression.py](tempo_legacy_database/adapters/opening_progression.py).
- [backend/app/services/opening_ranking_snapshot.py](../backend/app/services/opening_ranking_snapshot.py)
- Completed controls SQL split: [backend/app/services/postgres_coverage_candidates.py](tempo_legacy_database/adapters/controls/README.md).
- Completed coverage build storage split: [backend/app/services/postgres_coverage_explorer.py](tempo_legacy_database/adapters/coverage/README.md).
- Completed controls SQL split: [backend/app/services/postgres_coverage_recovery.py](tempo_legacy_database/adapters/controls/README.md).
- Completed coverage build storage split: [backend/app/services/postgres_coverage_seed.py](tempo_legacy_database/adapters/coverage/README.md).
- Completed legacy derived-publication storage split: [backend/app/services/postgres_daily_statistics.py](tempo_legacy_database/adapters/derived_publications/README.md).
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
- Completed legacy derived-publication storage split: [backend/app/services/postgres_next_opponent.py](tempo_legacy_database/adapters/derived_publications/README.md).
- Completed legacy opening-evidence storage split: [backend/app/services/postgres_opening_evidence.py](tempo_legacy_database/adapters/opening_evidence/README.md).
- Completed existing legacy opening graph worker storage split: [backend/app/services/postgres_opening_graph.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed existing opening mutation storage split: [backend/app/services/postgres_opening_segmentation.py](tempo_legacy_database/adapters/authored_repertoires/opening_mutations/README.md).
- Completed legacy priority lifecycle storage split: [backend/app/services/postgres_priority.py](tempo_legacy_database/adapters/priority_lifecycle/README.md).
- [backend/app/services/postgres_queue_refresh.py](../backend/app/services/postgres_queue_refresh.py)
- Completed existing opening mutation storage split: [backend/app/services/prefix_split.py](tempo_legacy_database/adapters/authored_repertoires/opening_mutations/README.md).
- [backend/app/services/prefix_transition_application.py](../backend/app/services/prefix_transition_application.py)
- Completed legacy priority lifecycle storage split: [backend/app/services/priority_retention.py](tempo_legacy_database/adapters/priority_lifecycle/README.md).
- [backend/app/services/queue_admission_adapter.py](../backend/app/services/queue_admission_adapter.py)
- [backend/app/services/queue_application.py](../backend/app/services/queue_application.py)
- Completed Redis admission/policy storage split: [backend/app/services/redis_admission_gate.py](tempo_legacy_database/adapters/redis_control/README.md).
- Completed queue/lifecycle storage split: [backend/app/services/refresh_requests.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed legacy read/publication projection split: [backend/app/services/repertoire_comparison.py](tempo_legacy_database/adapters/projections/README.md).
- Completed authored-Studies policy / database line-projection split: [backend/app/services/repertoire_conflicts.py](../backend/app/studies/README.md).
- [backend/app/services/repertoire_coverage.py](../backend/app/services/repertoire_coverage.py)
- Completed repertoire-scope storage split: [backend/app/services/repertoire_game_refresh.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- [backend/app/services/repertoire_integrity.py](../backend/app/services/repertoire_integrity.py)
- Completed repertoire-scope storage split: [backend/app/services/repertoire_move_verification.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- [backend/app/services/repertoire_opportunities.py](../backend/app/services/repertoire_opportunities.py)
- Completed legacy read/publication projection split: [backend/app/services/repertoire_statistics.py](tempo_legacy_database/adapters/projections/README.md).
- [backend/app/services/review_service.py](../backend/app/services/review_service.py)
- Completed legacy statistics storage split: [backend/app/services/statistics.py](tempo_legacy_database/adapters/statistics/README.md).
- Completed legacy exercise-attempt storage split: [backend/app/services/study_attempts.py](tempo_legacy_database/adapters/study_attempts/README.md).
- Completed controls SQL split: [backend/app/services/study_mode.py](tempo_legacy_database/adapters/controls/README.md).
- [backend/app/services/study_portable.py](../backend/app/services/study_portable.py)
- Completed Redis admission/policy storage split: [backend/app/services/study_priority.py](tempo_legacy_database/adapters/redis_control/README.md).
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/tactic_admission.py](tempo_legacy_database/adapters/tactic_admission.py).
- Completed legacy tactic persistence split: [backend/app/services/tactic_capture.py](tempo_legacy_database/adapters/tactics/README.md).
- Completed legacy tactic persistence split: [backend/app/services/tactical_catalog.py](tempo_legacy_database/adapters/tactics/README.md).
- Completed legacy read/publication projection split: [backend/app/services/tactical_opportunities.py](tempo_legacy_database/adapters/projections/README.md).
- [backend/app/services/threat_pipeline.py](../backend/app/services/threat_pipeline.py)
- [backend/app/services/threat_training.py](../backend/app/services/threat_training.py)
- Completed foreground control storage split: [backend/app/settings_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed intact database snapshot/evidence reader move: [backend/app/snapshot_reads.py](tempo_legacy_database/adapters/snapshot_reads.py).
- Completed commands SQL split: [backend/app/statistics_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed legacy exercise-attempt storage split: [backend/app/study_attempt_commands.py](tempo_legacy_database/adapters/study_attempts/README.md).
- [backend/app/study_commands.py](../backend/app/study_commands.py)
- [backend/app/study_routes.py](../backend/app/study_routes.py)
- Completed legacy tactic persistence split: [backend/app/tactic_commands.py](tempo_legacy_database/adapters/tactics/README.md).
- Completed legacy daily rollover readiness storage split: [backend/app/tasks.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed commands SQL split: [backend/app/teaching_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed legacy defensive-analysis command storage split: [backend/app/threat_analysis_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed Content bridge split: [application/public composition](../services/content/src/tempo_content/bridge/README.md) and [database operations](tempo_content_database/adapters/bridge.py).
- Completed Content CLI readiness split: [cli.py](../services/content/src/tempo_content/cli.py) retains parsing/commands; [readiness connection](tempo_content_database/ddl/migrate.py) stays with DDL.
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/analysis_export.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/api.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/delivery.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/providers.py](tempo_game_evidence_database/adapters/README.md).
- Completed Game Evidence SQL split: [services/game-evidence/src/tempo_game_evidence/reconciliation.py](tempo_game_evidence_database/adapters/README.md).
- Completed Intelligence read-projection split: [games_reader.py](../services/intelligence/src/tempo_intelligence/analysis/games_reader.py) retains cursor/limit/envelope/read-transaction composition; [complete owner queries](tempo_intelligence_database/adapters/games_read_projection.py) live in database.
- Reviewed non-database false positive: [Intelligence analysis worker](../services/intelligence/src/tempo_intelligence/analysis/worker.py). Its only `execute` call is `client.execute(claim["job"])`, an engine-client operation; the file has no SQL/driver/cursor operations. Keep this application dispatcher with Analysis.
- Completed Learning foreground admission/review storage split: [services/learning/runtime/src/tempo_learning_runtime/admission.py](tempo_learning_database/adapters/admission-review/README.md).
- Completed existing Learning API/defense storage split: [services/learning/runtime/src/tempo_learning_runtime/api.py](tempo_learning_database/adapters/api-defense/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/card_deletion.py](tempo_learning_database/adapters/README.md).
- Completed existing Learning API/defense storage split: [services/learning/runtime/src/tempo_learning_runtime/defense.py](tempo_learning_database/adapters/api-defense/README.md).
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/durable_tasks.py](tempo_learning_database/adapters/essential-work/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/frozen_opening_order.py](tempo_learning_database/adapters/README.md).
- Completed Learning gateway/remediation storage split: [services/learning/runtime/src/tempo_learning_runtime/gateway.py](tempo_learning_database/adapters/gateway-remediation/README.md).
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/maintenance.py](tempo_learning_database/adapters/essential-work/README.md).
- Completed intact Learning-owned SQL-only helper move: [database/tempo_learning_database/adapters/opening_progression.py](tempo_learning_database/adapters/opening_progression.py).
- Completed existing Learning publication execution storage split: [services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py](tempo_learning_database/adapters/publication-execution/README.md).
- Completed existing Learning publication execution storage split: [services/learning/runtime/src/tempo_learning_runtime/publications.py](tempo_learning_database/adapters/publication-execution/README.md).
- Completed Learning queue storage split: [services/learning/runtime/src/tempo_learning_runtime/queue_application.py](tempo_learning_database/adapters/queue/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/queue_attempt_origins.py](tempo_learning_database/adapters/README.md).
- Completed Learning queue storage split: [services/learning/runtime/src/tempo_learning_runtime/queue_commands.py](tempo_learning_database/adapters/queue/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/real_game_feedback.py](tempo_learning_database/adapters/README.md).
- Completed Learning essential-work storage split: [services/learning/runtime/src/tempo_learning_runtime/receivers.py](tempo_learning_database/adapters/essential-work/README.md).
- Completed Learning gateway/remediation storage split: [services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py](tempo_learning_database/adapters/gateway-remediation/README.md).
- Completed Learning foreground admission/review storage split: [services/learning/runtime/src/tempo_learning_runtime/review_adapter.py](tempo_learning_database/adapters/admission-review/README.md).
- Completed Learning review storage split: [services/learning/runtime/src/tempo_learning_runtime/review_reconciliation.py](tempo_learning_database/adapters/reviews/README.md).
- Completed Learning review storage split: [services/learning/runtime/src/tempo_learning_runtime/review_service.py](tempo_learning_database/adapters/reviews/README.md).
- Completed Learning practice-attempt storage split: [services/learning/runtime/src/tempo_learning_runtime/study_attempt_commands.py](tempo_learning_database/adapters/practice-attempts/README.md).
- Completed Learning practice-attempt storage split: [services/learning/runtime/src/tempo_learning_runtime/study_attempts.py](tempo_learning_database/adapters/practice-attempts/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/study_bootstrap.py](tempo_learning_database/adapters/README.md).
- Completed intact Learning-owned SQL-only helper move: [database/tempo_learning_database/adapters/tactic_admission.py](tempo_learning_database/adapters/tactic_admission.py).
- Completed Learning practice-attempt storage split: [services/learning/runtime/src/tempo_learning_runtime/tactic_commands.py](tempo_learning_database/adapters/practice-attempts/README.md).
- Completed Learning storage/application split: [services/learning/runtime/src/tempo_learning_runtime/teaching_commands.py](tempo_learning_database/adapters/README.md).

</details>

## Additional storage candidates found after the baseline

The 156-path baseline is a conservative SQL/IndexedDB scan, not an exhaustive proof of storage placement. Nine additional storage/tooling sources were found by checking Redis operations, SQL constants and database-specific native harnesses outside the original call scan. They do not alter baseline arithmetic; their status is shown below:

| Additional source / status | Database operations | Application responsibility to retain outside database |
| --- | --- | --- |
| Extracted [background_wakes.py](../backend/app/services/background_wakes.py) | Cached factory and four exact Lua operations now live in [wake storage](tempo_legacy_database/adapters/redis_control/wake_runtime/README.md). | Existing wake lifecycle, identity, callback and queue orchestration. |
| Extracted [background_runtime.py](../backend/app/services/background_runtime.py) | Short-timeout factory, worker-slot SET/15-second TTL and bounded sixteen-slot MGET now live in the same [storage category](tempo_legacy_database/adapters/redis_control/wake_runtime/README.md). | Runtime snapshot/lease timing and presentation; its ContextVar `.set` calls are not Redis. |
| Extracted [legacy_game_observation.py](../backend/app/legacy_game_observation.py) | Accepted-head and imported-game SELECTs now live in the same [authority storage](tempo_legacy_database/adapters/games/authority/README.md), retaining the existing reader. | One-game capture scope, legal decision plies and export authority/format. |
| Extracted [legacy_game_capture.py](../backend/app/services/legacy_game_capture.py) | Existing metadata and finite stream/count/high-water/page operations now live in [authority storage](tempo_legacy_database/adapters/games/authority/README.md). | Capture budget/digest/selection and finite traversal; extract with its authority/observation callers. |
| Relocated [writer_latency.py](tests/benchmarks/writer_latency.py) | Disposable SQLite connection/schema/inserts and writer contention tooling. | Existing benchmark reporting; whole dedicated benchmark may relocate intact. |
| Extracted [qualify_legacy_bridge.py](../scripts/qualify_legacy_bridge.py) fixture operations | Five existing native setup statements now live in [ownership fixture.py](ownership/fixture.py), including the two unchanged test-table DDL definitions. | Qualification allocation/orchestration stays with its current tooling owner; move only DB fixture operations, with no native execution. |

Three Content native-only files discovered after the runtime audit are also relocated intact: [the PostgreSQL suite](tempo_content_database/tests/test_postgres.py), [its runner](tempo_content_database/tooling/run_postgres.py) and [benchmark](tempo_content_database/tooling/benchmark.py). Native assertions and benchmark are byte-identical; only runner/build/discovery/source-hash paths change. Application-facing synthetic tests and source-free consumer tooling retain Content ownership. These three additional relocations do not alter the original156-row arithmetic.

The original `redis_admission_gate.py` and `study_priority.py` entries are also real control persistence, not false positives. Their Redis factories/Lua/pipeline/operations and Study-settings query now live under [redis_control](tempo_legacy_database/adapters/redis_control/README.md), preserving private namespace/client overrides and local failure/timing behavior. No actual Redis values or live services were inspected.


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

PR334 owns the dedicated-file moves, source-schema charts and discovery repairs. Dependent owner batches separate complete database operations from existing application decisions. The original workload was 155 Python files (46,247 lines), 2,574 database-call sites across 828 functions, plus one browser file. These are static baseline counts, not a claim that every function is SQL-only or that its transaction may be split.

| Original checklist owner | Extracted / relocated | Confirmed false positives | Pending review / extraction |
| --- | ---: | ---: | ---: |
| Legacy backend | 92 | 0 | 28 |
| Learning runtime | 26 | 0 | 0 |
| Content | 2 | 0 | 0 |
| Game Evidence | 5 | 0 | 0 |
| Intelligence | 1 | 1 | 0 |
| Browser connection | 1 | 0 | 0 |
| **Total baseline** | **127** | **1** | **28** |

Additional sources above show their current extraction status; their pending operations and application computation inside originally moved adapters are further unfinished work. Zero pending baseline rows for a service does not mean its original database adapter is application-free.

### Application blocks removed from originally moved adapters

| Owner | Existing application home now outside database | Actual remaining work |
| --- | --- | --- |
| Content | [Source/exercise/binding/publication/coverage policy](../services/content/src/tempo_content/policy/README.md) [reconciliation/route/binding/exercise worker helpers](../services/content/src/tempo_content/worker/README.md) and [publication preparation](../services/content/src/tempo_content/publication/README.md). | [Source loading/staging orchestration](../services/content/src/tempo_content/source/README.md) is now outside database. The complete fifty-seven-method/four-helper store audit identifies no further independent mixed workflow; fixed guards/read projections/lifecycle/composition remain explicit. Nineteen production Content files contain no direct driver/cursor operations. The three database-specific native test/runner/benchmark files now reside in root owner [tests](tempo_content_database/tests/README.md) and [tooling](tempo_content_database/tooling/README.md); they are sdist-only, with no native execution. This is not a repository-wide completion claim. |
| Game Evidence | [Source identity/normalization](../services/game-evidence/src/tempo_game_evidence/source.py), [snapshot/cache/staleness preparation](../services/game-evidence/src/tempo_game_evidence/snapshot.py), [comparison preparation](../services/game-evidence/src/tempo_game_evidence/comparison.py), [reconciliation coordination](../services/game-evidence/src/tempo_game_evidence/reconciliation.py), and [response/list-input policy](../services/game-evidence/src/tempo_game_evidence/reporting.py). The [job worker](../services/game-evidence/src/tempo_game_evidence/worker.py) owns dispatch/failure control flow. | Final import metadata/player shaping is separated; the two named failure operations retain original fixed retry SQL literals and fence parameters. Source audit found no further pure application block in this owner store. SQL locks, replay conflicts, lease/resource limits, bounded atomic fanout and the unchanged compatibility cache holder stay explicit persistence/composition responsibilities; native execution remains unverified. |
| Intelligence | [Finding assembly](../services/intelligence/src/tempo_intelligence/analysis/README.md#finding-assembly), [serialization](../services/intelligence/src/tempo_intelligence/serialization.py) and [request descriptor/cache/retry helpers](../services/intelligence/src/tempo_intelligence/source_requests.py). | Request inspection, refresh/retry, [Maia profile/job/result/status](../services/intelligence/src/tempo_intelligence/maia_work.py) and evidence response orchestration now use fixed owner operations. Source submit/replay/cache-refresh and provider claim/publication application sequences now also live outside database, using fixed admission/provider operations. All four credential workflow source moves are now separated; the current Store audit identifies no further independent mixed workflow. Twelve existing Analysis read/workspace/export sequences now live in [read_work.py](../services/intelligence/src/tempo_intelligence/analysis/read_work.py), using fixed root reads. Manifest/page/finalize application sequences now live in [input_work.py](../services/intelligence/src/tempo_intelligence/analysis/input_work.py), using fixed input guards. The updated35-method AnalysisStore audit classifies three fixed guards, twenty composition methods and twelve remaining mixed methods. Run commands/receipts, derivation/publication and engine batches remain unfinished. No actual credential/provider action is part of these source moves. |
| Learning and legacy owner adapters | Frozen preference policy now lives in the [legacy Study folder](../backend/app/study/frozen_order/README.md) and [Learning Study folder](../services/learning/runtime/src/tempo_learning_runtime/study/frozen_order/README.md); authored-line conflict computation lives in [Studies](../backend/app/studies/README.md). | Learning has zero original baseline files pending. Its complete initial-adapter audit covers42 functions/methods across seven files; six files contain storage/driver/projection responsibilities. Existing opening-evidence still contains six pure application helpers, nine mixed functions and a DTO for extraction; compatibility wrappers remain inventoried. Legacy baseline work and the initially moved adapter policy audit remain. Do not assume that moving whole files established a boundary. |

The [HTTP composition storage README](tempo_legacy_database/adapters/http_composition/README.md) tracks the partial extraction of `backend/app/main.py`: system/settings/queue-maintenance, queue payload/head/prepared/entry and repertoire/annotation/teaching/snapshot/export projections PGN/branch authored-line operations and card revision/split/archive operations are centralized, while remaining inline operations keep that original checklist row pending. The fixed availability SQL helper shared with `queue_application.py` is centralized too; that file retains other pending operations. Partial slices do not increment completed-file arithmetic. The [queue refresh storage README](tempo_legacy_database/adapters/queue_refresh/README.md) tracks its partial admission extraction; `postgres_queue_refresh.py` remains pending until its remaining direct/bounded queries, SQL builders and existing reader bridges are separated.

All completed checklist rows above link their actual owner. Complete bound queries, driver/dialect handling, DDL, row locks, cursor batches and savepoints belong inside the one root database tree. Keep application decisions in logical owner folders and preserve their original evaluation/transaction/callback points. Existing legacy call-site composition/cross-feature coupling remains disclosed; no new sibling-domain dependency, generic SQL execution port, shared runtime, duplicate implementation or full-domain isolation claim is introduced.

The [validation record](VALIDATION.md) and category READMEs contain exact focused selections, source/SQL preservation evidence, artifact/resource discovery and native/qualification limits. No live data/schema or canonical-model implementation is part of this work. Continue one coherent owner slice at a time; inline database operations and mixed adapter policy are unfinished work, not permanent exceptions.
