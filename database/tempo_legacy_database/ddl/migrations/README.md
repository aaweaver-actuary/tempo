# Legacy backend migrations

[DDL owner](../README.md) · [Source schema charts](../../schema/README.md)

T22 adds [053_study_recombination.sql](053_study_recombination.sql) after
independent 052, with source readiness 53. The
[Study recombination chart](../../adapters/study/README.md) covers immutable
review/streak facts, new presentation generations/lineage and route-local current
decision targets. Original cards, source moves, review/effect identities and
deadlines are retained. No live migration or deployment is performed here.

Existing ordered source DDL. Runtime startup and migration authority are unchanged. Use the owning documented explicit migration command; this PR does not run it.

T06 adds [051_study_game_feedback.sql](051_study_game_feedback.sql), registered
at readiness version 51. Its [owner schema chart](../../adapters/study/README.md)
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
- [051_study_game_feedback.sql](051_study_game_feedback.sql) — Study-owned lapse receipt/fence.
- [052_product_game_sync.sql](052_product_game_sync.sql) — Games-owned capture/checkpoint/delivery and explicit sync configuration; independent of Study startup.
