# Game Evidence regression ownership

`tests/test_api_storage_boundary.py` owns readiness delegation to the existing
store's schema-access capability and unchanged unavailable-storage HTTP
translation, without opening a connection in HTTP composition. Existing
`test_analysis_packing.py`, `test_analysis_deviations.py`,
`test_analysis_schema.py`, `test_providers.py`, `test_credentials.py` and
`test_boundary.py` retain their policy, input, authentication and installed-import
assertions after SQL extraction into the root database owner tree. Native storage
proofs remain in `integration/test_postgres.py`; no native proof is implied by
these unit cases.

`tests/test_application_preparation.py` owns import preparation and read-response
shaping after their mechanical move out of the root database store: cheap input
guards, unchanged replay before normalization/writing, normalization between the
reader and writer scopes, original error documents, response object identity,
pending fallback and source-revision-before-snapshot readiness precedence.
`test_invalid_snapshot_does_not_replace_existing_worker_cache` complements
`test_C3_worker_reuses_only_byte_identical_validated_snapshot`: validation failure
retains the prior per-worker tuple, while digest/document changes still invalidate
it. These are application/installed-wiring checks, not native transaction proof.

`tests/test_reporting.py` owns comparison construction and import-status response
shaping: unchanged source/authority readiness precedence and fact identities,
timestamp/missing-result handling after closing the read, and report construction
only after the callback, locked reads and current-input fences when no comparison
already exists. It retains the original publication order with synthetic facts;
the native suite still owns actual locks, rollback and publication races.

`tests/test_snapshot_preparation.py` owns the snapshot preparation split: invalid
input precedes connection acquisition; exact replay and immutable identity conflict
retain their early returns/errors; staleness rejection precedes position access
and route compilation; affected positions retain prior parse counts/route order,
with no source-list mutation. Synthetic installed-store traces retain the locked
read and write call points, including rejection without writes. Native snapshot
installation/generation, concurrency and rollback proof remains with the existing
PostgreSQL regressions.

`tests/test_legacy_payloads.py` owns offline initial-v3 production from a scoped
legacy observation: exact original physical card/interior offset, played/expected
move, deterministic immutable identity, unknown/no-miss without an envelope,
mixed source rejection and a no-store/non-overwriting CLI. Existing v2 payload
and correction/withdrawal tests in `test_comparison.py` retain their owner.

`tests/test_legacy_scoped_authority.py` owns the schema2 legacy-local proof:
complete 4,102-row traversal with one retained candidate and preserved prior
policy anchor; foreign/unbound competitors after4,096; incomplete/mixed authority
rejection; no global-complete label; exact game/time/decision scope; unchanged
physical execution and uncertain introduction chronology. It normalizes through
the same comparison/projection as existing activated-publication v1/v2.

`tests/test_decision_projection.py` owns a learner miss inside an unchanged
physical executable, exact expected move/position, conflicting projected identity
and incorrect offset rejection. `tests/test_analysis_deviations.py` also requires
existing projected content to remain unknown without proven re-entry authority.

`tests/test_analysis_schema.py` owns offline immutable manifest/page integrity,
replay, mixed input/workspace/version rejection, missing pages/counts, corruption
and non-overwriting output. Learning's regular `test_authoritative_bridge.py`
owns producer-created snapshot v2 and analysis fixtures from genuine activation,
covered/opponent-deviation denominator contexts and proposal consumption.
`tests/test_bridge_snapshot.py` owns accepted v2 source/route/policy generation
and reconstructed response-scope rejection using a genuine installed synthetic
fixture. `integration/test_postgres.py` additionally owns interrupted frozen-export
resumption after a game revision changes, exact cursor scope and immutable-table
runtime update denial. The installed proof exports the existing outbox inspection
and genuine acknowledgements, without introducing another remediation producer.
`tests/test_providers.py` retains JSON datetime round trips when version validation
is shared with provider inputs.

`tests/test_credentials.py` owns private Learning credential-file validation.
The Learning runtime's regular `test_game_evidence_runtime.py` owns actual bearer
delivery, separate database credentials, lost acknowledgement/restart and producer
shutdown with continued studying. This complements the component's strict-receiver
and library fixture proofs; it does not replace them or claim full Study UI adoption.

The installed package's regular `tests/` suite owns legal PGN replay, precise ply
and board side, alternatives versus misses, route ambiguity, Italian/Ruy separation,
shared membership, explicit transposition re-entry, repeated occurrences, custom
FEN, castling, en-passant, promotions, unknown acceptance, invalid provenance,
source bounds and provider cursor/rate-limit behavior.

