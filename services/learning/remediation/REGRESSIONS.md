# Remediation owning regressions

## Typed legacy authority intake

`tests/test_legacy_validation.py` owns separate typed authority, no fake Content
facts, exact physical/route/executable binding, complete authored alternatives,
introduction chronology, withdrawal-only source fencing and capture UTC overflow.
The normal module suite discovers it. The native owning case
`test_native_legacy_variant_reuses_receipt_frontier_settlement_and_outer_transaction`
adds outer rollback, response replay, a pre-acceptance attempt fence, shared
qualifying module settlement and invalidation using v3. It is collected but
unexecuted at this checkpoint and does not prove actual normal Study adoption.
That case now also retains the owner scoped proof through actual module storage
when run. `test_owner_scoped_proof_is_retained_without_changing_shared_incident_or_authority_identity`
and `test_scoped_proof_for_another_game_cannot_be_retained_as_this_accepted_incident`
own proof provenance and linkage without new admission/settlement policy.

The installed package suite owns semantic linkage, validity, explicit gameplay
priority, cap/progression exceptions, alias deduplication, protected heads and
ordinary frozen order. `tests/test_validation.py`, `test_policy.py`, and
`test_boundary.py` are discovered by the registered owning command.

The native PostgreSQL suite owns receipt/obligation atomicity, rollback, response
loss and replay, concurrent duplicates, source correction/withdrawal, bounded
scanning, immutable attempt frontiers, late imports/unknown times, clean-only
settlement, and miss-arrival versus review-completion races. It invokes actual
installed module code inside explicit outer transactions with Learning credentials.
Admission callback fixtures prove the library seam, not production A/F adoption.

`test_native_new_miss_lookup_does_not_scan_past_review_history` uses actual
PostgreSQL EXPLAIN ANALYZE against 1,000 historical review pairs. It owns the
parameterized frontier-before-settlement lookup and bounds visited history rows;
the final plan JSON is retained with native evidence. Flattening this join
previously visited 1,001,000 history rows and failed the unchanged bound.

## PR #281 independent review repairs

- `test_native_current_publication_review_reports_semantic_settlement`: a current
  generation/reference review truthfully settles an accepted miss for the same
  immutable revision; committed receipts and exact replay preserve one effect.
- `test_native_new_card_revision_does_not_settle_old_semantic_work`: canonical
  card identity cannot cross exercise revisions; a qualifying marker is retained.
- `test_native_equivalent_publication_reanalysis_keeps_satisfied_incident_without_authorizing_stale_attempt`:
  completed work, its receipt and marker survive equivalent reanalysis.
- `test_eligible_miss_duplicate_preserves_priority_and_route_in_either_order`,
  `test_ineligible_miss_duplicate_never_displaces_eligible_ordinary_route`,
  `test_miss_duplicate_cannot_replace_protected_issued_head`, and
  `test_eligible_ordinary_duplicates_keep_first_route_and_frozen_card_order` own
  eligibility-first route selection, miss priority, protected heads and stability.
- `test_wire_valid_occurrence_utc_overflow_is_an_explicit_terminal_failure`,
  `test_normal_offset_occurrence_preserves_its_utc_instant`, and
  `test_invalid_trusted_receive_time_is_not_reclassified_as_a_producer_failure`
  distinguish producer range failures from valid instants and invalid caller state.
- `test_native_occurrence_utc_overflow_returns_terminal_failure_without_acceptance`
  proves the receiver returns an honest failure without durable acceptance.
- `test_native_same_frontier_nonsettling_history_lookup_is_bounded` records a
  second EXPLAIN plan for 1,000 matching-frontier nonsettling attempts and guards
  against a quadratic scan. The original lower-frontier bound remains unchanged.
# F1 runtime interface extensions

`tests/test_policy.py` owns smaller preparation bounds, one-card cursor advancement
and SQL-alias validation. `../../../database/tempo_remediation_database/tests/native/test_postgres.py` additionally owns
`test_native_single_card_page_advances_past_rejected_rows_without_skipping_pending`
and `test_native_correlated_queue_predicate_matches_repository_and_settlement`.
The original frontier-first query-plan checks and their row limits are unchanged.

## PR #287 stale transport identity

`test_native_stale_transport_receipt_survives_reconnect_and_rejects_changed_identity`
owns native stale persistence, fresh-connection replay, immutable stale status,
changed-payload conflict and acceptance of the correction under a new ID.
`test_native_old_withdrawal_or_review_cannot_clear_corrected_revision` retains its
original revision/frontier assertions and now additionally rejects reuse of its
stale withdrawal transport. The valid corrected withdrawal uses a new delivery ID;
its exact target revision and resulting lack of outstanding work remain asserted.
The original frontier-first EXPLAIN bounds are unchanged.

`test_native_stale_or_forged_supported_miss_is_not_accepted` retains all rejection
outcomes and now explicitly proves zero evidence, contributions and workspace
authority. Its previous zero-receipt assertion is superseded for stale rejection
by the required durable transport receipt; terminal identity failures still write
no receipt. No acceptance assertion is removed.

## Legacy owner read projection checkpoint

`test_owner_projection_uses_legacy_columns_without_forking_qualifying_review_predicate`
and `test_owner_current_miss_projection_rejects_sql_fragments` own the typed
column adapter while preserving current-game/frontier/settlement checks.
`test_native_owner_projection_keeps_replay_prior_attempt_fence_and_invalidation`
is the owning PostgreSQL proof for alternate columns, receipt replay, stale card
revision, prior-issued frontier, later settlement and invalidation. It remains
unexecuted at the first bridge checkpoint; it is not legacy HTTP adoption proof.

`test_scoped_policy_exclusivity_cannot_be_bypassed_by_a_forged_miss` requires the
trusted complete prefix-policy proof's selected-policy anchor at or before the
encounter. A foreign route ending exactly there vetoes acceptance even when
absent from executable candidates. Ending earlier permits later separation.
