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
