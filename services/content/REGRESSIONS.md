# Content regression ownership

The existing bridge source-capacity proof now follows the public
`SourceSnapshot.exercises` ceiling of 10,000 rather than the former 8,192
implementation guard. `test_complete_9612_definition_source_fits_existing_public_capacity_without_truncation`
checks complete binding/definition preservation through scope validation and
draft preparation; `test_complete_10001_definition_source_rejected_by_both_public_bridge_paths`
checks both public entry paths reject oversized input before storage. These are
constructed input-boundary tests, not actual graph import or PostgreSQL evidence.

Native test paths below are repository-root paths. The [native suite](../../database/tempo_content_database/tests/README.md) retains Content ownership; its [runner and benchmark](../../database/tempo_content_database/tooling/README.md) are centralized alongside it. Historical validation receipts retain their original paths.

`tests/test_execution_projection.py` owns original full-executable preservation
with an explicit opponent prelude, invalid prelude rejection, exact interior
decision reconstruction and absent-field byte compatibility. The installed
cross-component owner is Learning's
`test_C3_original_opponent_prelude_card_receives_interior_miss_and_settles_once`.

## A bridge

`tests/test_bridge.py` owns source/rename/permutation mapping, explicit provenance
and color, legal executable proposals, informational-context rejection, and the
accepted-alternative board-copy regression. `database/tempo_content_database/tests/test_postgres.py` owns
the named `test_A_*` approval-not-persisted, restart/replay, cancellation orders,
source reservation/CAS, incomplete count/digest manifest, and conflicting-page
replay cases. Existing source concurrency, failed-generation preservation,
retirement-required, immutability and foreign-database tests remain selected.
The actual installed cross-service producer/receipt proof belongs to Learning's
regular `test_authoritative_bridge.py`, not a fabricated Content fixture.

Content's regular installed `tests/` suite owns strict authored-input validation,
legal-state canonicalization, both colors, castling/en-passant, promotions,
terminal positions, complete reply identities, shared/transposed position reuse,
policy separation, stable card/decision identity, permutation determinism and
immutable publication contract behavior.

`tests/test_packaging.py` owns the named independent-distribution, packaged
migration-manifest, and import/CLI-without-service-initialization regressions.
These same tests execute against source-free installed wheel and sdist consumers;
they do not rely on a source checkout or ambient `PYTHONPATH`.

`database/tempo_content_database/tests/test_postgres.py`, executed only by the owned disposable native
runner, owns real migration/grants, persistence/restart, interrupted bounded
reconciliation, semantic replay, stale leases/generations, concurrent publication,
structural edits, shared membership safety, orphan cleanup/restart, failed
publication preservation, validation rollback and foreign database denial.
Deletion must retain a durable collector, and deletion/reconciliation must obey
one lock order. These substantive donor #117 invariants remain native regressions.

Named donor replacements:
`test_deletion_leaves_durable_bounded_collector_and_cleanup_resumes` and
`test_source_and_task_lock_order_serializes_deletion_versus_inventory`, with
both `inventory` and `deletion` first-holder schedules. Related native owners
include `test_orphan_collection_between_route_read_and_registration_is_fenced`,
`test_registration_holds_artifact_lock_collector_yields_without_lost_wakeup`,
`test_active_source_lease_does_not_starve_unscoped_work_for_another_source`, and
`test_runtime_readiness_rejects_drifted_grants_and_owner_migration_repairs_them`.
The installed subprocess acceptance owner is
`test_complete_source_cli_acceptance_restarts_and_preserves_both_publications`.

`integration/test_consumers.py` and its standalone `consumer_probe.py` own actual
installed Learning/Game Evidence adapter compatibility, complete executable kinds,
unchanged publication identities, source/learner-color provenance, null activation,
and the explicit rejection of a 129-fact current Learning bundle. Consumer
implementations remain test-only inputs and are absent from Content's production
dependency closure.

`run_suite.py` records independent collection and execution inventories and
requires identical nonzero unique test identities, passed setup/call/teardown,
and matching successful JUnit cases. The artifact qualifier applies the same
guard to Python 3.12/3.14 wheel/sdist consumers and proves pytest's zero-selection
exit separately. No selected skip, deselection or absent native proof is a pass.

