# Scheduling regression ownership

Owning regular suite: `uv run --locked python -m pytest tests` against the installed wheel.

The five `SchedulerTests` method names are preserved from `backend/tests/test_services.py::SchedulerTests` at main d5f9ef31. Legacy cases remain discoverable during transition; the package is independently executable.

- `test_again_is_reshuffled_into_todays_queue`
- `test_unlock_requires_stability_across_days`
- `test_first_clean_pass_returns_to_end_then_fsrs_schedules`
- `test_overdue_growth_is_capped`
- `test_hard_track_enters_and_recovers_on_fixed_cadence`
- M02: `test_fixed_time_schedule_matches_golden_output`, `test_installed_scheduling_exports_and_identity`, `test_invalid_outcome_is_rejected`.

No persistence or atomic-review proof moves here. Those remain Learning's owning native tests.

Consumer parity: `tests/unit/scheduling-producer-parity-regressions.test.ts` runs the installed Python producer in isolated mode and compares the serialized golden fixture. It belongs to the producer-parity lane, not frontend-only collection.
