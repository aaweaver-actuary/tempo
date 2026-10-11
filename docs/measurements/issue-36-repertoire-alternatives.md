# Issue #36: repertoire alternative membership

## Selected development scope

Replace only Home's unexpected-but-legal connected repertoire membership scan.
Risks: identity drift, repertoire leakage, unavailable evidence mistaken for a
negative result, stale asynchronous publication, obsolete evidence after a
confirmed repertoire mutation, and repeated preparation during ordinary reviews.
The existing board ownership, expected/checkmate ordering, phone/demo fallbacks,
opening journal and attempt persistence remain the behavioral controls.

Smallest proof: named pure membership/parity/work-count regressions, followed by
Home regressions with controlled fetch/preparation completion. Run the affected
preparation and Home files once coherent, plus typecheck/lint and conflict/migration
guards. Real browser evidence belongs to recovery and held-drag preservation specs
on disposable PostgreSQL stacks. No local Python environment or broad runtime
baseline is required for these TypeScript changes. CI owns complete current-head,
current-base, captured-integration qualification; no overlapping local full gate.

Historical timing artifacts were inspected for scope selection, not reused as
evidence for this candidate. The recorded old position-store file was 0.018 s and
old Home study file 21.241 s; both were earlier revisions and smaller inventories.

## Coordination

Started from remote main `964b2a3a2ede4d9ac92288bb98a9ccba831d6b62` in an isolated
worktree. PR #172 is open at `e1661b3a42a79ae368304834db338d7e2cda5039`; its Home
patch concerns confirmed-review delivery. Keep its ordinary workspace invalidation
independent of repertoire membership invalidation so prepared progression does
not rebuild the index after every review. Issue #35 has no implementing open PR
in the inspected inventory; no teaching-history changes belong to this patch.
Merged #32 / PR #46 board ownership and restoration behavior remains protected.

## Equivalence and work counts

The named legacy baseline tests failed on main `964b2a3a` before Home integration:
400 prepared records caused 801 canonicalization calls (the scan plus failure
annotation); an authoritative edit left removed `d2d4` evidence classified as
`alternate` instead of `unverified`. The identical regressions pass after the
change. Pure lookup tests require exactly one query canonicalization, at most two
Map reads and one Set membership operation at 1k/10k/100k, and forbid reading source
records. The scan oracle makes 2N canonicalizations on the identical miss. Home's
incorrect-move path retains its separate failure-annotation canonicalization.

Preparation retains the original records, duplicate continuations are unioned,
and repertoire identity stays independent. Canonical identity remains precisely
the existing first four FEN fields. The loader publishes records and membership
as one accepted generation; confirmed mutations clear evidence synchronously.
Returning to training evicts only its pending line request. Obsolete request
success/failure cannot evict a newer pending read. Ordinary review refresh and
prepared-card advancement do not invalidate membership.

## Reproducible scaling and memory

Command (run separately from tests/builds):

```sh
node --expose-gc scripts/benchmark-repertoire-alternatives.mjs docs/measurements/issue-36-repertoire-alternatives.raw.json
```

Measured clean revision `71becf14650b9ae23735e6c2395e5782cdcd16f6`, seed `360036`,
v26.10.0 / darwin / arm64, wall time
189.26 s. Production-source fingerprint:
`d2f6dae2278a180c5cec1adb170f22552dadf77e722db31c5c76064aff492014`.
[Raw samples and memory observations](issue-36-repertoire-alternatives.raw.json)
include all three samples, p50/p95, before/after GC heap, heap capacity, RSS,
external/array-buffer memory and process maximum RSS for every fixture.

Each size/distribution uses a fresh process. The high-distinctness fixture uses
seeded unique eight-pawn layouts with a nine-ply legal king route; 90% of records
have distinct continuation keys. The duplicate-heavy fixture repeats the same
eight-ply Spanish route and truncates only its last route for the exact count.
All six production preparations return exactly the requested record count.
The original scan and lookup agree for all 48 identical paired hit/miss queries
per fixture. A warm-up precedes three samples per operation. Fixture generation,
bundling and warm-up are excluded from the samples. Cloning uses native
`structuredClone`; scheduling and transfer through a real worker, UI, service
reads and persistence are excluded. The workstation is shared, not a dedicated
benchmark host; no task-owned tests/builds ran concurrently with this accepted run.

Times below are p50 milliseconds; lookup is a batch of 48 queries (24 hits / 24
misses), rather than one query. Construction operates on the same already-prepared
source array; it does not hide preparation or cloning cost.

