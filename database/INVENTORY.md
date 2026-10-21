# Database inventory and scope

[Root index](README.md). Dedicated DDL, connection managers, concrete persistence adapters, SQL-only helpers, PostgreSQL maintenance/proof scripts, IndexedDB connection/record stores and backup adapters were moved into this single tree. Owner public APIs continue to wire their own implementations; no component imports another database package's private adapters as a new dependency.

The original PR334 checklist contains **156 mixed application/SQL candidates**. Completed batches cover **155 files**; one candidate is confirmed non-database application code, and **zero original entries remain for review/extraction**. The original checklist is closed, and the separately identified native harness/test/benchmark placement groups below are complete. Technical composition boundaries and any further source-audit findings remain unfinished work, not permanent exceptions or a claim of complete centralization. Preserve one implementation and the owning component's interfaces, packaging and transaction authority. Application-facing tests and wire schemas retain their owners. No table is declared unused from its name or lack of a text match.

Baseline mixed source files found by a conservative database-call/IndexedDB text scan: **156**. False positives (SQL examples/comments) are possible; this is a navigation inventory, not dependency proof.

<details>
<summary>Paths for follow-up extraction review</summary>

- Completed browser storage split: [app/lib/study-connection.ts](../app/lib/study-connection.ts) retains application connection/authority/transport decisions; [database/browser/study-connection-storage.ts](browser/study-connection-storage.ts) owns the existing IndexedDB inspection and Web Storage operations.
- Completed commands SQL split: [backend/app/account_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/activity_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed commands SQL split: [backend/app/annotation_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed authored-repertoire command storage split: [backend/app/branch_commands.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- Completed authored-repertoire command storage split: [backend/app/canonical_prefix_api.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- Completed authored card edit/archive storage split: [backend/app/card_commands.py](tempo_legacy_database/adapters/authored_repertoires/card_edits/README.md).
- Completed card/repertoire deletion storage split: [backend/app/card_deletion.py](tempo_legacy_database/adapters/authored_repertoires/card_deletion/README.md).
- Completed command receipt persistence split: [backend/app/command_gateway.py](tempo_legacy_database/adapters/commands/receipts/README.md).
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
- Completed HTTP composition persistence split: [backend/app/main.py](tempo_legacy_database/adapters/http_composition/README.md).
- Completed controls SQL split: [backend/app/opening_evidence_api.py](tempo_legacy_database/adapters/controls/README.md).
- Completed existing opening advisory API storage split: [backend/app/opening_segmentation_api.py](tempo_legacy_database/adapters/authored_repertoires/opening_advisory/README.md).
- Completed authored discovery/integrity command storage split: [backend/app/opportunity_commands.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- Completed PGN import/discard storage split: [backend/app/pgn_import_commands.py](tempo_legacy_database/adapters/authored_repertoires/pgn_import/README.md).
- Completed legacy bounded prefix-read projection split: [backend/app/prefix_diagnostics_api.py](tempo_legacy_database/adapters/prefix_reads/README.md).
- Completed existing opening advisory API storage split: [backend/app/prefix_evaluation_api.py](tempo_legacy_database/adapters/authored_repertoires/opening_advisory/README.md).
- Completed foreground control storage split: [backend/app/prefix_split_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed legacy bounded prefix-read projection split: [backend/app/prefix_transition_api.py](tempo_legacy_database/adapters/prefix_reads/README.md).
- Completed queue/lifecycle storage split: [backend/app/queue_attempt_origins.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed queue command storage split: [backend/app/queue_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed foreground control storage split: [backend/app/repertoire_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed controls SQL split: [backend/app/repertoire_settings.py](tempo_legacy_database/adapters/controls/README.md).
- Completed controls SQL split: [backend/app/review_commands.py](tempo_legacy_database/adapters/controls/README.md).
- Completed pasted-analysis storage split: [backend/app/services/analysis_paste.py](tempo_legacy_database/adapters/authored_repertoires/README.md).
- Completed background activity control storage split: [backend/app/services/background_activity.py](tempo_legacy_database/adapters/controls/activity/README.md).
- Completed background diagnostic query/scope split: [backend/app/services/background_diagnostics.py](tempo_legacy_database/adapters/background_diagnostics/README.md).
- Completed queue/lifecycle storage split: [backend/app/services/background_metrics.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed existing canonical-prefix implementation storage split: [backend/app/services/canonical_prefix.py](tempo_legacy_database/adapters/authored_repertoires/canonical_prefix/README.md).
- Completed existing canonical-prefix implementation storage split: [backend/app/services/canonical_prefix_preview.py](tempo_legacy_database/adapters/authored_repertoires/canonical_prefix/README.md).
- Completed repertoire-scope storage split: [backend/app/services/canonical_scope_freshness.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- Completed defensive settings/predicate storage split: [backend/app/services/defensive_analysis.py](tempo_legacy_database/adapters/defensive_analysis.py).
- Completed discovery admission and acceptance storage split: [backend/app/services/discovery_admission.py](tempo_legacy_database/adapters/authored_repertoires/discovery_admission/README.md).
- Completed legacy scheduling storage split: [backend/app/services/durable_scheduling.py](tempo_legacy_database/adapters/scheduling/README.md).
- Completed durable task persistence and SQL compilation split: [backend/app/services/durable_tasks.py](tempo_legacy_database/adapters/durable_tasks/README.md).
- Completed legacy scheduling storage split: [backend/app/services/engine_scheduling.py](tempo_legacy_database/adapters/scheduling/README.md).
- Completed SQL/DDL split: [backend/app/services/frozen_opening_order.py](../backend/app/services/frozen_opening_order.py) now only binds [Study policy](../backend/app/study/frozen_order/README.md) and [database storage](tempo_legacy_database/adapters/frozen_order.py). Legacy caller composition migration remains.
- Completed legacy analysis/finding storage split: [backend/app/services/game_analysis_worker.py](tempo_legacy_database/adapters/games/analysis_worker/README.md).
- Completed legacy analysis/finding storage split: [backend/app/services/game_findings.py](tempo_legacy_database/adapters/games/finding_projections/README.md).
- Completed legacy Games imports storage split: [backend/app/services/game_sync.py](tempo_legacy_database/adapters/games/imports/README.md).
- Completed legacy Games coordinator storage split: [backend/app/services/game_sync_coordinator.py](tempo_legacy_database/adapters/games/coordinator/README.md).
- Completed legacy Games evidence storage split: [backend/app/services/gameplay_events.py](tempo_legacy_database/adapters/games/evidence/README.md).
- Completed guided game-review storage split: [backend/app/services/guided_review.py](tempo_legacy_database/adapters/games/guided_review/README.md).
- Completed legacy introduction-priority storage split: [backend/app/services/introduction_priorities.py](tempo_legacy_database/adapters/introduction_priorities/README.md).
- Completed legacy Learning review storage split: [backend/app/services/learning_review_adapter.py](tempo_legacy_database/adapters/learning_bridge/README.md).
- Completed legacy authority/evidence storage split: [backend/app/services/legacy_game_authority.py](tempo_legacy_database/adapters/games/authority/README.md).
- Completed legacy Study remediation storage split: [backend/app/services/legacy_study_remediation.py](tempo_legacy_database/adapters/learning_bridge/README.md).
- Completed queue/capacity fixed storage split: [backend/app/services/opening_capacity_admission.py](tempo_legacy_database/adapters/queue_refresh/README.md).
- Completed existing compatibility graph storage split: [backend/app/services/opening_graph.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed existing legacy opening graph recovery storage split: [backend/app/services/opening_graph_recovery.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/opening_progression.py](tempo_legacy_database/adapters/opening_progression.py).
- Completed opening ranking snapshot storage split: [backend/app/services/opening_ranking_snapshot.py](tempo_legacy_database/adapters/opening_ranking/README.md).
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
- Completed native integrity query/cursor split: [backend/app/services/postgres_integrity.py](tempo_legacy_database/adapters/authored_repertoires/integrity/README.md).
- Completed legacy derived-publication storage split: [backend/app/services/postgres_next_opponent.py](tempo_legacy_database/adapters/derived_publications/README.md).
- Completed legacy opening-evidence storage split: [backend/app/services/postgres_opening_evidence.py](tempo_legacy_database/adapters/opening_evidence/README.md).
- Completed existing legacy opening graph worker storage split: [backend/app/services/postgres_opening_graph.py](tempo_legacy_database/adapters/opening_graph/README.md).
- Completed existing opening mutation storage split: [backend/app/services/postgres_opening_segmentation.py](tempo_legacy_database/adapters/authored_repertoires/opening_mutations/README.md).
- Completed legacy priority lifecycle storage split: [backend/app/services/postgres_priority.py](tempo_legacy_database/adapters/priority_lifecycle/README.md).
- Completed queue/capacity fixed storage split: [backend/app/services/postgres_queue_refresh.py](tempo_legacy_database/adapters/queue_refresh/README.md).
- Completed existing opening mutation storage split: [backend/app/services/prefix_split.py](tempo_legacy_database/adapters/authored_repertoires/opening_mutations/README.md).
- Completed existing prefix transition application storage split: [backend/app/services/prefix_transition_application.py](tempo_legacy_database/adapters/prefix_application/README.md).
- Completed legacy priority lifecycle storage split: [backend/app/services/priority_retention.py](tempo_legacy_database/adapters/priority_lifecycle/README.md).
- Completed queue admission fixed storage split: [backend/app/services/queue_admission_adapter.py](tempo_legacy_database/adapters/queue_refresh/application/README.md).
- Completed queue application fixed storage split: [backend/app/services/queue_application.py](tempo_legacy_database/adapters/queue_refresh/application/README.md).
- Completed Redis admission/policy storage split: [backend/app/services/redis_admission_gate.py](tempo_legacy_database/adapters/redis_control/README.md).
- Completed queue/lifecycle storage split: [backend/app/services/refresh_requests.py](tempo_legacy_database/adapters/lifecycle/README.md).
- Completed legacy read/publication projection split: [backend/app/services/repertoire_comparison.py](tempo_legacy_database/adapters/projections/README.md).
- Completed authored-Studies policy / database line-projection split: [backend/app/services/repertoire_conflicts.py](../backend/app/studies/README.md).
- Completed repertoire coverage query/session split: [backend/app/services/repertoire_coverage.py](tempo_legacy_database/adapters/coverage/README.md).
- Completed repertoire-scope storage split: [backend/app/services/repertoire_game_refresh.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- Completed integrity scan/repair storage split: [backend/app/services/repertoire_integrity.py](tempo_legacy_database/adapters/authored_repertoires/integrity/README.md).
- Completed repertoire-scope storage split: [backend/app/services/repertoire_move_verification.py](tempo_legacy_database/adapters/repertoire_scope/README.md).
- Completed repertoire opportunity storage split: [backend/app/services/repertoire_opportunities.py](tempo_legacy_database/adapters/authored_repertoires/opportunities/README.md).
- Completed legacy read/publication projection split: [backend/app/services/repertoire_statistics.py](tempo_legacy_database/adapters/projections/README.md).
- Completed borrowed Study review storage split: [backend/app/services/review_service.py](tempo_legacy_database/adapters/study/review/README.md).
- Completed legacy statistics storage split: [backend/app/services/statistics.py](tempo_legacy_database/adapters/statistics/README.md).
- Completed legacy exercise-attempt storage split: [backend/app/services/study_attempts.py](tempo_legacy_database/adapters/study_attempts/README.md).
- Completed controls SQL split: [backend/app/services/study_mode.py](tempo_legacy_database/adapters/controls/README.md).
- Completed authored Studies portable storage split: [backend/app/services/study_portable.py](tempo_legacy_database/adapters/studies/portable/README.md).
- Completed Redis admission/policy storage split: [backend/app/services/study_priority.py](tempo_legacy_database/adapters/redis_control/README.md).
- Completed intact SQL-only helper move: [database/tempo_legacy_database/adapters/tactic_admission.py](tempo_legacy_database/adapters/tactic_admission.py).
- Completed legacy tactic persistence split: [backend/app/services/tactic_capture.py](tempo_legacy_database/adapters/tactics/README.md).
- Completed legacy tactic persistence split: [backend/app/services/tactical_catalog.py](tempo_legacy_database/adapters/tactics/README.md).
- Completed legacy read/publication projection split: [backend/app/services/tactical_opportunities.py](tempo_legacy_database/adapters/projections/README.md).
- Completed existing threat pipeline storage split: [backend/app/services/threat_pipeline.py](tempo_legacy_database/adapters/threats/README.md).
- Completed existing threat training storage split: [backend/app/services/threat_training.py](tempo_legacy_database/adapters/threats/README.md).
- Completed foreground control storage split: [backend/app/settings_commands.py](tempo_legacy_database/adapters/foreground_controls/README.md).
- Completed intact database snapshot/evidence reader move: [backend/app/snapshot_reads.py](tempo_legacy_database/adapters/snapshot_reads.py).
- Completed commands SQL split: [backend/app/statistics_commands.py](tempo_legacy_database/adapters/commands/README.md).
- Completed legacy exercise-attempt storage split: [backend/app/study_attempt_commands.py](tempo_legacy_database/adapters/study_attempts/README.md).
- Completed authored-Studies command storage split: [backend/app/study_commands.py](tempo_legacy_database/adapters/commands/studies/README.md).
- Completed authored-Studies route storage split: [backend/app/study_routes.py](tempo_legacy_database/adapters/commands/studies/README.md).
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

The 156-path baseline is a conservative SQL/IndexedDB scan, not an exhaustive proof of storage placement. The first nine additional storage/tooling sources were resolved by checking Redis operations, SQL constants and Content native harnesses outside the original call scan. Further closure scans found the separately tracked native/test groups below, now relocated. None alter baseline arithmetic.

| Additional source / status | Database operations | Application responsibility to retain outside database |
| --- | --- | --- |
| Extracted [background_wakes.py](../backend/app/services/background_wakes.py) | Cached factory and four exact Lua operations now live in [wake storage](tempo_legacy_database/adapters/redis_control/wake_runtime/README.md). | Existing wake lifecycle, identity, callback and queue orchestration. |
| Extracted [background_runtime.py](../backend/app/services/background_runtime.py) | Short-timeout factory, worker-slot SET/15-second TTL and bounded sixteen-slot MGET now live in the same [storage category](tempo_legacy_database/adapters/redis_control/wake_runtime/README.md). | Runtime snapshot/lease timing and presentation; its ContextVar `.set` calls are not Redis. |
| Extracted [legacy_game_observation.py](../backend/app/legacy_game_observation.py) | Accepted-head and imported-game SELECTs now live in the same [authority storage](tempo_legacy_database/adapters/games/authority/README.md), retaining the existing reader. | One-game capture scope, legal decision plies and export authority/format. |
| Extracted [legacy_game_capture.py](../backend/app/services/legacy_game_capture.py) | Existing metadata and finite stream/count/high-water/page operations now live in [authority storage](tempo_legacy_database/adapters/games/authority/README.md). | Capture budget/digest/selection and finite traversal; extract with its authority/observation callers. |
| Relocated [writer_latency.py](tests/benchmarks/writer_latency.py) | Disposable SQLite connection/schema/inserts and writer contention tooling. | Existing benchmark reporting; whole dedicated benchmark may relocate intact. |
| Extracted [qualify_legacy_bridge.py](../database/tempo_legacy_database/tooling/legacy_bridge/qualify_legacy_bridge.py) fixture operations | Five existing native setup statements now live in [ownership fixture.py](ownership/fixture.py), including the two unchanged test-table DDL definitions. | Existing allocation/orchestration now lives together in the root legacy native tooling category; five fixed fixture statements retain their separate ownership helper. No native execution. |

Three Content native-only files discovered after the runtime audit are also relocated intact: [the PostgreSQL suite](tempo_content_database/tests/test_postgres.py), [its runner](tempo_content_database/tooling/run_postgres.py) and [benchmark](tempo_content_database/tooling/benchmark.py). Native assertions and benchmark are byte-identical; only runner/build/discovery/source-hash paths change. Application-facing synthetic tests and source-free consumer tooling retain Content ownership. These three additional relocations do not alter the original156-row arithmetic.

### Further native harness and storage-test placement — current status

These separately discovered source-placement/discovery groups are now complete; they are not part of the original 156-row arithmetic. All changes are mechanical. No native suite, restore, provisioning or live service was run. Application policy/contract tests remain outside database where they do not implement database-specific fixtures. Historical qualification receipts retain original paths. Native qualification and strict domain/composition boundaries remain unverified or unfinished as specified below.

| Owner / placement status | Completed placement or reference work | Current validation limit |
| --- | --- | --- |
| Completed Game Evidence | [Native suite and source metadata tests](tempo_game_evidence_database/tests/README.md),[runner](tempo_game_evidence_database/tooling/README.md) and qualifier root-owner fingerprints are centralized. | Three installed focused checks and sdist→wheel/resource checks pass; all native functions/SQL/guards unchanged. Native tooling remains source-checkout-only and unexecuted. |
| Completed Intelligence | Ten native runner/test/fixture/support files,including actual-A/Analysis qualification,now live in [owner tests](tempo_intelligence_database/tests/README.md) and [tooling](tempo_intelligence_database/tooling/README.md); artifact/registry inputs follow them. | Guarded collection preserves141 default identities in original/source/wheel-consumer layouts,with no native fixture/body execution. Runtime Python bytes unchanged; full qualifier has a documented pre-existing guard-name limitation. |
| Completed Learning/remediation/review placement | Remediation [tests](tempo_remediation_database/tests/native/README.md)/[runner](tempo_remediation_database/tooling/README.md),runtime [native/restore tooling](tempo_learning_database/tooling/README.md),legacy-review [tests/guards](tempo_legacy_database/tests/learning_review/README.md)/[runner](tempo_legacy_database/tooling/learning_review/README.md) are centralized. Pure qualification-policy tests remain outside. | Source/discovery/archive checks pass; runtime wheel contents unchanged. Application tooling selection is93pass/seven failures reproduced original; native tooling is sdist-only and unexecuted. |
| Completed legacy native placement | [Bridge proofs](tempo_legacy_database/tests/legacy_bridge/README.md),[acceptance](tempo_legacy_database/tests/discovery_acceptance/README.md),[queue admission](tempo_legacy_database/tests/queue_admission/README.md) and [qualification/resource tooling](tempo_legacy_database/tooling/legacy_bridge/README.md) are centralized. | Stale acceptance import repaired;131 pure cases and25 guarded collection nodes pass. Exact existing controlled read-only source mount is allowed; all other owner/target/cleanup predicates unchanged. No native proof claimed. |
| Completed Content | [Cursor suite and four storage-only functions](tempo_content_database/tests/unit/README.md) now reside root; pure runner/artifact/registry inputs explicitly include only unit tests. | Seventy focused cases and source/discovery/archive checks pass; native parent stays excluded. Runtime wheel resources are unchanged; source-free full qualifier and five denied loopback cases remain unverified. |

Two dedicated benchmark entry points are also centralized: [storage read rehearsal](tempo_legacy_database/tooling/benchmark_storage_reads.py) and [writer launcher](tests/benchmarks/benchmark-writer.mjs). Their existing bodies are unchanged except physical root/resolver references; npm and finite direct-CLI discovery follow them. Other benchmark scripts exercise application algorithms and retain their logical owners. No benchmark ran.

The original `redis_admission_gate.py` and `study_priority.py` entries are also real control persistence, not false positives. Their Redis factories/Lua/pipeline/operations and Study-settings query now live under [redis_control](tempo_legacy_database/adapters/redis_control/README.md), preserving private namespace/client overrides and local failure/timing behavior. No actual Redis values or live services were inspected.


The scan excludes the centralized tree and tests. Historical evidence/qualification receipts retain their recorded source paths; current active references were relocated. Redis orchestration and host resource coordination remain in their existing infrastructure owners.

### Additional browser source classification and minimal plan

The PR350 closure scan identified three actual IndexedDB mixed-file remainders, now separated in this follow-up: [prepared training](browser/prepared-training-storage.ts), [opening journal](browser/opening-evidence-journal-storage.ts) and [legacy Maia model cache](browser/maia-worker-cache.js). Existing capture/replay/download/inference decisions stay outside; transaction callbacks, stored keys, schema versions and public worker URL remain unchanged. The Maia cache's canonical source is emitted as a build asset, with no maintained duplicate or new runtime framework.

A separate browser API scan at PR350 found 58 runtime files / 357 bound WebStorage calls. This is a **classification inventory, not 58 database files or an instruction to add an adapter for every preference read**. Keep the following categories distinct before additional edits:

| Category | Existing sources / minimal organization | Current scope |
| --- | --- | --- |
| Actual database / IndexedDB | Prepared queue, opening attempt/event journal and model Blob cache above. | Complete existing operations move root; policy stays outside. Source/fake/build proof does not qualify a real browser or failure isolation. |
| Durable browser mutation outboxes | Sixteen authoring/Discovery journals, nine Games/Tactics/Endgames journals, practice/settings/activity receipts, immutable workbench command journal, and the authored-Studies retry key. | Move only existing fixed persistence operations and storage record facts; retain separate implementations, keys, immutable payloads, validation/error/receipt/timing and caller ownership. No new generic storage/dispatch framework. Owner README dictionaries describe keys and payloads. These splits are still in progress. |
| Saved practice/annotation records | Existing burial state, tactic progress and authored annotations with pending mutation identities. | Review the specific stored authority and move only a concrete cohesive persistence responsibility; preserve existing policy/defaults and distinct concepts. |
| Ordinary browser preferences / display state | Board/layout/tabs/sound/view settings, notification display history, selected-pack preference, Lichess connection/OAuth session metadata. | Retain with the logical domain or browser action. Their use of local/sessionStorage alone does not justify a database adapter or storage redesign. |
| Rebuildable browser asset/response caches | Existing workspace/Explorer response caches and `public/sw.js` CacheStorage shell assets. | Retain existing logical cache owner and operations. CacheStorage is separately inventoried browser asset delivery, not an unfinished relational/IndexedDB extraction requirement. |

The generic `validated-data` helper remains unchanged; this task does not introduce callback-expanded validation wrappers. Mixed UI files are classified by exact key/responsibility, not moved wholesale. Actual DB/Redis diagnostic/backup tooling and source-level native fixture candidates are tracked separately from ordinary browser state and from the closed original 156-row checklist. No live browser data, sessions, credentials or services are inspected.

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
| Legacy backend | 120 | 0 | 0 |
| Learning runtime | 26 | 0 | 0 |
| Content | 2 | 0 | 0 |
| Game Evidence | 5 | 0 | 0 |
| Intelligence | 1 | 1 | 0 |
| Browser connection | 1 | 0 | 0 |
| **Total baseline** | **155** | **1** | **0** |

Additional sources above show their separate placement status. The initially relocated adapters also received the bounded application-responsibility audits below; remaining technical composition and backend boundary work stays explicit. Zero pending baseline rows alone does not establish that an adapter is application-free or a domain is independent.

### Application blocks removed from originally moved adapters

| Owner | Existing application home now outside database | Actual remaining work |
| --- | --- | --- |
| Content | [Source/exercise/binding/publication/coverage policy](../services/content/src/tempo_content/policy/README.md) [reconciliation/route/binding/exercise worker helpers](../services/content/src/tempo_content/worker/README.md) and [publication preparation](../services/content/src/tempo_content/publication/README.md). | [Source loading/staging orchestration](../services/content/src/tempo_content/source/README.md) is now outside database. The complete fifty-seven-method/four-helper store audit identifies no further independent mixed workflow; fixed guards/read projections/lifecycle/composition remain explicit. Nineteen production Content files contain no direct driver/cursor operations. The three database-specific native test/runner/benchmark files now reside in root owner [tests](tempo_content_database/tests/README.md) and [tooling](tempo_content_database/tooling/README.md); they are sdist-only, with no native execution. This is not a repository-wide completion claim. |
| Game Evidence | [Source identity/normalization](../services/game-evidence/src/tempo_game_evidence/source.py), [snapshot/cache/staleness preparation](../services/game-evidence/src/tempo_game_evidence/snapshot.py), [comparison preparation](../services/game-evidence/src/tempo_game_evidence/comparison.py), [reconciliation coordination](../services/game-evidence/src/tempo_game_evidence/reconciliation.py), and [response/list-input policy](../services/game-evidence/src/tempo_game_evidence/reporting.py). The [job worker](../services/game-evidence/src/tempo_game_evidence/worker.py) owns dispatch/failure control flow. | Final import metadata/player shaping is separated; the two named failure operations retain original fixed retry SQL literals and fence parameters. Source audit found no further pure application block in this owner store. SQL locks, replay conflicts, lease/resource limits, bounded atomic fanout and the unchanged compatibility cache holder stay explicit persistence/composition responsibilities; native execution remains unverified. |
| Intelligence | [Finding assembly](../services/intelligence/src/tempo_intelligence/analysis/README.md#finding-assembly), [serialization](../services/intelligence/src/tempo_intelligence/serialization.py) and [request descriptor/cache/retry helpers](../services/intelligence/src/tempo_intelligence/source_requests.py). | Request inspection, refresh/retry, [Maia profile/job/result/status](../services/intelligence/src/tempo_intelligence/maia_work.py) and evidence response orchestration now use fixed owner operations. Source submit/replay/cache-refresh and provider claim/publication application sequences now also live outside database, using fixed admission/provider operations. All four credential workflow source moves are now separated; the current Store audit identifies no further independent mixed workflow. Twelve existing Analysis read/workspace/export sequences now live in [read_work.py](../services/intelligence/src/tempo_intelligence/analysis/read_work.py), using fixed root reads. Manifest/page/finalize application sequences now live in [input_work.py](../services/intelligence/src/tempo_intelligence/analysis/input_work.py), using fixed input guards. Run submit/command/dismissal and receipt-response application sequences now live in [run_work.py](../services/intelligence/src/tempo_intelligence/analysis/run_work.py). Capture/derive/derive_next sequences now live in [publication_work.py](../services/intelligence/src/tempo_intelligence/analysis/publication_work.py). The final forty-method AnalysisStore audit classifies three fixed guards, thirty-seven composition methods and zero independent mixed application blocks; claim/accept/failure/engine-unavailable workflows now live in [engine_work.py](../services/intelligence/src/tempo_intelligence/analysis/engine_work.py). The initial Store audit covers38 unchanged methods. The additional runtime Psycopg subclass now lives in root [background_connection.py](tempo_intelligence_database/adapters/background_connection.py); runtime retains runner orchestration and the single shared deadline ContextVar. The scoped owner source scan identifies no remaining direct driver/SQL/cursor implementation outside root; the worker execute call is an engine-client operation. No full-system/domain independence follows from this finite source audit. No actual credential/provider action is part of these source moves. |
| Learning and legacy owner adapters | Frozen preference policy now lives in the [legacy Study folder](../backend/app/study/frozen_order/README.md) and [Learning Study folder](../services/learning/runtime/src/tempo_learning_runtime/study/frozen_order/README.md); authored-line conflict computation lives in [Studies](../backend/app/studies/README.md). | Learning has zero original baseline files pending. Its complete initial-adapter audit initially covered42 functions/methods across seven files. Opening-evidence application helpers, DTO and mixed workflow portions now live in runtime [opening_evidence](../services/learning/runtime/src/tempo_learning_runtime/opening_evidence/README.md), with fixed operations/root read scopes separate. The updated58-function/method audit identifies no remaining independent application block in those seven adapters; compatibility exports and uncertain standalone usage remain inventoried. This source-level audit does not claim whole-system/domain isolation. The initial legacy audit now covers14 modules/88 function-method definitions plus connections and original schema lifecycle. No further independent feature-policy block was identified in that bounded review after the frozen-order,readiness,acceptance and feedback splits; original baseline work and technical composition-boundary review remain. The initial readiness adapter now contains only four fact queries; its HTTP/Celery/queue-progress workflow moved to [operations](../backend/app/operations/README.md). The initial discovery acceptance adapter now contains six fixed queries; its exact existing class/scope/contract/task coordination lives in the backend discovery acceptance composition. The initial feedback adapter now contains only fixed facts/locks/priority writes and bounded cursor batches; its unchanged fourteen-method borrowed capability, typed mapping, sorted priority choices and canonical queue/remediation callbacks live in backend feedback-store composition. Direct re-export imports for preview/origin DDL, scope predicates and metric flushing now refer to their actual root definitions rather than routing through application facades. Initial source inspection classifies the1766-line connection module’s initialization/schema upgrades as database lifecycle/DDL,not independent feature policy; no initialization executes during that inspection. Remaining technical composition edges are activity-gate admission/reservation and runtime telemetry in connections/executor/PostgreSQL timing,and explicit frozen/review/UTC contracts. These still need the subsequent backend composition-boundary review; no whole-graph independence claim. Do not assume that moving whole files established a boundary. |

The [HTTP composition storage README](tempo_legacy_database/adapters/http_composition/README.md) records the complete database extraction from `backend/app/main.py`: all202 original direct calls plus one dynamic alias, SQL/filter builders and two field selectors now reside in root owner operations. The complete source scan finds no inline SQL/predicate/DDL/driver/cursor/factory/transaction implementation. Seven existing SQL compatibility exports remain aliases to single root implementations. Main's HTTP/outer composition and remaining mixed application responsibilities are separate backend-phase work; database file closure does not establish cohesive domain boundaries. The fixed availability helper and queue application operations are centralized too. The [queue refresh storage README](tempo_legacy_database/adapters/queue_refresh/README.md) tracks all79 queue direct/bounded sites plus the complete capacity-query/reader split. Both files have no direct database operations/SQL literals; existing reader compatibility APIs remain inventoried. `queue_application.py` now has no direct database operations/SQL literals/dialect checks; all39 original sites plus the sparse selector are separated, including publication and requeue. The capacity-reader bridge in `queue_admission_adapter.py` is now fixed root storage plus the unchanged pure route-selection loop; that baseline file is also complete. Existing standalone reader compatibility APIs remain inventoried.

All completed checklist rows above link their actual owner. Complete bound queries, driver/dialect handling, DDL, row locks, cursor batches and savepoints belong inside the one root database tree. Keep application decisions in logical owner folders and preserve their original evaluation/transaction/callback points. Existing legacy call-site composition/cross-feature coupling remains disclosed; no new sibling-domain dependency, generic SQL execution port, shared runtime, duplicate implementation or full-domain isolation claim is introduced.

The [validation record](VALIDATION.md) and category READMEs contain exact focused selections, source/SQL preservation evidence, artifact/resource discovery and native/qualification limits. No live data/schema or canonical-model implementation is part of this work. Continue one coherent owner slice at a time; inline database operations and mixed adapter policy are unfinished work, not permanent exceptions.
