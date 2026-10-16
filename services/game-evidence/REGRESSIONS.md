# Game Evidence regression ownership

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
