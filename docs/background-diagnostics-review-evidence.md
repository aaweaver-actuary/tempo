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
