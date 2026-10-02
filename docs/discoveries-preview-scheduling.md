# Discoveries preview scheduling (#33)

Recommendation previews use one `DiscoveryPreviewScheduler` per mounted tray.
Its single pending queue and active registry enforce a limit of two requests,
including initial preparation, feed changes, retries and viewer requests.
Priority is explicit requested/current item, two-item viewer look-ahead, unread
safe-break candidate, then background preparation; ties follow feed order.
An explicit request elevates queued work but does not interrupt an active request.

Closed, visible, idle trays prepare at most two selected items per successful
complete feed refresh. Newly ready items enter navigation immediately in feed
order without replacing the active item. Saved-card items do not need previews.
The open viewer has a rolling two-item look-ahead, filling unused slots from the
nearest preceding entries so returned items become navigable; unavailable items do not occupy
that buffer. There is no all-feed preparation requirement.

Home supplies `speculativePreparationPaused` from its existing workspace and
attempt state. Player/guided/opponent-reply training phases and the tactics,
endgames and Builder workspaces pause closed-tray preparation. Existing editing
`interactionBlocked` also pauses it. This new prop does not alter board input,
interaction timing, or the existing safe-break modal rules.

## Feed freshness

| State | Automatic full refresh | Preview launches |
| --- | --- | --- |
| Visible, viewer open | On open if missing/stale, then every 30 seconds | Requested/current item and two-item look-ahead |
| Visible, closed, idle | Initially, then every 30 seconds | Two selected candidates per complete refresh |
| Visible, closed, active foreground workspace | Deferred | None |
| Hidden | Periodic refresh deferred | Only existing explicit viewer demand |
| Offline | Automatic requests deferred | None |
| Explicit open request/manual refresh | Prompt complete refresh, including during training | Prioritize requested item; no bulk preparation |
| Confirmed admission/command reconciliation | Required complete refresh remains independent | Ordinary demand policy |

Nominal feed staleness while eligible and online is 30 seconds plus fetch time.
There is no finite freshness guarantee during uninterrupted closed-tray training,
hidden time, offline time, or service failures. Visibility/reconnect wakes coalesce
one necessary refresh when eligible; otherwise freshness remains deferred until
idle or explicit demand. One refresh may run, with at most one forced follow-up
for commands that complete after the current read started.

All pages still use the existing endpoint and schema. Automatic pagination stops
between pages if demand disappears; incomplete reads never replace the last
complete snapshot or invent a partial count. Explicit and command-required reads
finish pagination independently of that policy. Counts retain their existing
meaning (complete loaded feed, or ready/unread items as appropriate).

## Ownership, retry and validation

Identity includes discovery ID, evidence fingerprint and item generation. Removal,
return, fingerprint change, or changed decision inputs creates new ownership.
Unchanged refreshes preserve valid results. Queued obsolete work is discarded;
in-flight obsolete results cannot write previews, statuses, retries or validation.
An obsolete request continues to occupy capacity until it settles. Unmount aborts
owned client requests and fences publication. HTTP abort is **not** a guarantee
that the server stopped computation.

Waiting, failed and expected inactive/stale 404 results retry after 30 seconds
while demanded. Unavailable results do not automatically retry. One earliest-due
retry timeout replaces scanning; paused demand has no retry timer. Inactive 404s
retain silent, coalesced feed reconciliation rather than generic error reporting.

Zod parsing remains required. Legal-move validation is cached by item identity,
decision inputs and parsed result object, with a 256-entry bound and removal
cleanup. Rendered ready metadata reuses the checked result and decision identity;
replaced positions/results cannot reuse that validation. The tray DOM element's
`discoveryPreviewDiagnostics()` returns bounded counters without render churn.
`validationInvocations` counts executions of the expensive validation callback;
`validationCacheHits` counts saved answers returned by `DiscoveryPreviewValidationCache.matches()`.
React reuse of checked metadata increments neither counter.
`measureTempoDragPhase("discovery-preview")` remains around request/validation.

Admission enqueueing, startup recovery, the three-second admission flush,
confirmation, command-time eligibility and confirmed training-queue refresh never
enter this scheduler.

## Reproducible evidence

