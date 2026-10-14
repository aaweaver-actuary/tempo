# Contract-local regression ownership — #264

Regular owning command: `contracts/.venv/bin/python contracts/run_tests.py --typescript`.
Native ownership is selected separately: `contracts/.venv/bin/python contracts/database-ownership/run.py`.
Every package also supports `python -m pytest contracts/<root>/tests`. Collection/execution counts are reported; skipped and zero-selected required tests fail. No application startup is involved.

| Invariant | Named owning regression |
|---|---|
| Logical exactly-once settlement and original receipt replay | `learning/tests/test_learning_reference.py::test_lost_response_retry_preserves_one_atomic_receipt_and_all_effects` |
| Same-key payload conflicts | `test_same_submission_identity_changed_payload_conflicts_without_effects` |
| Concurrent identical and competing submissions | `test_concurrent_identical_submissions_settle_once`, `test_concurrent_competing_payloads_have_one_winner` |
| Separate pending submission IDs cannot settle the same attempt twice | `test_two_pending_submission_identities_cannot_both_settle_one_attempt` |
| Pending is not committed; retirement fences unsettled work | `test_pending_is_not_committed_and_same_key_can_settle`, `test_retirement_fences_unsettled_attempt_but_committed_receipt_replays` |
| Publication is not activation; stale acknowledgements do not activate older content | `content-publication/tests/test_activation_reference.py::test_publication_acceptance_is_not_activation`, `test_stale_generation_and_acknowledgement_cannot_activate_older_content` |
| Retirement/replacement fencing and immutable generations | `test_retirement_acknowledgement_requires_fencing_and_is_idempotent`, `test_structural_replacement_requires_accepted_current_generation`, `test_immutable_generation_and_activation_identity_reject_conflicts` |
| Duplicate observations preserve one obligation; completion cannot regress | `game-evidence/tests/test_evidence_reference.py::test_duplicate_game_evidence_and_new_delivery_identity_do_not_duplicate_obligations`, `test_completed_evidence_replay_cannot_regress_to_pending` |
| Unknown probability and deterministic fallback | `intelligence/tests/test_advisory_reference.py::test_unknown_probability_is_distinct_from_zero`, `test_intelligence_unavailable_preserves_deterministic_introductions` |
| Rankings preserve eligibility/quota/deduplication | `test_ranking_never_overrides_eligibility_quota_or_deduplication` |
| Worker-specific results, stale versions and cancellation | `analysis-jobs/tests/test_jobs_reference.py::test_bounded_worker_result_matches_request_and_provenance`, `test_cancellation_requested_is_not_computation_termination`, `test_stockfish_and_maia_cannot_exchange_results`, `test_same_job_request_replay_and_new_execution_generation_preserve_input` |
| Native role isolation, default PUBLIC ACLs and credential guards | All six named test families in `database-ownership/tests/test_ownership.py` |
| Installed source independence; forbidden direct/lazy/dynamic imports | `tests/test_dependency_boundaries.py::test_undeclared_direct_lazy_conditional_and_dynamic_dependencies_fail`, `test_installed_imports_do_not_load_application_registry_or_model_runtimes` |
| Independent consumers and cross-field negatives | `tests/test_python_consumers.py::test_python_v1_producers_exchange_with_independent_python_v1_consumers`, `tests/test_semantic_negatives.py::test_independent_python_and_typescript_consumers_reject_semantic_conflicts`; TypeScript tests name every record |
| Cross-contract definition/obligation compatibility | Both tests in `tests/test_cross_contracts.py` |

These are new contract/reference invariants. There is no demonstrated failing baseline for the legacy production services, and this registration does not claim those implementations have been repaired. Exact parameterized identities are retained in qualification reports.
