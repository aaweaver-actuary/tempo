# Controlled browser regression ownership

This feature owns `unit/*.test.ts`, collected by `vitest.config.ts` and executed by
`node tests/controlled-browser/test-unit.mjs`. This local discovery is authoritative;
the central `controlled-browser` lane is a validated compatibility index and
delegates execution to that same runner/configuration.

The preserved donor ledger is `../fixtures/controlled-browser/pilot.json`; it records
all original/new file/title/project identities, exact assertions/actions, fingerprints,
and retained full-stack/offline owners. The normal browser runner collects and
accounts its nine native identities plus the three titles in `visual.spec.ts`.

Named families in the owning unit suite:

- `controlled_browser_supported_aliases_use_the_owned_runner_without_setup_hooks`,
  `controlled_browser_registered_owning_suite_lists_without_setup`, and
  `controlled_browser_listing_and_rejected_browser_selections_do_not_provision`:
  actual command registrations, guarded listing and rejected selections cannot
  build, allocate output or start a service.
- `controlled_browser_discovery_rejects_missing_duplicate_and_extra_registration`,
  `controlled_browser_central_dispatch_uses_dedicated_configuration_and_one_sanitized_build`,
  `controlled_browser_unit_accounting_rejects_missing_duplicate_skipped_and_incomplete_selected_cases`,
  and `controlled_browser_invalid_unit_file_selection_fails_before_build`:
  local ownership, dedicated dispatch, one JS prerequisite, excluded environment,
  exact selected cases, and pre-build invalid file rejection. The alias and missing
  lane regressions failed against the merged implementation before integration.

- `controlled_browser_unexpected_api_request_fails_without_live_fallback` and
  `controlled_browser_rejected_traffic_is_fatal_even_if_application_catches_*`:
  origin/method/path/query/body mismatches, duplicate query fields, malformed bodies,
  asset escape and swallowed request errors fail.
- `controlled_browser_websocket_attempt_is_closed_and_fails_traffic_assertion`:
  an undeclared WebSocket is closed and fails the owning test.
- `controlled_browser_context_teardown_fences_native_polling_before_releasing_interception`:
  real Chromium polling cannot reach the host during context teardown. A forced
  unroute-first scheduling window reproduces the former late API escape.
- `controlled_browser_local_host_preserves_product_mode_and_contract_errors`,
  `controlled_browser_child_environment_excludes_proxy_service_and_node_injection`,
  and `controlled_browser_missing_or_changed_real_asset_fails_instead_of_substituting`:
  local production mode, environment exclusion, real pinned input and host boundaries.
- `controlled_browser_collection_matches_nonzero_exact_request`,
  `controlled_browser_invalid_or_empty_selection_fails`,
  `controlled_browser_result_accounting_rejects_invalid_missing_duplicate_and_retry_reports`,
  and `controlled_browser_movement_preserves_exact_case_inventory`: exact nonzero
  collection/results and literal-sensitive original proof preservation.
- `controlled_browser_runner_cleans_owned_resources_after_*` and
  `controlled_browser_owned_port_closes_and_cannot_be_reused_as_a_live_server`:
  package/build/startup/browser/report/API/cancellation failures retain evidence and
  remove only owned outputs/ports. Injected runner stages are unit failure proofs;
  the normal browser runs separately prove real builds and interactions.
- `controlled_browser_review_receipt_uses_original_intent_and_authorized_continuation`,
  `controlled_browser_delayed_response_waits_for_explicit_release`, and
  `controlled_browser_legacy_fixtures_validate_current_consumers_without_python`:
  honest pending/failure/completed legacy receipts, original identity and current
  frontend response shape without executing a producer.
