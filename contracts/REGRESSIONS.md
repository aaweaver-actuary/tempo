# Contract-local regression ownership — #264

Regular owning command: `contracts/.venv/bin/python contracts/run_tests.py --typescript`.
Every package also supports `python -m pytest contracts/<root>/tests`. Collection/execution counts are reported; skipped and zero-selected required tests fail. No application startup is involved.

| Invariant | Named owning regression |
|---|---|
| Duplicate observations preserve one obligation; completion cannot regress | `game-evidence/tests/test_evidence_reference.py::test_duplicate_game_evidence_and_new_delivery_identity_do_not_duplicate_obligations`, `test_completed_evidence_replay_cannot_regress_to_pending` |
| Installed source independence; forbidden direct/lazy/dynamic imports | `tests/test_dependency_boundaries.py::test_undeclared_direct_lazy_conditional_and_dynamic_dependencies_fail`, `test_installed_imports_do_not_load_application_registry_or_model_runtimes` |
| Independent consumers and cross-field negatives | `tests/test_python_consumers.py::test_python_v1_producers_exchange_with_independent_python_v1_consumers`, `tests/test_semantic_negatives.py::test_independent_python_and_typescript_consumers_reject_semantic_conflicts`; TypeScript tests name every record |

These are new contract/reference invariants. There is no demonstrated failing baseline for the legacy production services, and this registration does not claim those implementations have been repaired. Exact parameterized identities are retained in qualification reports.

## PR #271 repair ownership

The retained Game Evidence v1 suite exercises timestamp validity and serialization.

The shared Python negative test's historical name mentions TypeScript, but executes
Python only. Independent TypeScript execution is registered in `consumer.test.ts`
under `consumer rejects semantic negative` and `repair:` case names.