The Content entry in `components.json` is the regular component discovery owner;
this local inventory replaces a duplicate root regression list for Content.
Source-bound results and any remaining gaps are recorded in `VALIDATION.md`.

## PR #288 correctness repairs

[Root inventory cursor unit tests](../../database/tempo_content_database/tests/unit/test_inventory_cursors.py) own canonical round trips, every missing field,
strict primitive types, reader/workspace/source identity, malformed/legacy tokens,
route composite keys and obsolete revision/generation rejection. These execute in
every Python 3.12/3.14 wheel/sdist consumer.

Native owners in `database/tempo_content_database/tests/test_postgres.py`:

- R1: `test_coverage_snapshot_survives_generation_replacement_and_collection`
  pauses after the G1 occurrence, proves G2 completion and committed G1 membership,
  occurrence and artifact deletion, then requires the covered G1 reply and metadata.
- R1: `test_inventory_cursor_rejects_changed_source_generation` and
  `test_inventory_cursor_rejects_malformed_and_cross_source_tokens`, both readers;
  `test_inventory_cli_rejects_invalid_cursor_as_structured_error`;
  `test_coverage_rejects_missing_mandatory_complete_inventory` (six faults);
  `test_inventory_read_snapshot_is_read_only_and_rolls_back_on_error`.
- R2: `test_stale_revision_is_rejected_before_any_input_staging` (installed CLI,
  distinct payloads and actual database counts),
  `test_identical_source_replay_with_old_expected_revision_is_idempotent`,
  `test_unknown_source_wrong_initial_revision_rejects_without_staging`, and
  `test_concurrent_distinct_source_edits_admit_only_one_revision` (both writers
  staged before final admission; winner-only source/publication).
- R3: `test_unscoped_repertoire_lookup_rejects_ambiguous_source_keys` includes
  installed CLI stderr/exit assertions and distinct scoped stable identities;
  `test_unique_and_missing_repertoire_lookups` owns zero/one cases.
- R4: `test_runtime_readiness_rejects_missing_required_grant_and_owner_repairs_it`
  revokes input INSERT, task UPDATE and response SELECT individually, restores
  grants in finally, and proves idempotent owner repair without ledger changes.

The original donor lock-order, collector, interrupted-stage, stale-worker,
publication, legal-reply, policy and consumer owners above remain selected.

## PR #294 repair regressions

Regular native `database/tempo_content_database/tests/test_postgres.py` owns:

- `test_A_terminal_learning_rejection_releases_source_reservation`: durable failure, current source revision, corrected approval, restart and replay after successor reservation.
- `test_A_terminal_outcomes_cannot_overwrite_each_other` and `test_A_terminal_failure_and_corrected_approval_serialize`: immutable outcomes and independent-session races.
- `test_A_concurrent_proposal_identity_is_unique_across_sessions` and `test_A_response_proposal_concurrency_and_restart`: identical/conflicting concurrent requests, actual proposal validation and restart.
- `test_A_proposal_unique_index_rejects_legacy_duplicates_without_deletion`: actual owner migration from pre-0004 history, duplicate refusal with rollback preserving both drafts, incompatible readiness, successful upgrade after fixture-only duplicate cleanup, immutable draft replay and unchanged checksum history on migration replay.
- `test_A_semantic_draft_collision_rejects_changed_raw_provenance`, `test_A_exact_draft_replay_returns_original_immutable_document`, and `test_A_approved_draft_cannot_be_relabelled_by_reimport`: exact immutable provenance.

Regular installed pure `tests/test_bridge.py::test_A_terminal_failure_requires_original_authenticated_request` covers terminal state and request/workspace/command/operation mismatches, and safe failure details. Real authenticated rejection and corrected activation are owned by Learning's installed `test_A_corrected_source_activates_after_original_terminal_failure`.

`test_A_success_and_terminal_failure_race_preserves_one_outcome` owns concurrent competing terminal records; `test_A_proposal_identity_is_scoped_to_workspace_and_source` owns permitted source scoping.

