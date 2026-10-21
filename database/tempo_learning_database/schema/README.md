# Learning runtime source schema

Tables are grouped for navigation, not split into new runtime owners. [Database directory](../README.md). Every table has one canonical dictionary here.

| View | Tables | Stored data |
| --- | --- | --- |
| [Authored Content](authored-content/README.md) | `logical_request_bindings`, `opening_card_schedule_seeds`, `repertoire_cards`, `repertoire_integrity_card_blocks`, `repertoire_lines`, `repertoires` | Repertoire/line/membership and annotation source data or related authored-content projections. |
| [Opening Evidence](opening-evidence/README.md) | `frozen_opening_activation`, `frozen_opening_ranks`, `frozen_opening_runs`, `opening_evidence_attempts`, `opening_evidence_clean_days`, `opening_evidence_events`, `opening_evidence_observations`, `opening_evidence_presentations`, `opening_evidence_queue_contexts`, `opening_evidence_summaries` | Persisted opening decision evidence, presentation snapshots and attempts, segmentation and frozen ordering. |
| [Opening Graph](opening-graph/README.md) | `opening_graph_publications`, `opening_graph_steps` | Versioned opening graph routes, source mappings, canonical prefixes, publication/transition state and diagnostics. |
| [Practice Families](practice-families/README.md) | `accepted_defense_exercises`, `analysis_obligations`, `defense_attempts`, `defense_recognition_submissions`, `endgame_attempts`, `endgame_templates`, `tablebase_cache`, `tactic_discovery_attempts`, `tactic_progress` | Tactic, defense, threat and endgame exercise/progress/attempt facts and tablebase cache. |
| [Publications Evidence](publications-evidence/README.md) | `accepted_evidence_revisions`, `accepted_publications`, `activation_receipts`, `advisory_notifications`, `content_card_aliases`, `evidence_deliveries`, `learning_obligations`, `serving_publications` | Accepted/staged/serving publication identities and receipts, advisory/evidence notifications and practice obligations. |
| [Runtime Metadata](runtime-metadata/README.md) | `background_task_events`, `background_tasks`, `instance`, `migration_history`, `operation_receipts`, `settings` | Schema/import progress, workspace settings, durable operations and background scheduling/lease metadata. |
| [Study Queue Review (1)](study-queue-review-1/README.md) | `cards`, `daily_queue`, `daily_queue_days`, `deleted_cards`, `queue_attempt_origins`, `queue_projection_diagnostics`, `queue_projections`, `review_attempt_receipts`, `review_schedule_snapshots`, `reviews`, `studies`, `study_attempts` | Accepted cards/exercises, queue membership and presentation, teaching progress, review/attempt receipts and scheduling history. |
| [Study Queue Review (2)](study-queue-review-2/README.md) | `study_exercise_revisions`, `study_exercises`, `study_positions`, `study_sibling_burials`, `teaching_states` | Accepted cards/exercises, queue membership and presentation, teaching progress, review/attempt receipts and scheduling history. |

The 12 included remediation tables have their canonical charts/dictionary in the [Learning remediation owner](../../tempo_remediation_database/schema/README.md), using `public` here for legacy and `learning` for Learning runtime.
