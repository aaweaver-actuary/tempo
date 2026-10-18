# Learning review application regression ownership

`tests/test_retry_policy.py` owns the soft failed-retry decision: bounded saved
draws, original-time/midnight deadlines, ready-work preference, one early fallback,
issued-head authority, persisted insertion order/priority and projection placement
restoration. `test_failed_review_binds_original_identity_time_once_before_receipt_replay`
and `test_required_failed_retry_failure_preserves_transaction_owner_failure_boundary`
in `test_application.py` own the review-port binding and required-effect boundary.
The regular package selection includes these cases; source checks do not replace
installed-package or native qualification.

`test_missing_issued_origin_requires_same_identity_recovery_before_empty_completion`
owns the pure recovery decision. Backend `test_failed_retry.py` supplies real
SQLite proof for required first-issuance effects, rollback, essential work before
and after the final facts read, missing projection without a replacement, same
identity/position recovery, protected replacement and ID/content/revision/lifecycle
conflicts. Retained timestamp/decision fields stay immutable through recovery.

The regular owning package command is `python -m pytest tests` against the installed
artifact. All parametrized identities in `tests/test_application.py` and
`tests/test_boundary.py` must collect and pass, including original receipt replay,
legacy broader projection replay, changed request binding, recorded clocks, guided
failure, recovered chronology, explicit registration, evidence and continuation.
`qualify.py` runs the same suite in source-free Python 3.12/3.14 consumers with
service guards. A failed, skipped, deselected, incomplete or zero-selected suite is not qualification.

The separate owning native command is `python integration/run_postgres.py DIR` in
a legacy test environment containing installed tempo-learning-review and
 tempo-scheduling. It discovers `integration/test_postgres.py` in its own fresh
PostgreSQL cluster and exports every executed pytest identity to JUnit. It covers
ordinary evidence/history/schedule/receipt/queue effects, response loss and logical
retry, native competing connections, stage failures, handled savepoint conflict,
stale/deleted/archived distinctions, optional successor failures/lock contention,
connection termination with rollback and fresh-process replay, and active/inactive
frozen reader constraints. It never accepts a pre-existing server/database URL. The unmodified D component
selector also executes these same 43 pure and 24 native identities in the
disposable combined context; its externally supplied JUnit receipt is preserved.

Existing backend characterization remains in its regular suite: scheduling review
application/store, prepared review continuation, queue attempt recovery/issuance,
phone offline training and relevant study durability. The existing native training
transition regression injects the relocated adapter issuance seam without weakening
its deletion/revision contention assertions. Scheduler package/adapter/boundary
tests remain with C and are not edited here.

## Integration and qualification regressions (PR #278)

`integration/test_qualification_runners.py` is a separate regular owning tooling
suite, registered alongside the package and native suites in `components.json`.
It requires no PostgreSQL cluster and stays outside the guarded application tests.

- `test_full_qualification_rejects_inherited_partial_selection`: selectors,
  ignore options, early exits and plugin injection cannot become qualification.
- `test_qualifier_rejects_selectors_before_build_and_native_before_docker`: a
  no-match inherited selector fails before creating build/database resources.
- `test_native_preserves_external_junit_destination_with_spaces` and
  `test_native_rejects_invalid_reporting_or_reporting_with_selection`: both
  supported spellings/forms preserve reporting; malformed/duplicate destinations
  and JUnit plus deselection are rejected.
- `test_full_qualification_requires_exact_complete_normal_inventory` and
  `test_full_qualification_rejects_missing_skipped_failed_or_duplicate_required_cases`:
  nonempty exact collected/executed identities, all successful execution phases,
  zero deselections, and complete passing JUnit are mandatory.
- `test_full_qualification_removes_external_plugin_and_environment_injection` and
  `test_actual_controlled_collection_execution_and_external_junit_ignore_plugin_injection`:
  sanitized actual pytest subprocesses execute the full fixture and write JUnit.
- `backend/tests/test_learning_package_installation.py::test_backend_learning_distribution_and_command_import_need_no_source_or_services`:
  declared installed package versions/origins, noneditable metadata and explicit
  command registration work without import-time database/network initialization.

## Qualification review follow-up

All cases below are discovered in the same regular tooling suite and through its
registered `run_suite.py tooling` command. Tiny pytest fixtures and mocked Docker
responses exercise failure paths without provisioning extra native clusters.

- `test_registered_component_python_suites_cannot_accept_partial_selection`:
  execute each actual registered Python command with the reproduced selector,
  a no-match selector and other filtering/early-exit options; both must reject it.
- `test_component_wrapper_runs_complete_fixture_and_preserves_external_junit`,
  `test_component_wrapper_rejects_unknown_arguments_and_duplicate_junit`, and
  `test_component_wrapper_fails_on_nonpassing_required_fixture`: controlled real
  collection/execution, all supported JUnit forms, strict CLI and required
  skip/failure/setup/teardown rejection. Existing exact-inventory cases retain
  missing/duplicate/deselection coverage.
- `test_native_failure_paths_cleanup_only_verified_owned_resources`:
  start failure after/before creation, image failure, collision, mismatched labels,
  daemon/label inspection failure, removal failure and surviving-container proof.
- `test_native_success_is_published_only_after_verified_cleanup` and
  `test_native_preserves_startup_and_cleanup_errors_together`: current success is
  conditional on cleanup, and diagnostics preserve both failures.
- `test_native_failed_retry_invalidates_previous_success_and_preserves_history`:
  success followed by skip, collection/startup/image/cleanup failure or rejected
  selectors has a fresh incomplete summary, distinct invocation inputs, preserved
  previous evidence and a nonzero qualification exit even when pytest returns zero.
- `test_atomic_summary_replacement_never_exposes_partial_json`: replacement
  failure preserves parseable prior bytes and removes the incomplete temporary file.