`integration/test_postgres.py`, executed by `integration/run_postgres.py`, owns
atomic comparison/outbox publication, rollback, duplicate sources, verified-index
reuse, stale source/snapshot fencing, pending/completed/rejected/stale delivery,
lost acknowledgement with fresh-process replay, durable blocked corrections,
exact v2 correction/withdrawal, partial-snapshot non-withdrawal, stable bounded
evidence scopes, source reversion, bounded resumption and durable rate-limit state,
real database restart, runtime-role restrictions, read API isolation and provider
source checkpoint/cancellation. These cases run on a newly provisioned component
database. They never accept a live database URL.

`integration/qualify_artifacts.py` installs built wheel and source distributions
in clean Python 3.12/3.14 consumers and runs the same regular tests. No additional
global qualification framework or root regression inventory is required for this
independently discovered component.

## PR #283 correctness repairs

Named regular native regressions include:

- `test_rejected_replacement_reconciles_exact_accepted_revision` and
  `test_rejected_withdrawal_cannot_erase_missing_successor`: exact accepted targets
  and recovery without a new source/snapshot revision after the receipt.
- `test_real_remediation_reconciles_rejected_or_accepted_intermediate` and
  `test_actual_receiver_lost_response_has_one_contribution`: actual installed
  Learning validation/repository effects, separate fixture database, fresh processes.
- `test_multiple_source_changes_while_waiting_use_latest_desired_state`,
  `test_multi_bucket_correction_preserves_other_scope_obligations`, and the existing
  partial-snapshot/rollback/race tests retain correction and ownership invariants.
- `test_nonintersecting_snapshot_refresh_converges_without_replay` and
  `test_snapshot_change_mid_rescope_and_import_converge_after_restart`: full bounded
  workspace convergence, including custom FENs, empty routes and new imports.
- `test_malformed_provider_checkpoint_refetches_valid_page`,
  `test_saved_month_validates_later_slice_and_recovers_after_cancel`, and
  `test_oversized_provider_response_stays_explicitly_blocked`: checkpoint validity.
- `test_rate_limit_covers_other_accounts_without_starving_other_provider`,
  `test_rate_limit_deadline_never_shortens_and_extends`, and
  `test_concurrent_provider_workers_own_one_request_after_cooldown`: provider authority.
- `test_database_restart_preserves_reconciliation_cooldown_and_valid_checkpoint`
  and `test_version_one_migration_recovers_completed_history_in_bounded_pages`:
  database durability, ordered forward migration and bounded receipt reconstruction.
- `test_expired_provider_worker_cannot_replace_newer_valid_checkpoint`,
  `test_late_rejection_from_expired_sender_cannot_undo_completed_receipt`,
  `test_deferred_game_does_not_starve_other_game_delivery`, and
  `test_invalid_historical_completed_receipt_blocks_projection_recovery`:
  adversarial ownership, acknowledgement order, fairness and diagnostic blockers.

The regular unit suite owns canonical UTC windows, offset/month/year transitions,
366-day bounds, naïve-time rejection, Retry-After HTTP dates and half-open bounds.
`test_equivalent_windows_share_persisted_identity_and_utc_month` proves durable
identity deduplication and archive selection. Test-only Learning dependencies are
development inputs; production imports remain confined to shared contracts.

## PR #287 authentication recovery

`tests/test_credentials.py` owns
`test_work_with_learning_endpoint_requires_private_credential_before_mutation`
(missing configuration/file, invalid value and shared permissions),
`test_work_without_learning_endpoint_remains_producer_only`, and
`test_direct_delivery_missing_credential_never_claims`.

The regular native suite owns
`test_learning_401_retries_original_delivery_after_credential_correction`,
`test_retry_auth_delivery_recovers_only_legacy_401_block`,
`test_retry_auth_delivery_rejects_other_states_and_active_leases`, and
`test_auth_retry_preserves_predecessor_and_acknowledgement_fences`.
Existing strict receiver calls and subprocesses use an explicit synthetic private
credential, retaining production preflight. No existing replay/withdrawal/lease
assertions are removed. Learning's native suite separately exercises actual
installed CLI -> authenticated Learning HTTP -> independent databases.

## PR #294 snapshot repair

`integration/test_postgres.py::test_A_snapshot_installation_orders_preserve_current_generation` runs both historical-first and current-first loading against real PostgreSQL, using actual installed producer fixtures. It verifies immutable replay, current pointer, stale rejection and comparison progress on generation 2. Existing mixed-generation/response-scope and evidence-withdrawal regressions remain unchanged.

`test_A_post_retirement_snapshot_cannot_claim_current_serving` installs a genuine retired observation, recomputes comparison readiness and preserves the original snapshot and delivery records.

## C3 mapping uncertainty

