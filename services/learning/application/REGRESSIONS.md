# Learning review application regression ownership

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
