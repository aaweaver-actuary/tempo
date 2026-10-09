# Refresh input coalescing validation

Scope: priority and opportunity source intent uses indexed version/publication
identities and a durable quiet window. Unchanged inputs preserve useful stages,
generations and failures. Actual accepted priority publication schedules its
dependent opportunity work. Lease acquisition closes the pending quiet window
atomically. No foreground, provider or analysis computation is added to request
transactions. SQLite uses conservative source epochs with no-op/telemetry
updates excluded. PostgreSQL preserves existing scoped epochs and adds finding,
analysis-publication and discovery-window input triggers in additive schema 043.

Failure modes: concurrent first requests, repeated replacement, infinite quiet
postponement, obsolete priority evidence, restart, source/intent rollback and
silent missing repertoire. Smallest proof: the eight named SQLite regressions,
then producer/handoff/publication/cutover callers. Native PostgreSQL is required
because counters and claims/publications share transaction boundaries. CI owns
complete selected candidate validation; there is no rendering or TS/API shape
change in this patch. Scope-only game-index reuse follows separately.

On the dirty candidate based on the rebased engine branch, macOS ARM64 and
Python 3.14.8: eight new named cases pass in 0.92 seconds. The complete affected
refresh, introduction, opportunity, priority preparation, game completion and
cutover files pass 303 cases in 8.55 seconds. Two initial baseline cases failed
when duplicate requests replaced saved generations. The caller run initially
failed old in-memory database doubles that lacked source-version inputs; those
producer tests now mock the coalescing boundary, while the new native proof
exercises the real boundary and accepted publication end to end. The existing
foreground/replay opportunity proof uses a controlled due-time clock for the
intentional quiet delay, retaining its real concurrency assertions.

`TEMPO_TEST_INSTANCE=disposable TEMPO_REDIS_URL=redis://127.0.0.1:49502/0
TEMPO_REFRESH_PROOF_URL=postgresql://postgres@127.0.0.1:49501/tempo_graph_proof
backend/.venv/bin/python scripts/check_postgres_refresh_inputs.py` passes on
PostgreSQL 18.6 / Redis 7: 100 unchanged reconnects, 12 concurrent first requests,
quiet boundaries, actual generation 7 publication and dependent opportunity
reset, atomic crash rollback, retained failures, and foreground denial. Maximum
measured reconnect-inclusive request transaction: 15.162 ms, below the unchanged
250 ms limit; this is deadline evidence, not a throughput claim. The first
native attempt selected the intentionally competing repertoire; the fixture now
promotes only its target for the publication portion. No claim policy changed.

Each proof creates and drops a fresh marked helper database and unique admission
keys. Parent foreground leases are retained; no live study data is read or
modified. Owning checkout: /Users/andy/tempo/.dev-copies/analysis-activity-repair.
Parent lightweight resource identities and exact teardown are retained in root
`test-results/analysis-activity-2026-10-09/graph-proof-resources.txt`; helper
cleanup succeeded. Native log: root
`test-results/analysis-activity-2026-10-09/refresh-input-native.log`.
`git diff --check` passes. Current-head full required CI is pending; focused
native execution does not substitute for complete durability and browser gates.

Additional source/canonical/paused-defense callers initially exposed immediate
opportunity-claim assumptions and an unchanged-source stale test. The canonical
driver now claims at the saved due-time using a controlled clock; the stale
priority case changes an actual input before requesting its successor. The
paused-report fixture stops its own startup coordinator so the test driver owns
its validation task. Existing HTTP pause/resume, zero-review and single-review
assertions remain. The affected canonical/priority/activity/defensive files pass
195 cases in 27.99 seconds. The unchanged PostgreSQL derivation/threat callers
also passed in the preceding 191-case run (six failures were the subsequently
repaired canonical/fixture assumptions); that partial run is not a complete
pass. Eight new coalescing cases pass after the final input-version refinement
in 0.90 seconds. CI owns the complete new candidate evidence.