`test_A_proposal_insert_rollback_allows_legitimate_retry_after_restart` owns aborted insertion and durable retry. Pure `test_A_failure_transport_rejects_uncertain_http_outcomes` uses real local HTTP for redirect/authentication/unavailability/malformed/oversized boundaries. Terminal request-binding parameters include retirement and wrong original origin.

## PR #294 pre-activation recovery

Native `database/tempo_content_database/tests/test_postgres.py` owns:

- `test_A_unsupported_bridge_exercise_does_not_authorize_application` and
  `test_A_general_content_explanation_remains_publishable`: A rejection without
  source admission, corrected approval, and general v1 explanation publication.
- `test_A_preflight_rejection_cannot_race_into_applying`: independent callers
  observe the same frozen failure and no admitted source.
- `test_A_known_pre_delivery_failure_releases_source_without_remote_receipt`:
  exported but uncheckpointed abandonment, retained revision, restart and exact
  replay after successor reservation.
- `test_A_pre_activation_failure_recovery_survives_restart`: stale preparation
  waits after immutable staging; failure and optional successor approval win
  before its final source admission, which must be rejected.
- `test_A_undelivered_failure_and_checkpoint_serialize`: both actual PostgreSQL
  lock-winning orders, with only one persisted authority.

Pure installed `test_A_bridge_scope_preflight_is_local_and_preserves_general_types`
checks explanation, open judgment, missing/terminal bindings and alias collisions.

`test_A_completion_receipt_requires_exact_original_authority` shares success/failure
workspace/family/request/publication/generation binding. The real installed
`test_A_installed_completion_cannot_substitute_checkpointed_request` completes a
different publication's retirement under the same remote ID and proves that both
reconcile and abandon retain the original Content reservation and serving content.

## C3 authored scope and historical uncertainty

Regular `tests/test_bridge.py::test_C3_authored_reply_without_executable_binding_remains_unknown`
reproduces an authored reply incorrectly exported as uncovered and requires an
unknown, incomplete scope. Game Evidence independently reconstructs this rule.
The import manifest can explicitly mark normalized exercises with unresolved
historical mapping; these definitions cannot establish existing-card misses.
`test_C3_preparation_persists_packed_export_and_public_operation_rehydrates` is the
native storage regression: it failed on the original inline checkpoint and proves
both actual packed persistence and exact public export/replay after the repair.

## Root database bridge extraction

`tests/test_bridge_storage_boundary.py` owns semantic approval facts and source-scope-before-row-check ordering on the same opaque borrowed transaction, plus unchanged CLI/readiness configuration. The [root pure storage guards](../../database/tempo_content_database/tests/unit/test_storage_guards.py) own readiness connection-context composition. The existing bridge/export, execution projection and cursor assertions remain. Bridge public composition now lives in `src/tempo_content/bridge/__init__.py`, application logic in `bridge/application.py`, and concrete SQL in `database/tempo_content_database/adapters/bridge.py` at repository root. The installed migration-resource regression follows that root database package's `ddl/migrations`, preserving all manifest/checksum assertions.


## Source application helper separation

`tests/test_source_policy.py` covers source/workspace admission, exercise turn checks, binding responses, publication status/error selection and coverage labels over supplied facts. Its import guard keeps storage, network and sibling implementations out of the pure policy module. SQL, locks, fencing and hook timing remain in the existing root database store; these synthetic cases do not replace native contracts.

## Reconciliation orchestration separation

`tests/test_worker_application.py` owns the moved `work_once` orchestration's ordered claim/idle-collection/manifest/phase operations, unchanged manifest exception scope, domain and generic validation errors, unknown phase, stale authority, and propagation of unexpected failures. [Root pure storage guards](../../database/tempo_content_database/tests/unit/test_storage_guards.py) require the original single transaction and fence before failure-write/error-text evaluation; the public `ContentService.work_once` composition is exercised without opening a database. Existing native worker, lease, lock-order, collector, rollback and failed-generation contracts remain with `database/tempo_content_database/tests/test_postgres.py`; synthetic cases do not establish actual PostgreSQL locking or resource isolation.

## Route and publication preparation separation

