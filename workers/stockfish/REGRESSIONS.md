# Stockfish regression ownership (#43, workstream B)

Ordinary suites are registered in components.json and listable through
`node tools/component-build/test-lane.mjs --component stockfish --list`.
`node test.mjs pure`, `node test.mjs protocol`, installed Python
`python -I -m pytest tests/contracts/test_exchange.py --import-mode=importlib -q`,
and `node qualify.mjs container` are owning executable commands. Container runs
use actual installed artifacts; the first two suites use deterministic fixtures.
Empty selections and absent/failed required proofs fail; no tests are skipped.

## Exact B8 identities

| Identity | Owning proof |
| --- | --- |
| B_real_engine_smoke | qualify_container.py acceptance, actual installed engine |
| B_history_root | tests/core.test.mjs; actual history fixture in container exchange |
| B_both_colors | tests/core.test.mjs; both-color actual mate/search fixtures |
| B_special_moves | tests/core.test.mjs; actual castling/EP/promotion/custom FEN searches |
| B_illegal_history | tests/core.test.mjs, pre-dispatch rejection |
| B_restricted_roots | tests/core.test.mjs; independent actual exchange consumer |
| B_multipv_integrity | tests/core.test.mjs; actual MultiPV plus python-chess consumer |
| B_score_types | tests/core.test.mjs; actual positive/negative mate scores |
| B_partial_or_bound_output | tests/core.test.mjs, deterministic UCI records |
| B_terminal_position | tests/core.test.mjs, no valid empty success |
| B_exact_replay | tests/protocol.test.mjs and actual container replay |
| B_payload_conflict | tests/protocol.test.mjs |
| B_stale_generation | tests/protocol.test.mjs, old child callback fencing |
| B_cancel_before_job | tests/protocol.test.mjs, speculative future cancellation |
| B_cancel_during_search | tests/protocol.test.mjs; actual CPU-observed cancellation/exit/recovery |
| B_cancel_complete_race | tests/protocol.test.mjs, both terminal orders |
| B_multiple_cancellation_ids | tests/protocol.test.mjs, all retained IDs and capacity rejection |
| B_superseded_queued_work | tests/protocol.test.mjs, replacements and pre-job tombstones |
| B_process_restart | tests/protocol.test.mjs; actual fresh worker session/new generation |
| B_wall_memory_failure | tests/protocol.test.mjs; actual wall timeout, memory admission and kernel OOM pressure |
| B_slow_consumer | tests/protocol.test.mjs; qualify_container.py unread stdout |
| B_clean_shutdown | tests/protocol.test.mjs; actual EOF and signal container shutdown |
| B_installed_consumer | tests/contracts/consume_observed.py, installed contracts and python-chess |
| B_restricted_artifact | qualify.mjs restricted installation and actual container filesystem/configuration |
| B_finite_backlog_driver | backlog_driver.py with fixtures/backlog.json, installed typed client |

The memory-pressure proof kills a deliberately oversized task-owned helper inside
the worker cgroup; it does not assert an unexplained engine failure was OOM.
Only kernel-observed memory events establish that cause. Session loss never
manufactures a JobStatus or successful result.

## Donor ownership and retained invariants

Maia #110 supplies reviewed lifecycle patterns: state transitions before callbacks,
one child, generation fencing, queued replacement cancellation, future cancellation
IDs, and exact session receipts. Stockfish owns its copy and equivalent named
proofs; no Maia runtime dependency or Maia lifecycle change is introduced.

PR #182's restricted-image dependency closure and transient-versus-unexpected
failure distinction inform the artifact and execution tests. Its HTTP/PostgreSQL
claim journal remains domain/C/legacy ownership; no scheduler or DB failure handler
is transplanted. No historical transaction-budget root cause is claimed resolved.

Input digest, strict schemas, timestamp chronology, reservation exhaustion,
reentrant cancellation, memory/CPU/swap admission, passive imports, installed
Python 3.12/3.14 clients and restricted dependency closure have additional owning
cases. Shared v1/schema/reference tests remain owned by contracts/analysis-jobs.


## Client credential isolation reported by C

