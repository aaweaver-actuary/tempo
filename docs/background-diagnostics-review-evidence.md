# PR #56 instrumentation correction evidence

Reviewed candidate: `39996316765e9875abc480666fe7ec7af34d69ce`.
Current main at start: `937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1`.

Test plan selected before implementation: fix primary/background preflight,
reserved-section telemetry I/O, and redundant lifecycle kind reads. Risks are
replica-driven false staleness, bypassed foreground admission, extended database
reservations, misattributed timing and counter/replay drift. Start with named
regressions in `backend/tests/test_background_diagnostics.py`, including traced
connections/events and controlled clocks. Then run affected durable/activity and
PostgreSQL connection contracts. No public schema/frontend behavior change is
planned. Exercise actual primary/read separation, read-only enforcement, gate
contention, telemetry reservation state, concurrent counters and complete event
accounting in the owned disposable PostgreSQL proof. Rebase conservatively onto
main and rerun affected checks after integration. CI owns the complete required
final candidate gate; old candidate passes are historical evidence only.

Existing evidence inspected: previous CI quality success and local Python timing
artifact (52.19 seconds, failed earlier route audit). No baseline full gate is
needed to reproduce these three findings. No live queue actions are authorized.

Preflight baseline: the three new `delivery_preflight` cases failed as expected
in 2.30 s: foreground classification, replica pool selection and bypassed wait.
Fix: execute preflight inside measured/background task context, reuse admitted
background reads with an authoritative option that selects the primary pool and
sets the transaction read-only. Existing PG background timeout/lock policy is
unchanged. The SQLite test submission adapter now honors background classification
instead of substituting a foreground connection for both write classes.

Reservation/timing baseline: three new cases failed in 3.09 s. The two
reservation cases first exposed missing SQLite database-stage classification;
the controlled-clock case attributed five seconds to three seconds of admission
wait. The corrected tests assert the actual Redis call boundary against both
local reservation and shared lease state, including exceptions. Combined
preflight/admission/reservation selection: seven passed in 2.61 s. Stage samples
are throttled; lifecycle start/end remain forced outside reservations. Network
publication precedes the admission clock, and suppression lasts through shared
lease release. No admission policy changed.

Kind-lookup baseline: the named lifecycle trace failed with 12 redundant kind
SELECTs in 1.82 s. Lifecycle owners now pass known kind to the single counter
implementation; completion/deferral accept an optional kind from the worker.
Interrupted-task selection adds kind to its existing projection. Stale-result
existence checking reuses its row rather than repeating the lookup. ID-only
compatibility retains one fallback read. The full diagnostic file passed 32
cases in 15.32 s before the final completion/deferral trace extension.

The PostgreSQL proof now distinguishes ordinary read-pool state from the primary,
checks SQL read-only enforcement and actual admitted preflight, and instruments
Redis calls during real PG reads. Its transition timing includes the entire
known-kind raw-event/counter hook and commit flush, instead of calling increment
alone. It excludes runtime Redis and preflight latency; no total-instrumentation
overhead claim follows from this measurement.

Rebased code candidate: `f7d27630f73ff06d100afded166c883266a94cdf`, based on
`937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1`. The only conflict was the appended
`tests/REGRESSIONS.md` sections; both main's tactic-capture coverage and all #37
entries were preserved. Main's changed tactic-capture implementation/browser/unit
files match main exactly. No frontend/schema contract changed in this correction.

Final focused command on that rebased code candidate (macOS ARM64, Python 3.14.5):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest \
  backend/tests/test_background_diagnostics.py \
  backend/tests/test_background_activity.py \
  backend/tests/test_postgres_durable_phase.py \
  backend/tests/test_postgres_game_derivation.py \
  backend/tests/test_postgres_game_sync_windows.py \
  backend/tests/test_postgres_cutover.py \
  backend/tests/test_durable_work_queue.py \
  backend/tests/test_external_analysis_worker.py -q --tb=short --rootdir=.
```

Result: **261 passed in 16.86 s**. An earlier affected-file run found three
test doubles that did not accept the new optional kind argument (252 passed,
three failed, 16.66 s); those doubles were updated, not their assertions.
The fourth event-hook double was also updated. No skips or timeout changes.

Real disposable PostgreSQL 18.6 command:

```sh
TEMPO_PYTHON=backend/.venv/bin/python make docker-durability
```

The runner created and cleaned only its own containers/network/volumes/images.
Stage times: build 31.13 s, maintenance 3.97 s, startup 20.69 s, health 0.72 s,
background budgets 2.99 s, operation recovery 2.98 s, schema/priority/diagnostics
upgrade 22.95 s, workloads 30.77 s, cleanup 12.98 s (plus compose 0.61 s).
The whole local run **failed**, so this is not complete durability evidence.
The diagnostic proof passed every assertion: primary/read-pool separation,
SQL read-only enforcement, measured foreground admission, mocked Redis network
boundary during real PG reads, replay/replacement/reclaim, rollback, all 80
same-shard concurrent increments, zero redundant kind lookups in 50 full hooks,
288 retained buckets after 600 rotations and all 40 snapshots below 100 ms.

The later unchanged opening-segmentation rehearsal timed out at its domain
`DELETE FROM opening_segmentation_runs`, before the event hook. That rehearsal
sets its own **50 ms** transaction deadline (not the normal 250 ms setting).
Focused reproduction used the same script, assertions and 50 ms deadline in
a fresh minimal disposable PG/Redis stack with no product consumers, after the
full runner had cleaned up. It passed 26 slices, replay/restart/preview coherence,
zero cached-read traversals and a foreground review in **8.24 ms**; setup plus
execution wall time **6.84 s**, excluding teardown. The temporary runner built
`backend/Dockerfile`, applied checked-in migrations and executed
`scripts/check_postgres_opening_segmentation.py`; owned resources were removed.
This supports environment sensitivity, not a claim of a successful local full
run or proof that the earlier timeout is harmless. Fresh CI must pass the entire
required plan on the published candidate.

[PostgreSQL raw samples and summaries](background-diagnostics-postgres-cost.json):
snapshot p50/p95 **11.986/21.153 ms** before versus **11.628/41.127 ms** after
100,000 events; maximum **41.780 ms**. Matched full event-accounting transitions
p50/p95 **14.264/34.757 ms** baseline versus **14.808/28.783 ms** instrumented;
median difference **0.544 ms**. No cross-condition or previous-run speedup claim.
These measurements came from the passed diagnostics stage of the failed local
durability run, with its other services present. Percentiles use nearest rank.

Independent compatibility measurement, after Docker cleanup and before focused
reproduction, with no concurrent local test/build:

```sh
PYTHONPATH=backend backend/.venv/bin/python scripts/benchmark-background-diagnostics.py
```

[SQLite artifact](background-diagnostics-cost.json): 1,000 current tasks;
snapshot p50/p95 **4.492/5.326 ms** before and **5.010/5.487 ms** after 100,000
events. Matched full known-kind event hooks: **2.864/3.961 ms** baseline,
**3.564/4.545 ms** instrumented, median difference **0.699 ms**. SQLite is
compatibility evidence, not PostgreSQL evidence. Original measurements in the
runbook are historical; these are the current correction's measurements.

Only evidence/docs/cost artifacts follow the tested code revision. CI owns the
complete final candidate, including public contract, frontend, engine, browser
and pinned performance validation. No local `make full` was run. No live queue
was paused, reset or cleaned.