`tests/test_bridge_snapshot.py::test_C3_unknown_historical_mapping_cannot_become_a_repertoire_miss`
requires historical uncertainty to remain unknown even for an otherwise valid
activated publication. Unbound authored replies also make response scope
incomplete during the independent v2 snapshot reconstruction.

C3 native rerun also executes the existing provider replay, cooldown, malformed
source, custom FEN, authority, durable delivery and immutable export regressions.
The private cohort is separate from these constructed edge cases. A reconstructed
PGN digest identifies the supplied local source bytes, not unavailable original
provider PGN bytes.
`test_C3_worker_reuses_only_byte_identical_validated_snapshot` bounds the worker
cache to one validated snapshot and invalidates it on either identity or byte
change. Publication still rechecks workspace epoch, game revision and lease.
The real cohort exposed repeated full-snapshot validation near the 30-second
lease bound; no lease or timeout was increased.

C3 existing-position/re-entry uncertainty:
`test_C3_existing_authored_position_does_not_become_a_new_content_gap` reproduces
an uncovered ordered path arriving at an existing same-policy authored position.
That is unknown re-entry authority, not evidence for a new card. Other policies
cannot suppress a genuine selected-policy gap. The observation retains existing
Content definition IDs and normalizes legal en-passant identity without granting
re-entry. The independent real cohort audit exposed this case before report
acceptance.

`test_C3_export_derivation_upgrade_preserves_reserved_original_bytes` owns the
export identity transition: new exports use the current derivation (schema remains v1), while
existing directory reservations retain their original immutable manifest/pages.
A correctness repair never rewrites an accepted input under its old identity.

`tests/test_analysis_packing.py` owns C3 byte-aware immutable page packing:
64 remains the item ceiling, 8 MiB remains the page ceiling, and 64 MiB remains
the complete-export ceiling. UTF-8 payloads split without losing observations;
individual oversized records and oversized complete exports remain errors.
`test_C3_dense_80_game_export_keeps_scope_and_closes_source_transaction` owns the
native 80-game traversal, complete encountered reply inventories, exact selected
scope, offline verification, and release of the source read transaction before
chess traversal. Derivation 3 exports only inventories for opponent positions
actually encountered in the selected games; every scope and reply at those
positions is retained. Definitions, routes, games and observations are retained.
Previously reserved exports keep their original bytes and identity.

## Observed legacy authority checkpoint

`test_legacy_validator_accepts_equivalent_execution_root_with_original_card_bytes`
and `test_legacy_execution_equivalence_cannot_relax_source_or_binding_authority`
separate legal execution equivalence from exact route/catalogue source equality.
Original physical FEN/moves/ID/revision remain the comparison evidence; meaningful
rights, wrong offsets/executables and route source changes fail closed.
`test_legacy_validator_rejects_counter_selected_root_on_repeated_position`
requires a unique ordered executable root across the route. Introduction-time
eligibility is unchanged and remains independent of current authored conformance.

`tests/test_legacy_authority.py` owns the synthetic physical black-card mapping
with opponent prelude, exact executable/revision and interior offsets. The
competing Italian catalogue keeps the shared prefix unknown. Controls cover
stale/incomplete/unready/unintroduced observations, early policy anchors,
re-entry, mixed offsets/color/executable/target, shared/duplicate cards, missing
routes, digest/workspace mismatch and no-miss/unknown games. The publication
loader must reject this separate comparison input; there is no delivery or
activation assertion. Existing v1/v2 comparison/fixture tests retain their owner.

`test_legacy_unmapped_authored_alternative_cannot_become_a_physical_card_miss`
owns the P1 shared-policy alternative regression: a valid unbound `...Nf6`
branch must veto a physical `...a6` miss. A different played move also stays
unknown when physical coverage is incomplete; the exact `...a6` match remains
mapped. Authored move authority is retained separately from card eligibility.
The regression fails with `missed == unknown` against checkpoint `88a1fdf`.

`test_ended_foreign_route_requires_later_exclusive_policy_anchor` pairs an
Italian route ending at the proposed miss with one ending a ply earlier. The
first stays unknown without an exclusive anchor; later genuine separation can
still produce the original physical-card miss.

## Database relocation fingerprints

`tests/test_boundary.py::test_native_source_fingerprints_cover_relocated_database_inputs_and_hooks`
evaluates the native report's two input-map expressions against synthetic files.
It retains existing service-relative keys and proves that changes to either the
Game Evidence or remediation database adapter, migration and build hook change
the corresponding fingerprint. It does not import or execute the native runner.
The existing regular-suite count mismatch remains disclosed in
[focused relocation evidence](../../database/VALIDATION.md#pr334-reference-audit-repair);
collection and this one passing regression do not establish native qualification.