`tests/test_content_preparation.py` owns the moved 16-ply/terminal route-window decision, completed-cache reuse, exact publication/provenance fields, exercise-count rejection before construction, retained serialization/decode pass, inventory retirement/status/stale projection, and route/publication computation at the original read/hook/transaction points. Existing selected `tests/test_domain.py` checkpoint, illegal-route, publication identity/round-trip/permutation and retirement cases retain the underlying algorithm/contract invariants. Native epoch/fence/cache races and publication authority/rollback/replay contracts remain in `database/tempo_content_database/tests/test_postgres.py`; they were not replaced by synthetic timing tests.

## Complete binding phase separation

`tests/test_binding_application.py` owns the full moved phase: exhaustion without reads, ordered binding/target/exercise reads, original hook/policy order, missing/incomplete target and missing exercise handling, hook failure before writes and public phase composition. The [root pure storage guards](../../database/tempo_content_database/tests/unit/test_storage_guards.py) own target-query parameters/missing-route short circuit and require the same transaction for fence, response insert, cursor advance and `after_writes`, with rollback on a stale fence or failed after-write hook. Existing `tests/test_source_policy.py` binding completion/position/policy/accepted-response cases remain selected. These checks establish application/composition behavior, not actual native locking, leases or rollback; regular `database/tempo_content_database/tests/test_postgres.py` retains those contracts.

## Complete route phase separation

`tests/test_route_application.py` adds only the missing moved-boundary cases: exhaustion without reads, valid/invalid new-artifact registration ordering and epoch-race result mapping. The existing `tests/test_content_preparation.py` route-window/cache-reuse/compute-hook cases and `tests/test_domain.py` checkpoint/illegal-route cases retain the underlying proof. The compute-hook test's monkeypatch follows the moved call in `worker.routes`; its event assertions are unchanged. One-time exact source comparison covers the full copied phase and both atomic write sections. Existing native artifact/collector/epoch/fence/lock-order contracts remain in `database/tempo_content_database/tests/test_postgres.py`; synthetic checks do not replace them.

## Complete exercise phase separation

`tests/test_exercise_application.py` adds four missing moved-boundary cases: exhaustion without reads, model parsing before dependent reads/hooks, and continuation/completion invocation order and result mapping. Existing selected `tests/test_source_policy.py` learner-turn and `tests/test_domain.py` 16-ply JSON checkpoint/resume and legality checks retain the underlying computation proof. One-time exact source comparison covers the complete moved phase and original atomic identity/predecessor/revision/write section. Native identity/fence/replay/rollback contracts remain in `database/tempo_content_database/tests/test_postgres.py`; they were not replaced by a new exhaustive phase test campaign.

## Publication build/admission workflow separation

`tests/test_publication_application.py` adds four missing workflow cases: build replay before task-state/build work, incomplete and empty-source preconditions before reservation/hydration, and replay admission still calling the original atomic guard. Existing selected preparation and publication contract/identity/permutation/retirement cases remain; the two preparation monkeypatches follow the moved calls into `publication.application` with unchanged function/assertion bodies. One-time exact source comparison covers both complete workflow bodies and original native reservation/immutable-insert sections, including the two invocation sites sharing one fixed payload SELECT implementation. Native concurrent publication/replay/provenance/current-generation/rollback contracts remain in `database/tempo_content_database/tests/test_postgres.py`; they were not replaced by a new exhaustive campaign.

## Source staging/admission workflow separation

`tests/test_source_application.py` adds four missing boundary cases: preflight replay skips staging/seal/hooks/admission, ordered 64-item pages before sealing and the staging hook, normal versus in-transaction replay receipts, and staging-hook failure before admission. Existing selected source-policy cases retain canonical payload/digest/stable identity and validation-before-canonicalization/hash behavior. One-time exact source comparison reconstructs the full original `load_source` workflow and its four native sections, including inside-transaction page slicing, late bridge scope import and all native queries/locks/guards/hooks. The regular native source concurrency, reservation/CAS, staged-input conflict and manifest/rollback contracts remain in `database/tempo_content_database/tests/test_postgres.py`; they were not rerun or replaced by these synthetic cases.
