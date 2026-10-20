# Database inventory and scope

[Root index](README.md). Dedicated DDL, connection managers, concrete persistence adapters, SQL-only helpers, PostgreSQL maintenance/proof scripts, IndexedDB connection/record stores and backup adapters were moved into this single tree. Owner public APIs continue to wire their own implementations; no component imports another database package's private adapters as a new dependency.

The following **156 mixed application/SQL modules remain to be separated**. Complete centralization is unfinished while their database operations live outside the root `database/` tree. This is a starting checklist, not an accepted permanent exception. Extract application/domain logic into logically named folders outside `database/`, keep each database operation inside it, and connect the single implementations through explicit interfaces and outer composition. Application-facing tests and public wire JSON schemas retain their owners. No table is declared unused from its name or lack of a text match.

Mixed source files found by a conservative database-call/IndexedDB text scan: **156**. False positives (SQL examples/comments) are possible; this is a navigation inventory, not dependency proof.

<details>
<summary>Paths for follow-up extraction review</summary>

- [app/lib/study-connection.ts](../app/lib/study-connection.ts)
- [backend/app/account_commands.py](../backend/app/account_commands.py)
- [backend/app/activity_commands.py](../backend/app/activity_commands.py)
- [backend/app/annotation_commands.py](../backend/app/annotation_commands.py)
- [backend/app/branch_commands.py](../backend/app/branch_commands.py)
- [backend/app/canonical_prefix_api.py](../backend/app/canonical_prefix_api.py)
- [backend/app/card_commands.py](../backend/app/card_commands.py)
- [backend/app/card_deletion.py](../backend/app/card_deletion.py)
- [backend/app/command_gateway.py](../backend/app/command_gateway.py)
- [backend/app/coverage_maia_commands.py](../backend/app/coverage_maia_commands.py)
- [backend/app/endgame_commands.py](../backend/app/endgame_commands.py)
- [backend/app/finding_card_commands.py](../backend/app/finding_card_commands.py)
- [backend/app/finding_commands.py](../backend/app/finding_commands.py)
- [backend/app/game_analysis_commands.py](../backend/app/game_analysis_commands.py)
- [backend/app/game_analysis_publication.py](../backend/app/game_analysis_publication.py)
- [backend/app/game_commands.py](../backend/app/game_commands.py)
- [backend/app/game_evidence_commands.py](../backend/app/game_evidence_commands.py)
- [backend/app/game_sync_commands.py](../backend/app/game_sync_commands.py)
- [backend/app/guided_review_commands.py](../backend/app/guided_review_commands.py)
- [backend/app/integrity_repair_commands.py](../backend/app/integrity_repair_commands.py)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/opening_evidence_api.py](../backend/app/opening_evidence_api.py)
- [backend/app/opening_segmentation_api.py](../backend/app/opening_segmentation_api.py)
- [backend/app/opportunity_commands.py](../backend/app/opportunity_commands.py)
- [backend/app/pgn_import_commands.py](../backend/app/pgn_import_commands.py)
- [backend/app/prefix_diagnostics_api.py](../backend/app/prefix_diagnostics_api.py)
- [backend/app/prefix_evaluation_api.py](../backend/app/prefix_evaluation_api.py)
- [backend/app/prefix_split_commands.py](../backend/app/prefix_split_commands.py)
- [backend/app/prefix_transition_api.py](../backend/app/prefix_transition_api.py)
- [backend/app/queue_attempt_origins.py](../backend/app/queue_attempt_origins.py)
- [backend/app/queue_commands.py](../backend/app/queue_commands.py)
- [backend/app/repertoire_commands.py](../backend/app/repertoire_commands.py)
- [backend/app/repertoire_settings.py](../backend/app/repertoire_settings.py)
- [backend/app/review_commands.py](../backend/app/review_commands.py)
- [backend/app/services/analysis_paste.py](../backend/app/services/analysis_paste.py)
- [backend/app/services/background_activity.py](../backend/app/services/background_activity.py)
- [backend/app/services/background_diagnostics.py](../backend/app/services/background_diagnostics.py)
- [backend/app/services/background_metrics.py](../backend/app/services/background_metrics.py)
- [backend/app/services/canonical_prefix.py](../backend/app/services/canonical_prefix.py)
- [backend/app/services/canonical_prefix_preview.py](../backend/app/services/canonical_prefix_preview.py)
- [backend/app/services/canonical_scope_freshness.py](../backend/app/services/canonical_scope_freshness.py)
- [backend/app/services/defensive_analysis.py](../backend/app/services/defensive_analysis.py)
- [backend/app/services/discovery_admission.py](../backend/app/services/discovery_admission.py)
- [backend/app/services/durable_scheduling.py](../backend/app/services/durable_scheduling.py)
- [backend/app/services/durable_tasks.py](../backend/app/services/durable_tasks.py)
- [backend/app/services/engine_scheduling.py](../backend/app/services/engine_scheduling.py)
- [backend/app/services/frozen_opening_order.py](../backend/app/services/frozen_opening_order.py)
- [backend/app/services/game_analysis_worker.py](../backend/app/services/game_analysis_worker.py)
- [backend/app/services/game_findings.py](../backend/app/services/game_findings.py)
- [backend/app/services/game_sync.py](../backend/app/services/game_sync.py)
- [backend/app/services/game_sync_coordinator.py](../backend/app/services/game_sync_coordinator.py)
- [backend/app/services/gameplay_events.py](../backend/app/services/gameplay_events.py)
- [backend/app/services/guided_review.py](../backend/app/services/guided_review.py)
- [backend/app/services/introduction_priorities.py](../backend/app/services/introduction_priorities.py)
- [backend/app/services/learning_review_adapter.py](../backend/app/services/learning_review_adapter.py)
- [backend/app/services/legacy_game_authority.py](../backend/app/services/legacy_game_authority.py)
- [backend/app/services/legacy_study_remediation.py](../backend/app/services/legacy_study_remediation.py)
- [backend/app/services/opening_capacity_admission.py](../backend/app/services/opening_capacity_admission.py)
- [backend/app/services/opening_graph.py](../backend/app/services/opening_graph.py)
- [backend/app/services/opening_graph_recovery.py](../backend/app/services/opening_graph_recovery.py)
- [backend/app/services/opening_progression.py](../backend/app/services/opening_progression.py)
- [backend/app/services/opening_ranking_snapshot.py](../backend/app/services/opening_ranking_snapshot.py)
- [backend/app/services/postgres_coverage_candidates.py](../backend/app/services/postgres_coverage_candidates.py)
- [backend/app/services/postgres_coverage_explorer.py](../backend/app/services/postgres_coverage_explorer.py)
- [backend/app/services/postgres_coverage_recovery.py](../backend/app/services/postgres_coverage_recovery.py)
- [backend/app/services/postgres_coverage_seed.py](../backend/app/services/postgres_coverage_seed.py)
- [backend/app/services/postgres_daily_statistics.py](../backend/app/services/postgres_daily_statistics.py)
- [backend/app/services/postgres_game_derivation.py](../backend/app/services/postgres_game_derivation.py)
- [backend/app/services/postgres_game_events.py](../backend/app/services/postgres_game_events.py)
- [backend/app/services/postgres_game_features.py](../backend/app/services/postgres_game_features.py)
- [backend/app/services/postgres_game_findings.py](../backend/app/services/postgres_game_findings.py)
- [backend/app/services/postgres_game_misses.py](../backend/app/services/postgres_game_misses.py)
- [backend/app/services/postgres_game_priorities.py](../backend/app/services/postgres_game_priorities.py)
- [backend/app/services/postgres_game_repertoire.py](../backend/app/services/postgres_game_repertoire.py)
- [backend/app/services/postgres_game_sync.py](../backend/app/services/postgres_game_sync.py)
- [backend/app/services/postgres_game_sync_completion.py](../backend/app/services/postgres_game_sync_completion.py)
- [backend/app/services/postgres_game_sync_windows.py](../backend/app/services/postgres_game_sync_windows.py)
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
- [backend/app/services/refresh_requests.py](../backend/app/services/refresh_requests.py)
- [backend/app/services/repertoire_comparison.py](../backend/app/services/repertoire_comparison.py)
- [backend/app/services/repertoire_conflicts.py](../backend/app/services/repertoire_conflicts.py)
- [backend/app/services/repertoire_coverage.py](../backend/app/services/repertoire_coverage.py)
- [backend/app/services/repertoire_game_refresh.py](../backend/app/services/repertoire_game_refresh.py)
- [backend/app/services/repertoire_integrity.py](../backend/app/services/repertoire_integrity.py)
- [backend/app/services/repertoire_move_verification.py](../backend/app/services/repertoire_move_verification.py)
- [backend/app/services/repertoire_opportunities.py](../backend/app/services/repertoire_opportunities.py)
- [backend/app/services/repertoire_statistics.py](../backend/app/services/repertoire_statistics.py)
- [backend/app/services/review_service.py](../backend/app/services/review_service.py)
- [backend/app/services/statistics.py](../backend/app/services/statistics.py)
- [backend/app/services/study_attempts.py](../backend/app/services/study_attempts.py)
- [backend/app/services/study_mode.py](../backend/app/services/study_mode.py)
- [backend/app/services/study_portable.py](../backend/app/services/study_portable.py)
- [backend/app/services/study_priority.py](../backend/app/services/study_priority.py)
- [backend/app/services/tactic_admission.py](../backend/app/services/tactic_admission.py)
- [backend/app/services/tactic_capture.py](../backend/app/services/tactic_capture.py)
- [backend/app/services/tactical_catalog.py](../backend/app/services/tactical_catalog.py)
- [backend/app/services/tactical_opportunities.py](../backend/app/services/tactical_opportunities.py)
- [backend/app/services/threat_pipeline.py](../backend/app/services/threat_pipeline.py)
- [backend/app/services/threat_training.py](../backend/app/services/threat_training.py)
- [backend/app/settings_commands.py](../backend/app/settings_commands.py)
- [backend/app/snapshot_reads.py](../backend/app/snapshot_reads.py)
- [backend/app/statistics_commands.py](../backend/app/statistics_commands.py)
- [backend/app/study_attempt_commands.py](../backend/app/study_attempt_commands.py)
- [backend/app/study_commands.py](../backend/app/study_commands.py)
- [backend/app/study_routes.py](../backend/app/study_routes.py)
- [backend/app/tactic_commands.py](../backend/app/tactic_commands.py)
- [backend/app/tasks.py](../backend/app/tasks.py)
- [backend/app/teaching_commands.py](../backend/app/teaching_commands.py)
- [backend/app/threat_analysis_commands.py](../backend/app/threat_analysis_commands.py)
- [services/content/src/tempo_content/bridge.py](../services/content/src/tempo_content/bridge.py)
- [services/content/src/tempo_content/cli.py](../services/content/src/tempo_content/cli.py)
- [services/game-evidence/src/tempo_game_evidence/analysis_export.py](../services/game-evidence/src/tempo_game_evidence/analysis_export.py)
- [services/game-evidence/src/tempo_game_evidence/api.py](../services/game-evidence/src/tempo_game_evidence/api.py)
- [services/game-evidence/src/tempo_game_evidence/delivery.py](../services/game-evidence/src/tempo_game_evidence/delivery.py)
- [services/game-evidence/src/tempo_game_evidence/providers.py](../services/game-evidence/src/tempo_game_evidence/providers.py)
- [services/game-evidence/src/tempo_game_evidence/reconciliation.py](../services/game-evidence/src/tempo_game_evidence/reconciliation.py)
- [services/intelligence/src/tempo_intelligence/analysis/games_reader.py](../services/intelligence/src/tempo_intelligence/analysis/games_reader.py)
- [services/intelligence/src/tempo_intelligence/analysis/worker.py](../services/intelligence/src/tempo_intelligence/analysis/worker.py)
- [services/learning/runtime/src/tempo_learning_runtime/admission.py](../services/learning/runtime/src/tempo_learning_runtime/admission.py)
- [services/learning/runtime/src/tempo_learning_runtime/api.py](../services/learning/runtime/src/tempo_learning_runtime/api.py)
- [services/learning/runtime/src/tempo_learning_runtime/card_deletion.py](../services/learning/runtime/src/tempo_learning_runtime/card_deletion.py)
- [services/learning/runtime/src/tempo_learning_runtime/defense.py](../services/learning/runtime/src/tempo_learning_runtime/defense.py)
- [services/learning/runtime/src/tempo_learning_runtime/durable_tasks.py](../services/learning/runtime/src/tempo_learning_runtime/durable_tasks.py)
- [services/learning/runtime/src/tempo_learning_runtime/frozen_opening_order.py](../services/learning/runtime/src/tempo_learning_runtime/frozen_opening_order.py)
- [services/learning/runtime/src/tempo_learning_runtime/gateway.py](../services/learning/runtime/src/tempo_learning_runtime/gateway.py)
- [services/learning/runtime/src/tempo_learning_runtime/maintenance.py](../services/learning/runtime/src/tempo_learning_runtime/maintenance.py)
- [services/learning/runtime/src/tempo_learning_runtime/opening_progression.py](../services/learning/runtime/src/tempo_learning_runtime/opening_progression.py)
- [services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py](../services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py)
- [services/learning/runtime/src/tempo_learning_runtime/publications.py](../services/learning/runtime/src/tempo_learning_runtime/publications.py)
- [services/learning/runtime/src/tempo_learning_runtime/queue_application.py](../services/learning/runtime/src/tempo_learning_runtime/queue_application.py)
- [services/learning/runtime/src/tempo_learning_runtime/queue_attempt_origins.py](../services/learning/runtime/src/tempo_learning_runtime/queue_attempt_origins.py)
- [services/learning/runtime/src/tempo_learning_runtime/queue_commands.py](../services/learning/runtime/src/tempo_learning_runtime/queue_commands.py)
- [services/learning/runtime/src/tempo_learning_runtime/real_game_feedback.py](../services/learning/runtime/src/tempo_learning_runtime/real_game_feedback.py)
- [services/learning/runtime/src/tempo_learning_runtime/receivers.py](../services/learning/runtime/src/tempo_learning_runtime/receivers.py)
- [services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py](../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_adapter.py](../services/learning/runtime/src/tempo_learning_runtime/review_adapter.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_reconciliation.py](../services/learning/runtime/src/tempo_learning_runtime/review_reconciliation.py)
- [services/learning/runtime/src/tempo_learning_runtime/review_service.py](../services/learning/runtime/src/tempo_learning_runtime/review_service.py)
- [services/learning/runtime/src/tempo_learning_runtime/study_attempt_commands.py](../services/learning/runtime/src/tempo_learning_runtime/study_attempt_commands.py)
- [services/learning/runtime/src/tempo_learning_runtime/study_attempts.py](../services/learning/runtime/src/tempo_learning_runtime/study_attempts.py)
- [services/learning/runtime/src/tempo_learning_runtime/study_bootstrap.py](../services/learning/runtime/src/tempo_learning_runtime/study_bootstrap.py)
- [services/learning/runtime/src/tempo_learning_runtime/tactic_admission.py](../services/learning/runtime/src/tempo_learning_runtime/tactic_admission.py)
- [services/learning/runtime/src/tempo_learning_runtime/tactic_commands.py](../services/learning/runtime/src/tempo_learning_runtime/tactic_commands.py)
- [services/learning/runtime/src/tempo_learning_runtime/teaching_commands.py](../services/learning/runtime/src/tempo_learning_runtime/teaching_commands.py)

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