`tests/contracts/test_exchange.py::test_B_client_environment_isolation` launches a
real subprocess through the public client with synthetic private-config, PG and
provider canaries in the parent. Both default-whitelist and explicit-env cases
prove those names are absent in the child, while intentional safe entries remain.
C owns constructing its safe map; B owns passing it without ambient inheritance.

## PR #293 independent-review repairs

All cases below run in normal registered suites. Direct/fake-child proofs do not
substitute for the separately named actual installed-container cases.

| Invariant | Ordinary owning identity |
| --- | --- |
| Repeated start retains active child/token/request, cancellation and recovery | protocol: `B_repeated_start_preserves_active_child` |
| Concurrent initial and repeated idle startup inspect/spawn once | protocol: `B_concurrent_initial_start_and_idle_reentry` |
| Failed initial inspection is shared; closed instances cannot retry | protocol: `B_initial_start_failure_is_shared_and_cannot_retry_after_close` |
| Inspection/factory callbacks cannot reenter before startup authority exists | protocol: `B_start_reentrant_inspection_and_factory_share_initial_authority` |
| Initial child warmup cannot resurrect shutdown | protocol: `B_start_during_initial_child_warmup_cannot_resurrect_shutdown` |
| Repeated startup during cancellation/replacement warmup | protocol: `B_start_during_cancellation_and_replacement` |
| Closing/closed startup refusal | protocol: `B_start_during_shutdown_and_after_closed` |
| Shutdown while resource inspection is actually pending | protocol: `shutdown during asynchronous resource inspection cannot spawn a late child` |
| Stale staged schema is never consumed | pure: `B_test_runner_refreshes_staged_contract_inputs` |
| Missing/partial schema and asset stages, corrupted assets | pure: `B_test_runner_refreshes_missing_partial_and_corrupted_assets` |
| Authoritative mismatches fail; matching updated pins work | pure: `B_test_runner_rejects_authoritative_mismatch_and_accepts_valid_manifest_update` |
| Concurrent input isolation, empty selection and invalid suite rejection | pure: `B_test_runner_parallel_invocations_have_private_inputs_and_truthful_selection` |
| Missing authoritative inputs cannot reuse a prior stage | pure: `B_test_runner_missing_authoritative_input_cannot_reuse_staged_success` |
| Failed/skipped/TODO cases cannot become successful reports | pure: `B_test_runner_failed_skipped_and_todo_cases_cannot_pass` |
| Running immutable conflict cannot emit execution failure | protocol: `B_conflicting_duplicate_does_not_fail_active_execution` |
| Completed result/status bytes survive conflicting submission | protocol: `B_conflicting_duplicate_preserves_completed_receipt` |
| Workspace-scoped cancellation-ID conflict has no target effect | protocol: `B_cancellation_identity_conflict_does_not_fail_target_execution` |
| Refused 65th cancellation is unretained/unacknowledged, 64 retained IDs settle | protocol: `B_cancellation_capacity_refusal_is_not_execution_failure` |
| Schema/shutdown/reservation refusal preserves execution authority | protocol: `B_invalid_commands_and_shutdown_cannot_fail_accepted_receipts` |
| NDJSON refusal closes session, sanitizes reason and reaps owned children | protocol: `B_cli_command_rejection_fails_closed_without_false_receipts` |
| Installed Python 3.12/3.14 observe typed loss and explicit next generation | contracts: `test_B_installed_client_command_rejection_is_typed_session_loss` (four parameters) |
| Independent consumer rejects a false failure after a completed result | contracts: `test_B_independent_consumer_rejects_false_terminal_execution_status` |
| Real CPU-active engine survives repeated startup and recovers after confirmed cancellation | container: `B_installed_repeated_start_preserves_active_child` |
| Actual CLI/client rejection and explicit generation-advanced restart | container: `B_actual_command_rejection_active_conflict`, `B_actual_command_rejection_completed_conflict`, `B_actual_command_rejection_cancellation_conflict`, `B_actual_command_rejection_cancellation_capacity` |

Existing `B_payload_conflict`, `B_stale_generation`, `B_multiple_cancellation_ids`
and pre-job capacity assertions now require explicit typed command rejection and
absence of false status records. Their identity/fencing/limit assertions remain.
No shared contract was extended. Accepted computation failures still use retained
JobStatus; refusal of an ambiguous command uses transport loss, never a fake
execution failure or termination acknowledgement.