The final source measurement compares pre-#33 base
`dfbb66d67b314357e55c2030ff794a15415f316c` against candidate
`1496021f82bfddd1df8b4276fcc57f7e7dd853df`, rebased onto main
`f054fbcb4b2bc22025bd4ceb2a7569675b3080ad` (merged PR #56).
Later evidence-only commits preserve these product, test and build inputs; their
final head and required CI result are recorded in [PR #58](https://github.com/aaweaver-actuary/tempo/pull/58).

[Dedicated paired run 36997589752](https://github.com/aaweaver-actuary/tempo/actions/runs/36997589752) passed on one
`ubuntu-24.04-arm` job. Base and candidate ran sequentially, with no unrelated
containers or test/build workloads on that runner. The base received only the
[measurement-fixture patch](measurements/issue-33/base-measurement-harness.patch);
no product changes were ported. Twelve harness inputs, including the full
performance spec, fixture helpers, lockfile, configuration and launcher, matched
byte-for-byte. The workflow and orchestrator are retained only on the temporary
measurement branch at `9fd701d8cd19360491522e9aae5a3485f13e04bd`, outside this PR.

The runner used Node v22.23.3, pinned Linux ARM64 Chromium 153.0.8010.12,
production build, 1280×800, DPR 1, and blocked service workers. Host/Docker
capacity was four CPUs and 16722010112 bytes;
CPU model was unavailable, and there were no explicit per-container limits.
Both revisions used image digest
`sha256:eff16c30e6f3f4af0a03fa4b706120d5e9b0891c344a27d64559aff5900a4a27`.
Dependencies and image caches were prepared before collection. Independent
contexts retain the fixture's cold/warm distinction and alternating capture order.

### Deterministic 100-item workload

`discovery_synthetic_workload_records_request_and_validation_counts` uses fresh
parsed responses, a controlled clock, one minute of closed training, opening,
20 rerenders and another minute. Validation counts observe actual legal-move
collection inside `previewMatchesDecision`. Viewer-open values are cumulative.
The case ran once on each revision, separately after performance collection.

| Cumulative checkpoint | Before previews / peak / feed pages / validations | After |
| --- | --- | --- |
| Closed training, 60 seconds | 100 / 2 / 3 / 200 | 0 / 0 / 0 / 0 |
| Viewer open and another 60 seconds | 100 / 2 / 5 / 323 | 3 / 2 / 3 / 3 |

The candidate reports three actual validation invocations and zero cache hits;
rendering checked metadata does not manufacture either counter. Direct cache
coverage independently proves a genuine hit. These are work counts, not timing
or presentation metrics. The overlapping-trigger regression separately reproduced
peak four before versus two after; the retry storm retains one timer and no duplicates.

### Held-drag and main-thread comparison

Each revision passed all four performance cases and completed all 60 holds:
three repetitions × five workloads × capture on/off × cold/warm contexts.
Every combination occurs exactly once. All discovery workloads completed, all
holds remained uninterrupted, and all injected stalls were detected. The table
retains both discovery preparation and idle controls; each row contains six holds.
Gaps are milliseconds, displacement is CSS pixels. Counts and values are rounded
only here; raw samples and full-precision summaries are retained.

| Workload / capture | Frame / displacement samples, before→after | Before gap p50 / p95 / max | After gap p50 / p95 / max | Displacement p50 / p95, before→after | Interruptions |
| --- | --- | --- | --- | --- | --- |
| idle, capture on | 618→639 / 612→633 | 16.70 / 17.20 / 17.45 | 16.70 / 17.24 / 20.36 | 0.940 / 11.043→0.940 / 11.043 | 0→0 |
| idle, capture off | 614→625 / 605→618 | 16.70 / 17.24 / 30.75 | 16.70 / 17.23 / 17.76 | 0.940 / 11.043→0.940 / 11.043 | 0→0 |
| discovery-preparation, capture on | 655→654 / 645→650 | 16.70 / 17.30 / 22.77 | 16.70 / 17.26 / 20.96 | 0.895 / 11.043→0.895 / 11.043 | 0→0 |
| discovery-preparation, capture off | 654→658 / 646→652 | 16.70 / 17.26 / 21.88 | 16.70 / 17.29 / 17.82 | 0.895 / 11.043→0.895 / 11.043 | 0→0 |

Captured discovery frame cost p50/p95 was 0.010/0.025 ms on both revisions;
event cost was 0.015/0.035 ms on both. Idle frame cost was also 0.010/0.025 ms;
idle event cost was 0.015/0.035→0.015/0.040 ms. Capture-off runs have no recorder
cost samples. These costs cover synchronous recorder callbacks, excluding GC
outside those callbacks and other observer work.

Each revision retained six `discovery-preview` phase completions during captured
discovery holds, with no buffer truncation. Supported long-task observation
recorded zero tasks during idle/discovery holds and six 80 ms tasks for the
captured stall control. Phase spans include request waiting; they are not CPU-time
measurements. The existing report reader produced no advisory signals or stale
artifact exclusions.

Request and validation volume falls substantially. Controlled single-preview
held-drag gaps and displacement are effectively unchanged; no drag-latency
improvement is established. These DOM/rAF proxies are not physical display
latency or INP. Routed fixtures omit server computation, live network/backend
load, the personal study dataset and physical GPU/presentation behavior.

### Commands, results and retained artifacts

From the respective isolated checkouts on the dedicated runner:

```sh
TEMPO_TEST_TIMING_DIR=test-results/performance/issue-33-before TEMPO_FULL_TEST_RUN_COMMIT=dfbb66d67b314357e55c2030ff794a15415f316c TEMPO_CI_REPORT=test-results/performance/issue-33-before/playwright-results.json make perf
TEMPO_TEST_TIMING_DIR=test-results/performance/issue-33-after TEMPO_FULL_TEST_RUN_COMMIT=1496021f82bfddd1df8b4276fcc57f7e7dd853df TEMPO_CI_REPORT=test-results/performance/issue-33-after/playwright-results.json make perf
TEMPO_DISCOVERY_MEASUREMENT=<checkout>/test-results/performance/issue-33-<before-or-after>/deterministic-workload.json npm run test:unit -- tests/unit/discoveries-tray-regressions.test.tsx -t discovery_synthetic_workload_records_request_and_validation_counts --reporter=default --reporter=json --outputFile.json=<checkout>/test-results/performance/issue-33-<before-or-after>/unit-workload.json
node scripts/report-performance.mjs --directory <candidate>/test-results/performance/issue-33-after --baseline <base>/test-results/performance/issue-33-before
```

`TEMPO_FULL_TEST_RUN_COMMIT` is required; standalone `TEMPO_COMMIT` is overwritten.
The [command receipt](measurements/issue-33/ci-pair-36997589752/run-evidence.json)
contains exact expanded paths, environments, timestamps, statuses and durations.
`make plan` also passed before collection.

| Command | Result | Command wall time |
| --- | --- | --- |
| Base `make perf` | 4/4 cases, 60/60 holds | 200.11 s |
| Candidate `make perf` | 4/4 cases, 60/60 holds | 203.08 s |
| Base named workload | 1 passed | 2.43 s |
| Candidate named workload | 1 passed | 2.08 s |

The raw compressed reports, run manifests, hashes, all ten held summaries,
main-thread controls, deterministic counts and comparison checks are in
[the final pair](measurements/issue-33/ci-pair-36997589752/comparison.json).
The complete CI artifact additionally retains logs, all four browser reports and
Playwright diagnostics. A prior valid pair against `89e6e09` is retained
[separately](measurements/issue-33/ci-pair-36996539621/comparison.json);
it is not presented as evidence for the rebased candidate.

Earlier local attempts remain historical evidence in
[the evidence index](measurements/issue-33/evidence.json): a workspace assertion
failed and concurrent shared-VM tests invalidated the candidate collection.
The first dedicated setup attempt stopped before collecting samples because
container warm-up preceded host dependency installation; corrected ordering is
recorded in the benchmark branch. Failed logs/artifacts remain in
[run 36996282414](https://github.com/aaweaver-actuary/tempo/actions/runs/36996282414).
Neither failures nor incomplete local samples supply a comparative verdict.

The representative before/after trace criterion is now supported. `Closes #33`
is justified once the final candidate's required quality gate also passes.
No thresholds, deadlines, quarantine or visual baselines were changed. CI owns
complete current-head validation; no local `make full` was run for this evidence
follow-up. Earlier focused correctness commands and their actual revisions remain
in the evidence index and PR description, without attributing them to newer code.
