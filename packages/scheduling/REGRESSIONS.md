# Scheduling regression ownership

Owning regular suite: `uv run --locked --no-editable python -m pytest tests` against the installed wheel.

The five `SchedulerTests` method names are preserved from `backend/tests/test_services.py::SchedulerTests` at main d5f9ef31. Legacy cases remain discoverable during transition; the package is independently executable.

- `test_again_is_reshuffled_into_todays_queue`
- `test_unlock_requires_stability_across_days`
- `test_first_clean_pass_returns_to_end_then_fsrs_schedules`
- `test_overdue_growth_is_capped`
- `test_hard_track_enters_and_recovers_on_fixed_cadence`
- M02: `test_fixed_time_schedule_matches_golden_output`, `test_installed_scheduling_exports_and_identity`, `test_invalid_outcome_is_rejected`.

No persistence or atomic-review proof moves here. Those remain Learning's owning native tests.

Consumer parity: `tests/unit/scheduling-producer-parity-regressions.test.ts` runs the installed Python producer in isolated mode and compares the serialized golden fixture. It belongs to the producer-parity lane, not frontend-only collection.

#200 strict-boundary regressions (same regular owning suite):

- `test_explicit_schedule_preserves_baseline`: 12 pre-edit complete-state goldens,
  including reinforcement, light/hard transitions, overdue cap and independent day.
- `test_environmental_inputs_are_required_keyword_arguments` and
  `test_naive_review_time_is_rejected`.
- `test_incomplete_card_state_is_rejected`, `test_invalid_explicit_card_state_is_rejected`,
  `test_missing_serialized_card_has_no_initial_state_fallback`.
- `test_aware_offsets_preserve_instant_and_separate_local_day` and
  `test_existing_card_offset_timestamps_do_not_change_effective_review_instant`.
- `test_installed_scheduling_is_identical_across_fresh_guarded_processes`:
  real FSRS decoding/reviews under clock/ID/sleep/randomness and initialization guards,
  repeated in UTC, America/New_York and Asia/Tokyo.
- `test_installed_guard_rejects_environmental_apis`: negative guard controls.

Legacy wrapper forwarding, controlled defaults, naive normalization, fresh-state
identity generation, signature/class identity and result parity belong to
`backend/tests/test_scheduling_package_adapter.py`. Function identity is intentionally
replaced by those behavioral proofs; the strict package function has no defaults.
Direct strict-package callers are the backend adapter, package tests, that adapter
regression and the installed producer-parity test. Review orchestration still calls
the unchanged backend signature. No database/queue/receipt claim moves here.
