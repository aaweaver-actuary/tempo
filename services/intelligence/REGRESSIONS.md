# Intelligence regression ownership

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