| Distribution | Records | Keys / memberships | Source preparation | Construction | Source clone | Snapshot clone | Legacy query batch | Indexed query batch |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| high-distinctness | 1,000 | 900 / 900 | 95.395 | 2.234 | 1.597 | 1.786 | 41.100 | 0.045 |
| high-distinctness | 10,000 | 9,000 / 9,000 | 1086.923 | 18.979 | 18.079 | 24.518 | 502.683 | 0.044 |
| high-distinctness | 100,000 | 90,000 / 90,000 | 11512.360 | 293.873 | 230.141 | 418.238 | 4269.636 | 0.047 |
| duplicate-heavy | 1,000 | 8 / 8 | 354.538 | 0.778 | 2.794 | 1.485 | 41.265 | 0.050 |
| duplicate-heavy | 10,000 | 8 / 8 | 1429.227 | 6.601 | 13.329 | 14.827 | 435.972 | 0.095 |
| duplicate-heavy | 100,000 | 8 / 8 | 8515.678 | 93.628 | 148.840 | 174.661 | 2674.772 | 0.048 |

| Distribution | Records | Observed retained heap delta (MiB) | Final process RSS (MiB) | Process max RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| high-distinctness | 1,000 | -0.036 | 95.7 | 126.4 |
| high-distinctness | 10,000 | 1.947 | 149.4 | 151.6 |
| high-distinctness | 100,000 | 21.099 | 302.2 | 473.7 |
| duplicate-heavy | 1,000 | -0.266 | 74.8 | 107.8 |
| duplicate-heavy | 10,000 | -0.309 | 98.7 | 127.1 |
| duplicate-heavy | 100,000 | -0.292 | 132.2 | 405.0 |

Heap deltas compare forced-GC snapshots before and after retaining the new index;
source records are retained on both sides. Small negative deltas reflect GC/JIT
bookkeeping noise, **not negative index allocation**. The eight-key duplicate
index is below this observation's resolution. The 90k-key case adds about 21 MiB.
RSS/max RSS include the entire measurement process, preparation and ephemeral
clones; they are not browser-only or isolated Map allocation claims. No memory or
timing threshold determines acceptance. These fixtures show algorithmic work and
memory tradeoffs; they do not establish production drop latency or a whole-app
speedup. Superseded random-game generation and one overlapping run were stopped;
their timing output was discarded and their logs remain in external diagnostics.

## Development validation

- Baseline command: `npm run test:unit -- tests/unit/repertoire-alternative-home-regressions.test.tsx -t 'issue36_home_query|issue36_authoritative_changes.*edit'`: 2 intended failures, 16.25 s, before Home integration.
- `make unit-file FILE=tests/unit/repertoire-alternative-index-regressions.test.ts`: 7 passed, 30.13 s.
- `npm run test:unit -- tests/unit/repertoire-alternative-index-regressions.test.ts tests/unit/repertoire-alternative-home-regressions.test.tsx tests/unit/study-position-store-regressions.test.ts tests/unit/position-search.test.ts tests/unit/study-regressions.test.tsx`: 105 passed, 1 navigation failure, 89.17 s. The misspelled position-search filter matched no file; the corrected file runs below. The navigation assertion diagnosed reuse of a prior pending request, then was repaired with targeted eviction.
- `npm run test:unit -- tests/unit/repertoire-alternative-home-regressions.test.tsx tests/unit/position-search-regressions.test.ts tests/unit/background-workspace-regressions.test.tsx tests/unit/board-authoritative-restoration-regressions.test.tsx tests/unit/board-drag-preservation-regressions.test.tsx`: all 53 passed, 28.71 s, including all 20 Home cases and targeted request isolation. These run against the uncommitted production sources subsequently committed in `6937b06a`; source preparation/index tests also passed in the preceding run.
- `npm run typecheck`: passed; local duration was not instrumented. `npm run lint`: passed with 10 existing warnings and no errors. Changed TypeScript/JavaScript files also passed direct ESLint; the bounded benchmark follow-up requires a fresh script lint.
- `npm run check:conflicts`, `node scripts/check-migration-inventory.mjs` (45), and `git diff --check`: passed before commits.

Local environment: isolated worktree, Node v26.10.0 / Vitest 5.0.1 on Darwin ARM64,
lockfile dependencies installed once with `npm ci`. An initial test process exited
11 while dependency installation was incomplete; it was not treated as evidence.
Test fixture corrections and controlled failing runs are retained diagnostically.
Recovery and held-drag browser results, exact tested revisions, cleanup and CI
current-candidate qualification will be added after execution. CI owns the complete
gate; no local `make full`, unrelated Python/FSRS change, live data reset, merge or
deployment belongs to this task.
