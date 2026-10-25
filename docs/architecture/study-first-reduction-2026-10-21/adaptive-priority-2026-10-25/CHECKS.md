# Existing proof owners for a later implementation

These are source references and future selections, **not tests run or passed by
this documentation-only packet**. Choose affected cases after an exact edit
manifest exists; do not run every row automatically. Use [docs/testing.md](../../../testing.md)
and each owner's existing setup/command, with task-owned resources and no live
database/Redis endpoints.

| Boundary that changes | Existing proof owner and named contract |
| --- | --- |
| Saved publication selection / due-review independence | `backend/tests/test_introduction_priorities.py::test_next_day_admits_from_last_published_scores_without_delaying_due_review`; same file's highest-impact and evidence-state cases |
| Frozen override and ordinary unranked fallback | `backend/tests/test_frozen_opening_order.py::test_frozen_ranks_override_generated_scores_breadth_and_ordinary_gameplay`, `test_unranked_legacy_origin_is_protected_but_new_unranked_fallback_remains`, issued/exposed-slot and changed-content cases |
| Limits, shared cards and feedback | `backend/tests/test_new_cards_per_repertoire.py`; relevant `backend/tests/test_real_game_feedback.py` cases; the selector/capacity owning cases in `backend/tests/test_opening_capacity_admission.py` |
| Enqueue, replay and stale generations | `backend/tests/test_priority_jobs.py::test_priority_refresh_coalesces_repeated_game_and_coverage_triggers`, `test_interrupted_priority_refresh_replays_once_after_restart`, `test_stale_priority_generation_cannot_overwrite_newer_inputs` |
| Epoch source and lock order | `backend/tests/test_priority_lifecycle_database_split.py::test_priority_lease_keeps_source_job_and_task_lock_order`, `test_source_epoch_identity_creates_only_existing_repertoire_and_keeps_shared_version` |
| Retained opportunity completion and refresh | `backend/tests/test_refresh_input_coalescing.py::test_stale_opportunity_completion_cannot_satisfy_replacement_refresh`, `test_accepted_opportunity_publication_survives_task_retention_without_refresh_loop`, `test_opportunity_input_changes_only_when_priority_is_published`, missing-source/rollback/terminal/restart cases |
| Prepared rows and bounded retention | `backend/tests/test_postgres_priority_preparation.py` source-change, crash/retry, missing-row and empty-repertoire cases; `backend/tests/test_postgres_priority_retention.py`; `backend/tests/test_priority_storage.py` publication/retention cases |
| Native publication, refresh serialization and freshness | `database/tests/priority_baseline_postgres.py`, `database/tests/check_postgres_refresh_inputs.py`, `database/tests/check_postgres_canonical_freshness.py`; applicable native frozen proof in `database/tests/test-frozen-postgres.py`. These require their existing disposable infrastructure, not a production connection. |
| Independent Learning frozen fallback, if that owner's boundary changes | `services/learning/runtime/tests/test_frozen_import_contract.py`; `test_publications.py::test_frozen_ranks_control_introductions_without_score_producers`. No changes to this owner are proposed here. #231 pure evidence and #386 native intake are separate from legacy saved-score semantics. |

Backend single-file selection is defined by `make python-file FILE=<path>`.
The bounded independent Learning pure command recorded in #231 is
`uv run --project services/learning/runtime --locked --no-editable pytest services/learning/runtime/tests/test_frozen_import_contract.py -q`.
Native scripts retain their own setup and options; no invented universal native
command is supplied. Existing named proofs are preserved, not replaced by new
implementation-mirroring tests.
