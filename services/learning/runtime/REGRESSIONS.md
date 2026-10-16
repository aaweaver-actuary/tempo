# Learning runtime regression ownership

The complete native inventory is discovered by `integration/run_postgres.py` from `tests/`; collection, every setup/call/teardown phase and JUnit must agree. No selection flags or skips are accepted. `components.json` registers the owning native suite and its real client/runner inputs. Installation checks also run in source-removed Python consumers through `qualify.py`.

| Regular file | Proof |
| --- | --- |
| `test_runtime.py` | Load/issue/review, immutable logical and transport replay, scope/origin/role denial, authoritative concurrent first enqueue |
| `test_transactions.py` | Opening evidence atomicity; handler and handled-reconciliation rollback; competing payloads; bounded current-card contention; nonblocking optional successor; stale/deleted/retired definitions; retained quota; finite-contention maintenance; local-day boundary |
| `test_families.py` | All five typed specifications, practice/review/self-assessment/feedback; tactical discovery and binding; defense recognition, covered grading and uncovered durable intent |
| `test_eligibility.py` | Parent exposure, integrity fences, competing issuance, middlegame review, guided typed attempts/sibling burial, local evidence promotion and required settlement |
| `test_endgames.py` | Offline cache-first probe, deterministic attempt and immutable retry; configured HTTP-provider protocol success/cache/failure separately; full-FEN cache isolation in both halfmove-clock orders and historical-key exclusion |
| `test_publications.py` | Immutable staging, completeness and latest-generation recheck; accepted frozen ranks; migration/workspace/privileged-role readiness; unique route positions and memberships; every-line learner-turn validation for both colors; exact multi-membership graph completeness and rollback; competing owners, retirement/replacement locking (including removed memberships), independent repertoires and retryable owner contention |
| `test_evidence_revisions.py` | Monotonic revisions, original transport replay, semantic retries without re-promoting settled misses, conflicting content, empty corrections, transaction-wide rollback, both concurrent revision orders, installed HTTP process restart, historical receipt migration and restricted-role access, custom receiver authority; actual PostgreSQL cross-evidence obligation/delivery identity races and loser rollback |
| `test_enqueue.py` | Barrier-controlled creator rollback with waiting insertion; existing leased task replacement; authoritative generations/event identities |
| `test_restart.py` | Installed HTTP process restart, response loss, durable intent resumption and task-owned database restart |
| `test_browser.py` | Real Chromium with existing strict schemas/outbox; progression and lost-response reload recovery against the real Learning API |
| `test_container.py` | Final restricted-user image, actual PostgreSQL load/issue/review/replay and absence of backend/broker/model source |
| `test_installation.py` | Installed closure, packaged append-only migration dependency/resources, forbidden optional imports, transaction owner and denied-auth startup without infrastructure |

Legacy compatibility adapters retain the regular scheduling application/package/boundary and defensive grading/validation/detection suites. Scheduling ownership metadata records the packaged owner. The retained enqueue primitive changes its returned row/event identity to PostgreSQL's `RETURNING` result; `backend/tests/test_durable_task_enqueue_identity.py` exercises the retained consumer's event/result binding, while native concurrency evidence lives in Learning's regular suite rather than a parallel harness. Shared runner normalization has a named regression in the existing component-build boundary suite.

## F1 adoption

`test_remediation.py` owns the integrated v2 intake/borrowed-row-factory proof;
issuance-only frontier binding and reload reuse; qualifying settlement and logical
review replay; late/unknown-time evidence requiring a fresh attempt; immutable
corrections, historical semantic replay and exact withdrawals; validity/lifecycle
fences; guided/failed nonsettlement; required-settlement rollback (including handled
reconciliation); superseded/expired lease rollback; zero-cap locked-card
introduction; protected positions and unchanged frozen inputs; concurrent delivery;
both actual review/evidence commit orders; acceptance/receipt/work-request rollback;
trusted alternatives/position/alias rejection; ordinary-work fairness; and stable
library miss order across single-card maintenance slices.
Prepared-continuation coverage asserts that prefetch has no frontier, the actual
receipt-authorized issuance binds it once, reload/replay retains it, and neither
review submission nor an old already-issued origin can invent a missing binding.

