# Deferred estimator adapter assertions

The following legacy tests remain in their regular backend suite and must be
qualified by Learning/scheduling when a public projection/export API is adopted.
They are not selected, skipped or claimed as passing by this package suite.

- `test_decision_readiness_boolean_scheduler_parameters_are_unavailable`
- `test_decision_readiness_clamped_memory_reference_and_actual_freshness_remain_distinct`
- `test_decision_readiness_clean_practice_restores_fsrs_estimate`
- `test_decision_readiness_corrupt_scheduler_state_is_explicit`
- `test_decision_readiness_missing_state_retains_reliable_failure`
- `test_decision_readiness_overdue_decay_uses_explicit_as_of_without_due_penalty`
- `test_decision_readiness_rejects_historical_evaluation_of_newer_scheduler_state`
- `test_decision_readiness_studied_card_without_scheduler_state_is_unavailable`
- `test_decision_readiness_uses_no_implicit_clock`
