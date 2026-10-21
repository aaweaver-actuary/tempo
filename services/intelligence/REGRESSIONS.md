# Intelligence regression ownership

## Database-free finding assembly

`tests/test_findings.py` owns synthetic advice identity merge/deduplication,
engine-supported draft proposal enrichment and ranking, unknown repertoire
association, incompatible-profile uncertainty and the existing source-classify /
background-evidence-read / merge composition order. Finding assembly lives in
`src/tempo_intelligence/analysis/findings.py`; the store retains evidence capture
and publication authority. These pure/fake cases complement the existing analysis,
ranking and native report/publication regressions; they do not prove native SQL,
locking, rollback, deployment or optional-service resource independence.

`test_C3_route_reference_capacity_matches_complete_authored_source_bound` owns
lossless shared-prefix route references within the producer's 10,000-route bound
and rejection beyond it. The external pilot's regular
`test_C3_full_authored_shared_prefix_retains_every_route_context` verifies 1,032
serialized authored branches through actual normalization and public Page parsing,
including encounter, learner comparison, gap and deterministic proposal anchor.

## PR295 R6/R7 corrective regressions

`integration/test_analysis_postgres.py` owns the native cases:

- `test_R6_engine_opportunity_has_no_invented_encounter_frequency`
- `test_R6_equal_support_cross_category_ranking` (also generation-bound pagination)
- `test_R6_new_revision_preserves_historical_report_and_dismissal`
- `test_R7_identical_run_submission_preserves_committed_state`
- `test_R7_concurrent_creation_replays_during_staging`

`tests/test_repairs.py` owns `test_R6_support_count_still_dominates_frequency` and
`test_R6_source_native_rankings_remain_separate` (actual/Lichess/Masters/Maia).
The existing R1/R2/R3/R4/R5 cases retain their original assertions. R7 backend
proofs preserve positions, accepted results, reports, dismissals, input, budget
and revision; no backend submission change was necessary.

The regular owning suites below retain the named #109 regressions. Parameterized
cases expand these test names; actual case identities, counts, outcomes and tested
revision come from preserved JUnit/runner reports, not from this inventory. Unit
and mock assertions do not establish PostgreSQL persistence or role isolation.

Run `tests/` through the installed distribution. Run the native files through
`integration/run_postgres.py`, which provisions only invocation-owned disposable
resources and rejects arbitrary database URLs. The native fixture disables external
network access and uses local fake providers; no live credential is required.

`integration/qualify_artifacts.py` owns source-free wheel/sdist consumer execution
on CPython 3.12/3.14, installed CLI/import guards and selected installed native
runtime proofs. Missing capability, zero selection, failure, cancellation or skipped
required cases cannot establish a pass.

## Preserved ownership boundaries

The legacy provider/session and coverage donors supplied recovery invariants;
their Intelligence replacement owners are the source, credential, cache and native
suites below. No donor Celery task registration, global broker, shared Redis or
cross-domain SQL is imported. These suites do not claim final coverage/fusion,
Learning/Study behavior, live-provider compatibility or hosted merge qualification.