The first small extraction is `backend/app/services/frozen_opening_order.py`: move its six-entry SQLite `SCHEMA` list (five CREATE TABLE statements plus the activation seed) into `database/tempo_legacy_database/ddl/frozen_opening_schema.py`; point the existing SQLite initializer and test fixture directly at that owner. Preserve every statement and order. This removes the reverse dependency where `database/tempo_legacy_database/connections.py` imports DDL from application code. It changes no schema, initialization timing or transaction authority.

Then separate frozen-order persistence/read projections from preference/eligibility decisions. Reuse the existing borrowed transaction and public fact/command interfaces; keep ordering/fingerprint decisions outside `database/` in the Study logical folder and supply storage capabilities through outer composition. Do not introduce a generic `execute(sql)` port, duplicate old/new implementations or present a compatibility facade as a finished strict domain boundary. Queue presentation/issuance and rollback operations need their original atomicity and lock order retained. Update the SQLite source-schema README separately from the PostgreSQL charts; the two dialects are not assumed identical.

Other checklist families include legacy queue/review/attempt commands, Games/analysis projections, authored Studies/PGN operations, Content bridge orchestration and Learning runtime application paths. Close and verify a bounded owning slice at a time. Inline SQL/DDL is not accepted as a permanent exception, and moving a whole mixed module into `database/` will not by itself separate application policy.
