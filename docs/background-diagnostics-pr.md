# Proposed PR: Distinguish useful background progress from queue and engine churn

Closes #37

Tempo's existing activity projections can report successful slices while useful
analyses remain blocked. Add a versioned, redacted diagnostics endpoint and debug
bundle projection with separate eligibility ages, bounded lifecycle counters,
worker admission/handler/dispatch timings, accepted engine search outcomes and
PR #49 priority generation calculator/publication counters.

This is observability only. Scheduling, lease capacity and admission policy remain
unchanged; #38/#39 and frontend Discoveries are outside this change. Dirty origins
survive retries, deferrals and active generation replacement. Persistent metrics
use 24-hour retention in 5-minute buckets with 16 shards and finite labels;
request queries have a 100 ms DB budget and explicit unavailable results.
Counters commit with accepted domain outcomes and flush after domain writes in
sorted lock order. Old callbacks accept missing timing as unknown.

Metric definitions, units, reset/window semantics, query costs and a sanitized
quiet/training/drain runbook are in `docs/background-diagnostics.md`; validated
snapshots and measured compatibility costs are adjacent JSON artifacts. Named
regular-suite regressions are registered in `tests/REGRESSIONS.md`.

Validation on macOS ARM64, isolated clone from current main
`dfbb66d67b314357e55c2030ff794a15415f316c`:

- Focused Python: 271 passed in 5.77 s. Broad Python: 807 passed / one missing route
  audit failed in 51.79 s; fixed its reader declaration. Final affected route and
  diagnostics files: 26 passed in 1.77 s. See runbook for exact commands and source
  timing distinctions; no full-Python success is inferred from this sequence.
- TS producer/consumer/debug and engine request contracts: 26 passed across four
  files (16 in 969 ms, 10 in 1.36 s). Typecheck and lint pass (9 existing warnings).
- Stockfish smoke passes; runner harness 35 passed in 599 ms; diff whitespace clean.
- Isolated SQLite benchmark: snapshots p50/p95 1.568/1.587 ms with 1,000 tasks;
  after 100,000 events 1.558/1.575 ms. Matched transitions add 0.191 ms median.
  This is not PostgreSQL contention evidence.
- Disposable PostgreSQL regression/measurement is wired into the regular
  schema-upgrade scenario. Local execution unavailable: Docker daemon access is
  denied and elevation is unavailable. CI owns required final candidate
  verification, including PostgreSQL/build/browser/pinned checks. No local full
  gate or live-queue actions were performed.

Implementation evidence was obtained on the isolated local candidate before
publication. Final required CI is pending on the published candidate; this PR
should not merge until its required current-candidate checks pass.