Historical references are [PR #128](https://github.com/aaweaver-actuary/tempo/pull/128)
at retained head `838052fe0f95240ffa7a14be48f012a042af2bab` and
[PR #129](https://github.com/aaweaver-actuary/tempo/pull/129) at retained head
`75f78b18f0650d409d3310d80956484700ead002`. This service supplies independent
equivalent proofs for delayed rejection versus replacement credentials, durable
429 deadlines, stale lease publication and retained accepted evidence after failed
refresh. It does not claim the legacy production assembly has adopted this runtime
or that donor deployments have been retired.

## `tests/test_models.py`

Complete legal-position inputs, strict versions, terminal positions, transpositions, canonical source identity and supported cohorts.

- `test_complete_position_fixture_preserves_all_twenty_legal_moves`
- `test_snapshot_rejects_unverified_legal_universe`
- `test_snapshot_rejects_duplicate_or_illegal_moves`
- `test_terminal_snapshot_has_no_nonterminal_distribution`
- `test_local_input_requires_explicit_integer_schema_one`
- `test_request_rejects_unknown_fields_and_duplicate_sources`
- `test_one_request_has_at_most_one_configuration_per_source_family`
- `test_canonical_position_reuses_transpositions_and_normalizes_clocks`
- `test_canonical_position_normalizes_only_nonlegal_en_passant`
- `test_source_cache_inputs_ignore_policy_and_repertoire_identity`
- `test_cohort_sets_have_one_canonical_parameter_order`
- `test_distinct_provider_inputs_do_not_share_evidence_identity`
- `test_unsupported_lichess_precision_is_rejected`
- `test_masters_cannot_claim_rating_or_speed_conditioning`
- `test_maia_accepts_only_existing_single_rating_context`
- `test_canonical_hash_is_stable_and_rejects_nonfinite_payloads`
- `test_terminal_draw_snapshot_preserves_legal_moves_without_querying`
- `test_automatic_draw_input_is_distinct_from_ordinary_clock_canonicalization`

## `tests/test_maia_validation.py`

Shared-v1 result coherence, exact legal moves, identity/provenance/model fences, explicit zero and invalid mass.

- `test_versioned_maia_fixture_is_bound_to_complete_position_input`
- `test_maia_artifact_replacement_changes_reproducible_input_identity`
- `test_execution_retry_preserves_job_semantics_and_advances_generation`
- `test_maia_result_rejects_wrong_workspace_job_or_generation`
- `test_maia_result_rejects_wrong_input_digest`
- `test_maia_result_rejects_wrong_model_identity`
- `test_maia_result_preserves_explicit_zero_for_every_unselected_legal_move`
- `test_maia_result_rejects_missing_or_illegal_prediction`
- `test_maia_result_rejects_incomplete_requested_legal_universe`
- `test_maia_result_revalidates_finite_nonnegative_normalized_mass`
- `test_maia_result_rejects_duplicate_moves_even_if_mass_still_one`
- `test_terminal_position_does_not_create_a_maia_distribution`
- `test_terminal_draw_with_legal_moves_cannot_create_maia_job`
- `test_intrinsically_dead_board_cannot_bypass_maia_guard_by_omitting_terminal_flag`

## `tests/test_credentials.py`

Private provider material, immutable registration identity, permission/path/symlink bounds and safe errors.

- `test_private_credential_registration_and_aware_expiry_are_loaded_without_secret_repr`
- `test_private_credential_restart_load_preserves_registration_and_expired_material`
- `test_private_credential_invalid_schema_errors_are_fixed_and_secret_free`
- `test_private_credential_rejects_nonprivate_file_permissions`
- `test_private_credential_requires_private_parent_and_path_outside_repository`
- `test_private_credential_rejects_relative_paths_and_missing_configuration`
- `test_private_credential_rejects_symlink_file_and_symlink_parent`
- `test_private_credential_rejects_nonregular_and_oversize_or_duplicate_json`
- `test_private_credential_atomic_replacement_requires_new_explicit_identity`
- `test_private_credential_rejects_repository_without_explicit_forbidden_root`

## `tests/test_provider.py`

Native counts and uncertainty, bounded HTTP, safe response/error parsing, retries and cancellation.

- `test_explorer_valid_native_counts_include_full_legal_universe_without_probabilities`
- `test_explorer_truncated_counts_preserve_unallocated_mass_and_unreported_legal_moves`
- `test_explorer_empty_sample_remains_unknown_and_explicit_zero_is_distinct`
- `test_explorer_terminal_evidence_has_no_move_distribution`
- `test_explorer_invalid_counts_missing_fields_duplicates_and_illegal_moves_fail_closed`
- `test_explorer_failures_are_classified_without_body_or_credential_leakage`
- `test_explorer_retry_after_seconds_and_fallback`
- `test_explorer_retry_after_http_date_and_provider_429_deadline`
- `test_explorer_invalid_json_and_duplicate_fields_are_nonretryable`
- `test_explorer_response_size_is_bounded`
- `test_explorer_network_and_timeout_errors_are_safe_retryable_failures`
- `test_explorer_total_timeout_bounds_blocked_response`
- `test_explorer_cancellation_propagates_without_waiting_for_provider`
- `test_explorer_output_digest_is_stable_for_reordered_move_listing_and_ignores_decorative_metadata`
- `test_explorer_unsupported_source_and_mismatched_parameters_never_send_request`
- `test_explorer_unsupported_cohorts_fail_before_network`
- `test_explorer_request_has_explicit_connection_timeout_and_never_follows_redirects`

## `tests/test_api.py`

HTTP authentication scopes, strict bounded bodies, workspace binding, safe errors/configuration and installed import independence.

- `test_authentication_precedes_request_validation_and_database_use`
- `test_caller_reads_and_bounded_refresh_retry_have_explicit_revision_commands`
- `test_valid_versioned_requests_and_worker_payloads_reach_their_own_ports`
- `test_strict_command_errors_never_echo_unrecognized_private_input`
- `test_scoped_credentials_and_workspace_header_cannot_be_substituted`
- `test_credential_reload_uses_only_configured_file_and_generation`
- `test_missing_optional_provider_credential_does_not_block_startup_or_reads`
- `test_streamed_http_body_limit_does_not_depend_on_content_length`
- `test_readiness_fails_truthfully_when_database_becomes_unavailable`
- `test_optional_lifespan_worker_processes_bounded_slices_and_stops_cleanly`
- `test_domain_and_unexpected_errors_retain_safe_public_shapes`
- `test_startup_failure_never_rethrows_private_driver_details`
- `test_configuration_never_reprs_private_values_or_accepts_shared_tokens`
- `test_model_profiles_copy_normalized_ratings_and_reject_rebound_model_versions`
- `test_provider_endpoint_override_requires_safe_configured_origin`
- `test_private_configuration_files_reject_shared_permissions_symlinks_and_empty_content`
- `test_installed_public_imports_and_help_need_no_optional_infrastructure`
- `test_equivalent_provider_origins_normalize_host_case_and_default_port`

## `tests/test_cli.py`

Installed local command behavior, explicit bounded work, owner-only migration entrypoint and safe error output.

- `test_installed_cli_help_needs_no_configuration_or_services`
- `test_local_reads_validate_startup_and_close_owned_store`
- `test_bounded_demand_uses_strict_command_document`
- `test_versioned_fixture_commands_validate_and_dispatch_installed_models`
- `test_unknown_command_input_never_echoes_private_material`
- `test_cli_errors_emit_fixed_codes_and_close_store`
- `test_owner_migration_does_not_load_runtime_configuration`
- `test_input_size_bound_fails_before_json_parsing`
- `test_work_requires_explicit_once`

## `integration/test_postgres.py`

Real PostgreSQL ownership, immutable persistence, revision/cache/lease races, recovery, Maia replay and installed API/CLI operation.

- `test_fresh_and_idempotent_migrations_and_workspace_rebind_refusal`
- `test_restricted_runtime_denies_ddl_authority_and_immutable_record_changes`
- `test_no_cross_domain_database_access`
- `test_startup_checks_workspace_migration_checksums_and_runtime_authority`
- `test_concurrent_first_requests_create_one_shared_cache_and_claim`
- `test_replay_conflicts_and_replacement_do_not_revive_old_caller_revision`
- `test_policy_and_content_revision_changes_reuse_identical_source_query`
- `test_expired_claim_cannot_publish_after_replacement_and_restart_recovers`
- `test_refresh_idempotence_and_failure_retain_immutable_evidence`
- `test_publish_rollback_preserves_last_valid_record`
- `test_read_freshness_is_independent_of_refresh_failure`
- `test_terminal_positions_do_not_queue_provider_or_maia_work`
- `test_snapshot_identity_rejects_changed_input_without_rewriting_history`
- `test_native_maia_digest_and_legal_set_mismatches_are_not_accepted`
- `test_valid_maia_result_replay_survives_newer_acceptance_without_rollback`
- `test_maia_identity_mismatches_reject_without_evidence`
- `test_failed_maia_retry_fences_old_results_and_preserves_terminal_status`
- `test_model_version_mapping_cannot_change_and_removed_profile_cannot_request`
- `test_actual_installed_api_cli_acceptance_without_unrelated_services`
- `test_changed_provider_origin_has_distinct_cache_and_cannot_claim_old_origin_work`
- `test_board_terminal_positions_with_legal_moves_do_not_queue_source_distributions`

## `integration/test_provider_postgres.py`

Real local HTTP against task-owned PostgreSQL: provider/session failures, rate limits, concurrency, cancellation and foreground-read independence.

- `test_postgres_provider_counts_for_both_endpoints_survive_service_restart`
- `test_postgres_provider_429_deadline_survives_restart_without_failure_attempts`
- `test_postgres_late_auth_rejection_cannot_reject_new_registration_even_for_same_token`
- `test_postgres_failed_explorer_refresh_preserves_accepted_explorer_and_maia`
- `test_postgres_one_provider_permit_serializes_lichess_and_masters`
- `test_postgres_provider_http_has_no_checked_out_connection_or_transaction_and_reads_continue`
- `test_postgres_cancelled_http_releases_lease_without_consuming_attempt_and_is_recoverable`
- `test_postgres_lost_private_file_is_unavailable_then_explicit_registration_recovers`
- `test_postgres_rejected_registration_is_not_retried_until_explicit_replacement`
- `test_postgres_invalid_provider_response_is_failed_without_automatic_retry`
- `test_postgres_late_auth_rejection_cannot_replace_explicit_revoked_state`
- `test_postgres_provider_secret_never_reaches_any_owned_durable_table`
- `test_postgres_expired_registration_waits_without_http_or_failure_attempts`
- `test_postgres_provider_outage_stops_after_three_failures_and_explicit_retry_recovers`

## `integration/test_server_postgres.py`

Actual installed HTTP server process, private registration, persisted counts and restart recovery against invocation-owned PostgreSQL.

- `test_installed_server_missing_credentials_explicit_reload_counts_and_restart`

## Repair qualification: `tests/test_worker.py`

Deterministic event-controlled worker/ASGI/CLI lifecycle proof. Blocking fake calls have task-owned release/watchdog cleanup; these are not native PostgreSQL claims.

- `test_worker_without_claim_performs_no_provider_or_failure_operation`
- `test_successful_slice_offloads_database_calls_and_keeps_http_on_event_loop`
- `test_credential_rejection_is_persisted_without_contacting_provider`
- `test_provider_failure_preserves_its_retry_and_authorization_metadata`
- `test_normal_cancellation_releases_claim_without_consuming_failure_attempts`
- `test_cancellation_cleanup_database_error_never_replaces_original_cancellation`
- `test_background_database_latency_does_not_block_event_loop`
- `test_cancellation_drains_inflight_database_operation_before_terminating`
- `test_repeated_cancellation_during_cleanup_waits_and_preserves_first_cancellation`
- `test_database_errors_propagate_without_becoming_provider_failures`
- `test_lifespan_shutdown_with_failed_cleanup_terminates_without_new_slice`
- `test_foreground_asgi_health_completes_while_background_claim_is_blocked`
- `test_lifespan_does_not_close_owned_store_until_inflight_operation_ends`
- `test_cli_single_slice_closes_its_worker_on_success_and_cancellation`
- `test_cancellation_during_executor_join_remains_authoritative`
- `test_repeated_cancellation_during_final_executor_join_preserves_provider_cancellation`

## Repair qualification: `tests/test_maia_validation.py`

New Maia supported-envelope budget contract.

- `test_generated_maia_job_budget_matches_supported_one_gib_worker_envelope`

## Repair qualification: `integration/test_postgres.py`

Native selective retry, command replay/conflict, retained evidence, historical constraints, and exact externally-produced Maia result acceptance.

- `test_request_retry_is_source_local_in_both_cache_orders_and_replays_without_rollback`
- `test_request_retry_advances_multiple_eligible_sources_but_preserves_provider_permit_and_evidence`
- `test_request_retry_with_three_ineligible_sources_is_a_durable_noop`
- `test_retry_inspection_matches_source_state_and_retryable_flag`
- `test_retry_inspection_and_execution_use_each_sources_own_deadline`
- `test_provider_origin_mismatch_is_ineligible_without_blocking_maia_retry`
- `test_legacy_maia_retry_preserves_persisted_constraints_and_refresh_uses_current_budget`
- `test_real_supported_maia_envelope_result_is_accepted_against_exact_persisted_job`

## Repair qualification: `integration/test_provider_postgres.py`

Native cancellation cleanup failure/deadline, lease expiration and fencing, real SQL isolation, and client deadline pool recovery.

- `test_postgres_cancelled_http_with_failed_cleanup_recovers_by_native_lease_expiry_and_fences_stale_publication`
- `test_postgres_advisory_locked_background_claim_keeps_actual_asgi_health_responsive`
- `test_postgres_background_client_deadline_discards_connection_resets_context_and_allows_pool_replacement`

## Repair qualification: `integration/test_server_postgres.py`

Installed server-process lifecycle failure injection, foreground health during background SQL, and shutdown.

- `test_installed_server_shutdown_with_failed_cancellation_cleanup_is_bounded`
- `test_installed_server_health_and_shutdown_remain_responsive_during_background_sql`

The source-free artifact runner executes the complete deterministic inventory on wheel and sdist consumers under Python 3.12 and 3.14, and the complete native inventory on Python 3.12/wheel and Python 3.14/sdist. Repeated cases across consumers are separate qualification executions, not additional regression identities. Fresh Maia inference is a separate recorded disposable-container proof; native tests accept its retained exact exchange without installing a model runtime.

## Additional reviewed worker lifecycle regressions

Owned by `tests/test_worker.py`; these cover executor join and cancellation of application startup/pool closure.

- `test_lifespan_cancellation_during_owned_store_close_drains_and_joins_runner`
- `test_lifespan_startup_cancellation_closes_eventually_created_store_and_runner`

## C analysis-v1 owning suites

### `tests/test_analysis.py`

- `test_C_accepted_alternative`
- `test_C_course_difference_not_blunder`
- `test_C_unknown_not_gap`
- `test_C_actual_opponent_gap`
- `test_C_post_gap_no_cascade`
- `test_C_explicit_reentry`
- `test_C_distinct_game_dedup`
- `test_C_policy_independence`
- `test_C_partial_explorer`
- `test_C_maia_full_legal_set`
- `test_C_engine_played_move`
- `test_C_engine_perspective`
- `test_C_mate_not_cp`
- `test_C_engine_quality`
- `test_C_partial_scan_truth`
- `test_analysis_input_strict_versions`
- `test_source_specific_proactive_gap_and_policy_identity`
- `test_positive_opponent_encounters_supply_distinct_game_denominator`
- `test_worker_session_is_closed_before_durable_failure`
- `test_worker_acknowledged_cancellation_is_not_a_failed_search`
- `test_packaged_analysis_v1_schemas_match_public_models`

### `integration/test_analysis_postgres.py`

- `test_C_duplicate_result`
- `test_C_worker_restart`
- `test_C_newer_input_during_publish`
- `test_C_cancel_resume`
- `test_C_finite_backlog_fairness`
- `test_C_read_has_no_work_effect`
- `test_C_dismissal_revision`
- `test_C_stale_pagination`
- `test_C_auth_workspace`
- `test_C_proposal_not_activation`
- `test_C_partial_scan_truth_native`
- `test_input_immutable_and_partial_rejection`
- `test_native_workbench_producer_contract`
- `test_source_correction_retains_report_and_fences_prior_execution`
- `test_dismissal_exact_replay_after_publication`
- `test_engine_job_builder_cannot_change_captured_root`
- `test_three_additional_games_reopen_durably_across_correction`
- `test_analysis_shutdown_receipt_for_optional_off_assembly`
- `test_retry_insufficient_quality_preserves_results_and_advances_execution`
- `test_engine_opportunity_without_repertoire_color_mapping`
- `test_first_report_cannot_publish_over_a_sibling_run_dismissal`

`C_native_workbench` belongs to the independent Workbench suite. `C_optional_off_study` and `C_actual_AB_consumers` belong to the explicit external pilot integration lane, with actual artifacts required; fixture-only suites do not claim those milestones.

Explicit actual lanes: `integration/test_actual_analysis.py` owns fresh installed B
same-root depth/mate acceptance. `integration/test_actual_a.py` owns actual A export
acceptance into a separate restricted C workspace and bounded B dispatch. They are
selected only with explicit artifact/profile paths; missing selected inputs fail.
The external pilot owns the exact named `test_C_actual_AB_consumers` and
`test_C_optional_off_study` cases. All 28 requested C identities have owning suites;
actual acceptance and synthetic compatibility remain separate executions.

## PR295 repairs

`tests/test_repairs.py` owns named R1 known/unknown frequency, selected actual/
lichess/masters/Maia population, significance/cost, source-order independence;
R2 exact association (none/one/ambiguous/deduplicated/earlier/diverged/re-entry) and
nullable-lineage invariants; R5 version/runtime/implementation compatibility,
completed depth, typed mate, original root/history/restriction checks.

`integration/test_analysis_postgres.py` owns:
- `test_R1_global_cross_category_priority_and_pagination`
- `test_R2_same_color_without_exact_context_has_no_lineage`
- `test_R2_finding_and_dismissal_identity_ignore_selection_order`
- `test_R4_receipt_binds_original_request_digest`
- `test_R5_incompatible_result_releases_lease_and_new_revision_recovers`
- `test_R5_played_cancellation_and_acceptance_fence_preserve_root`
- `test_R5_acceptance_fence_rejects_changed_result_without_consuming_attempt`

Existing result replay/conflict, session-loss, source-correction, dismissal and
stale-generation regressions retain ownership. Native producer output now includes
actual reopen receipts and an unscoped engine finding/dismissal for browser tests.
Actual A/B success requires accepted executions; a typed failure is not successful
artifact integration.

`test_R2_legacy_revision_preserves_identity_until_explicit_replacement` seeds the
reviewed fallback association, verifies dismissal preserves its ID/evidence and
legacy ordering, fences old execution/control/proposal use, then explicitly
replaces the revision without transferring a false attribution's dismissal.
`test_R5_compatible_session_restart_at_played_stage_and_replay_conflict` owns
compatible played-stage loss/recovery and changed replay bytes. The pure legacy
contract case includes the original borrowed-lineage/null-ID engine shape.

Legacy follow-up edges are owned by the same regular native suite:
`test_R2_unpublished_legacy_run_does_not_starve_new_reports` and
`test_R5_legacy_cancellation_reaps_expired_execution_without_new_work`. The legacy
identity test includes explicit dismissal → reopening → publication. These keep
legacy recovery from blocking corrected runs or retaining orphaned capacity.

`test_R3_configured_success_clears_only_current_pending_absence_markers` covers
no-engine → configured bounded success → continuation → explicit no-engine again.
Acceptance clears only the current revision's pending absence markers, preserving
other failures, states, results and other runs.

## C3 accepted game inspection

`tests/test_game_browser.py` owns read-only game paging, complete selected scope,
strict limits, malformed cursors and cursor fencing across runs/revisions.
`test_game_projection_keeps_bounded_reads_in_one_owning_read_transaction` and
the two operations in `test_game_projection_scope_envelopes_retain_the_owner_read_boundary`
retain the same contract after complete SQL queries move to
`tempo_intelligence_database.adapters.games_read_projection`: scope/input/page
parameters remain bound, the one-row lookahead remains bounded, and the caller
owns one read-only transaction. These use fake rows, not a live database.
`integration/test_analysis_postgres.py::test_C3_actual_game_read_is_exact_paginated_and_side_effect_free`
compares responses with actual accepted input, checks authentication/workspace
rejection and proves unchanged run/job progress across reads. The native
Workbench producer exports this real read response for its constructed fixture.

## Games-only read enforcement

`tests/test_games_readonly.py` owns configuration/worker exclusion, authenticated
mutation rejection before storage, workspace isolation and capability discovery.
`integration/test_games_readonly_postgres.py` owns actual read-only enforcement
through both pools even when an internal caller omits the read flag, accepted-game
paging through the real API, bounded DB settings and unchanged source/run/job/
command/profile state. The native `games-readonly` selection also retains the
existing exact accepted-game reader proof. No provider, engine or Study is used.

The Games-only native composition uses an invocation-owned SELECT-only role with
no engine-position privileges. Scope listing/page reads must still work; mutable
Analysis status is not registered in this mode. Pure configuration requires no
operator/worker files and the registered route inventory contains only Games
reads/health. These enforce architecture boundaries, not just hidden controls.
