# Legacy backend migrations

## Current migration owner

[Source readiness](../schema_version.py) requires schema 65; this is not an
installed-state observation. [The ordered migration directory](.) preserves
forward upgrade history, including [064 retirement dispositions](064_retire_optional_study_producers.sql)
and [065](065_position_introduction_holds.sql).
The [supported migration runner](../../../tooling/apply_postgres_migrations.py)
uses the retained SQL/checksum resources rather than importing a retired runtime.
Use the [registered CLI lifecycle](../../../../docs/TEMPO-CLI.md) with its stopped-writer,
backup and recovery safeguards. The historical descriptions below explain past
schema additions, not current active producers or permission to migrate.

## Historical extraction and proposal record

The material below is a dated historical record, retained as of November 12, 2026.
Its implementation, test counts, open-issue instructions and proposed interfaces
are **not a current runtime inventory or commands to execute**. Removed paths are
identified as historical names, not replacement implementations. The
[immutable pre-reconciliation guide](https://github.com/aaweaver-actuary/tempo/blob/799b66234b8be386a33d980df10b8b1d9a9d8b36/database/tempo_storage/ddl/migrations/README.md)
preserves the original text and evidence. Use the current owners above for navigation.


Migration [060](060_position_first_response_immutable.sql) rejects updates, deletions and truncation of original first responses at source readiness 60. Inserts and exact replay remain unchanged; existing rows are not rewritten. This source change does not migrate a live database.

Migration [059](059_queue_refresh_epochs.sql) adds nonnegative requested and
published queue-refresh epochs at source readiness 59. Existing projection
generations, queue entries, accepted reviews and retained attempts are preserved.
In-flight checkpoints without a cycle epoch restart conservatively. Producer
coalescing and the final publication fence must be deployed together. The
supported migration workflow requires its existing stopped-writer/backup and
verification steps; this source change does not itself migrate a live database.

[DDL owner](../README.md) · Source schema charts (historical path: `../../schema/README.md`)

Migration [058](058_queue_attempt_abandonment.sql) adds exact-origin abandonment receipts at source readiness 58. It preserves origins and accepted review history; no live migration is implied.

Position Study adds [057_position_study.sql](057_position_study.sql), at source
readiness 57. Its reject-only legacy card ID namespace guard reserves new IDs
without changing existing rows. Its owner dictionary (historical path: `../../schema/study-position/README.md`)
describes inactive epochs, schedules, issuance and receipts. This is additive
source DDL; it does not seed or activate a cohort.

#622 adds [055_study_attempt_issuance.sql](055_study_attempt_issuance.sql), at
source readiness 55. It extends existing attempt origins with actual first-head
sequence/time. Existing issued attempts keep unknown generation/time; no history
is backfilled. The canonical dictionary (historical path: `../../schema/study-queue-review-1/README.md`)
and Study issuance adapter (historical path: `../../adapters/study/issuance.py`) describe the fence
and measured issuance ordinal. Applying source DDL remains an explicit owner
operation; this change does not migrate the live database.

#623 adds [056_managed_game_feedback_mode.sql](056_managed_game_feedback_mode.sql)
after #622's [055_study_attempt_issuance.sql](055_study_attempt_issuance.sql), at
source readiness 56. One Games-owned row per workspace mirrors explicit managed
mode/release intent; only new Games handoff consumes its shared writer fence.
Accepted Study obligations and ordinary serving remain independent. No provider
configuration, learning history or existing queue row is rewritten. The operator
and exact release must be qualified before any separately authorized live apply.

C1 adds [054_product_repertoire_review.sql](054_product_repertoire_review.sql) after
053, at source readiness 54. It registers the existing Repertoire approval
[tables](../../../../backend/app/repertoire/changes/README.md) and Study conflict
holds/receipts (historical path: `../../adapters/study/CONFLICTS.md`). These owners share the caller
transaction; no live migration or deployment is performed here.

T22 adds [053_study_recombination.sql](053_study_recombination.sql) after
independent 052, with source readiness 53. The
Study recombination chart (historical path: `../../adapters/study/README.md`) covers immutable
review/streak facts, new presentation generations/lineage and route-local current
decision targets. Original cards, source moves, review/effect identities and
deadlines are retained. No live migration or deployment is performed here.

Existing ordered source DDL. Runtime startup and migration authority are unchanged. Use the owning documented explicit migration command; this PR does not run it.

T06 adds [051_study_game_feedback.sql](051_study_game_feedback.sql), registered
at readiness version 51. Its owner schema chart (historical path: `../../adapters/study/README.md`)
covers receipts, original deadlines and durable pre-delivery holds; no live
migration has been run by the feature implementation.

- [001_initial.sql](001_initial.sql)
- [002_game_sync_windows.sql](002_game_sync_windows.sql)
- [003_integrity_accumulators.sql](003_integrity_accumulators.sql)
- [004_discovery_cursor.sql](004_discovery_cursor.sql)
- [005_coverage_building.sql](005_coverage_building.sql)
- [006_coverage_maia_claim.sql](006_coverage_maia_claim.sql)
- [007_coverage_explorer_claim.sql](007_coverage_explorer_claim.sql)
- [008_coverage_recovery.sql](008_coverage_recovery.sql)
- [009_versioned_game_analysis.sql](009_versioned_game_analysis.sql)
- [010_game_analysis_publications.sql](010_game_analysis_publications.sql)
- [011_versioned_game_positions.sql](011_versioned_game_positions.sql)
- [012_versioned_game_repertoire.sql](012_versioned_game_repertoire.sql)
- [013_versioned_game_findings.sql](013_versioned_game_findings.sql)
- [014_versioned_game_events.sql](014_versioned_game_events.sql)
- [015_game_finding_preparations.sql](015_game_finding_preparations.sql)
- [016_phone_review_receipts.sql](016_phone_review_receipts.sql)
- [017_operation_recovery.sql](017_operation_recovery.sql)
- [018_operation_attempt_ownership.sql](018_operation_attempt_ownership.sql)
- [019_threat_claim_lookup.sql](019_threat_claim_lookup.sql)
- [020_ordered_threat_claim.sql](020_ordered_threat_claim.sql)
- [021_priority_preparations.sql](021_priority_preparations.sql)
- [022_priority_preparation_ordering.sql](022_priority_preparation_ordering.sql)
- [023_priority_visible_game_positions.sql](023_priority_visible_game_positions.sql)
- [024_opening_segmentation.sql](024_opening_segmentation.sql)
- [025_tactic_capture.sql](025_tactic_capture.sql)
- [026_background_diagnostics.sql](026_background_diagnostics.sql)
- [027_handled_discoveries.sql](027_handled_discoveries.sql)
- [028_revisioned_discovery_admissions.sql](028_revisioned_discovery_admissions.sql)
- [029_repertoire_daily_limits.sql](029_repertoire_daily_limits.sql)
- [030_opening_decision_evidence.sql](030_opening_decision_evidence.sql)
- [031_defensive_analysis_pause.sql](031_defensive_analysis_pause.sql)
- [032_queue_attempt_origins.sql](032_queue_attempt_origins.sql)
- [033_canonical_prefix.sql](033_canonical_prefix.sql)
- [034_current_canonical_routes.sql](034_current_canonical_routes.sql)
- [035_canonical_publication_freshness.sql](035_canonical_publication_freshness.sql)
- [036_prefix_diagnostics_indexes.sql](036_prefix_diagnostics_indexes.sql)
- [037_card_deletion.sql](037_card_deletion.sql)
- [038_prefix_transition_application.sql](038_prefix_transition_application.sql)
- [039_prefix_transition_lock_budget.sql](039_prefix_transition_lock_budget.sql)
- [040_queue_unlock_deadlines.sql](040_queue_unlock_deadlines.sql)
- [041_real_game_study_obligations.sql](041_real_game_study_obligations.sql)
- [042_queue_attempt_issuance.sql](042_queue_attempt_issuance.sql)
- [043_background_scheduling_turns.sql](043_background_scheduling_turns.sql)
- [044_next_opponent_profiles.sql](044_next_opponent_profiles.sql)
- [045_engine_scheduling.sql](045_engine_scheduling.sql)
- [046_refresh_input_versions.sql](046_refresh_input_versions.sql)
- [047_study_only_mode.sql](047_study_only_mode.sql)
- [048_frozen_opening_order.sql](048_frozen_opening_order.sql)
- [049_study_priority_recovery.sql](049_study_priority_recovery.sql)
- [050_learning_remediation.sql](050_learning_remediation.sql)

- [054_product_repertoire_review.sql](054_product_repertoire_review.sql)
- [055_study_attempt_issuance.sql](055_study_attempt_issuance.sql)

- [056_managed_game_feedback_mode.sql](056_managed_game_feedback_mode.sql)
- [057_position_study.sql](057_position_study.sql)
- [058_queue_attempt_abandonment.sql](058_queue_attempt_abandonment.sql)
- [059_queue_refresh_epochs.sql](059_queue_refresh_epochs.sql)

- [060_position_first_response_immutable.sql](060_position_first_response_immutable.sql)

Migration [061](061_content_origin_receipts.sql) retains immutable historical
authored source observations at readiness61. It confers no Content projection,
physical currentness or serving authority. See the
historical origin dictionary (historical path: `../../schema/authored-origin/README.md`).