`test_game_evidence_runtime.py` owns actual installed producer CLI -> authenticated
Learning HTTP -> separate PostgreSQL database integration. A real forwarding proxy
drops the acknowledgement after Learning commits; a new producer process replays
after Learning restarts. Role-denial checks prove the runtime identities cannot
connect to the other domain database. Its Chromium proof stops the producer and
Learning, restarts only Learning, settles remediation with real response-loss/reload
recovery, then follows existing reinforcement and saves ordinary newly introduced
work. No optional provider/model/broker is started, and browser traffic to any
other origin is rejected. These are focused client/API proofs, not the complete
Study UI switch owned by F2.

The existing `test_evidence_revisions.py` cases now exercise the same remediation
authority using coherent accepted v1 observations. Their original monotonicity,
global identity-race, rollback, migration, restricted-role and transport replay
invariants remain; old raw-obligation/frontier writes are replaced by equivalent
repository assertions. Historical v1 receipts remain a read-only replay boundary.
No selected check is skipped or weakened. Existing `test_eligibility.py` promotion
and settlement now observes the durable maintenance boundary, rather than assuming
intake performs synchronous admission. The native browser harness awaits #285's
durable enqueue and compiles the two unchanged Study package public entries using
its local TypeScript dependency; no root frontend install is required.

## PR #287 recovery repairs

The regular native `test_remediation.py` inventory adds:

- `test_remediation_refresh_preserves_active_cursor_and_lease`: queued, leased and
  retrying states through the real queue-refresh caller; cursor, lease, generation,
  ready ordering, attempts, other payload fields and database-returned identities.
- `test_repeated_refreshes_do_not_starve_later_remediation_card`: four retained
  non-actionable identities and one later valid miss; continuing requests between
  pairs of claims, at most five successful remediation slices, and ordinary work.
- `test_remediation_arrival_behind_cursor_is_found_by_followup_pass`: continuing
  and recreated clients. `recreated-client` owns restart/cursor/rescan preservation.
- `test_remediation_rescan_racing_with_final_slice_is_not_lost`: both serialization
  orders, actual PostgreSQL blocking and committed-state verification.
- `test_remediation_day_change_fences_old_slice`,
  `test_remediation_coalesced_request_rolls_back_with_parent`, and
  `test_remediation_failed_slice_release_preserves_concurrent_rescan`: day fencing,
  atomic request rollback and failed-owner release.

`test_evidence_revisions.py` adds `test_stale_v1_delivery_receipt_survives_reconnect`,
`test_stale_v1_delivery_id_cannot_be_reused_with_newer_payload`,
`test_historical_frontier_stale_delivery_binds_transport_identity` (both wires),
`test_concurrent_stale_delivery_identity_reuse_is_serialized` (both commit orders,
independent source identities without shared obligation locks),
`test_stale_receipt_failure_rolls_back_without_acceptance_effects`, and
`test_v2_historical_semantic_replay_is_unchanged_by_v1_stale_policy`.

`test_installed_producer_auth_recovery_accepts_once_in_independent_learning` in
`test_game_evidence_runtime.py` uses actual installed CLI processes, authenticated
Learning HTTP and separate disposable databases. Correcting the private token and
restarting retries the original delivery and produces one logical acceptance.

`test_installed_receipt_policy_replays_stale_identity_without_content_or_writes`
in `test_installation.py` checks optional installed receipt arguments, immutable
replay and digest conflict in all four wheel/sdist consumers using a receipt-read
port double. Native suites separately own actual persistence and concurrency.

`test_review_task_locks_follow_prepared_continuation_issuance` observes actual
PostgreSQL task-table lock ownership at continuation binding.
`test_stale_content_receipt_binds_identity_without_acceptance` additionally covers
durable rejected-content identity and acceptance only under a fresh transport.
