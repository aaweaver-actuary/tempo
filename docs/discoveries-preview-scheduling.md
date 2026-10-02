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

The component regression `discovery_synthetic_workload_records_request_and_validation_counts`
uses 100 discoveries, fresh parsed responses, a controlled clock, one minute of
closed training, then opening the viewer, 20 rerenders and another minute.
Validation counts observe calls inside `previewMatchesDecision`, excluding other
chess move collection. Viewer-open values below are **cumulative**, not phase deltas.
Set `TEMPO_DISCOVERY_MEASUREMENT` to a writable JSON path to retain raw counts.

| Cumulative checkpoint | Before previews / peak / feed pages / validations | After |
| --- | --- | --- |
| Closed training, 60 seconds | 100 / 2 / 3 / 200 | 0 / 0 / 0 / 0 |
| Viewer open and another 60 seconds | 100 / 2 / 5 / 323 | 3 / 2 / 3 / 3 |

Comparison base: `dfbb66d67b314357e55c2030ff794a15415f316c`.
Current main after rebase: `937aee78a7fe6c399c9d3a665d7d7d2aa8fd08f1`.
Measured candidate source: `c06a9e1d98fac083e9d3cf632192042b5bca2300`.
Later evidence-only commits do not change product/test source; the final pushed
head and its CI result are recorded in PR #58. macOS ARM64, Apple M3, Node
v26.3.0, Vitest 5.0.1/jsdom; separate `.dev-copies` checkouts. Before uses base
product code with only the measurement fixtures ported. After uses the rebased
candidate. The candidate's viewer checkpoint has three actual validation
invocations and zero actual cache hits. Twenty rerenders add neither counter.
These are deterministic work counts, not elapsed-time or presentation metrics.

Commands (run separately from each checkout):

```sh
TEMPO_DISCOVERY_MEASUREMENT=/private/tmp/tempo-58-before-final.json npm run test:unit -- tests/unit/discoveries-tray-regressions.test.tsx -t discovery_synthetic_workload_records_request_and_validation_counts
TEMPO_DISCOVERY_MEASUREMENT=/private/tmp/tempo-58-after-final.json npm run test:unit -- tests/unit/discoveries-tray-regressions.test.tsx -t discovery_synthetic_workload_records_request_and_validation_counts
```

Three original baseline regressions failed: closed-training work, incremental
readiness and overlapping feed replacements (peak four versus two). Two further
regressions failed against the original PR head before the continuation fix:
false cache-hit accounting and preparation of an earlier returned feed entry.
The scheduler's 100-item retry-storm test retains one timer and no duplicates.

## Browser and performance evidence status

The focused disposable PostgreSQL browser runs passed: Discoveries 20/20
(24.3 seconds execution), held drag 3/3 (16.0 seconds), foreground queue
contention 1/1 (8.6 seconds). They used the pre-rebase continuation product;
the rebase does not change those product/test files. Main adds tactic-capture-only
CSS selectors outside these fixtures. CI owns validation of the final current-base
candidate. No tests, deadlines or performance thresholds were loosened.

Docker/loopback capabilities are now available. A base pinned performance run
completed all 60 held-drag trials and passed that scenario, with zero interruptions.
The **whole** `make perf` target failed the workspace no-long-task assertion
(observed 133 ms), taking 615.96 seconds; it is not a full performance pass.
The candidate attempt also failed that assertion (107 and 109 ms), then was
stopped when concurrent durability and visual test runs from other checkouts were
observed. Incomplete candidate samples do not support a paired comparison.

Exact attempted commands:

```sh
TEMPO_TEST_TIMING_DIR=test-results/performance/issue-33-before TEMPO_FULL_TEST_RUN_COMMIT=dfbb66d67b314357e55c2030ff794a15415f316c /usr/bin/time -p make perf
TEMPO_TEST_TIMING_DIR=test-results/performance/issue-33-after TEMPO_FULL_TEST_RUN_COMMIT=c06a9e1d98fac083e9d3cf632192042b5bca2300 /usr/bin/time -p make perf
```

The runner reads `TEMPO_FULL_TEST_RUN_COMMIT`; a standalone `TEMPO_COMMIT`
would be overwritten. Both attempts used identical performance/held-drag fixtures,
lockfile, browser configuration and image digest. Pinned Linux ARM64 Chromium
153.0.8010.12, production build, 1280×800, DPR 1, three repetitions × cold/warm
contexts × capture on/off. Apple M3 with eight host CPUs and 24 GiB memory;
shared Docker VM with four CPUs and 6,198,358,016 bytes memory, no per-container
limits. The controlled preview starts through explicit viewer demand, returns
to training and resolves during the hold. GPU/physical presentation and live
backend load are unmeasured. These are DOM/rAF proxies, not INP or physical latency.

Raw base trials, their manifest, the measurement-only base patch, deterministic
counts and correctness commands are retained in
[measurements/issue-33](measurements/issue-33/evidence.json).
The raw artifact's uncompressed SHA-256 is recorded in the evidence JSON.
The baseline is retained for inspection, **not** as a completed comparative
acceptance result. A fresh quiet paired run must include all repetitions and idle
controls, frame samples/interruption counts, p50/p95/max gaps, displacement and
capture costs. No drag improvement is established or claimed.

Issue #33's representative before/after trace acceptance remains open. PR #58
must use `Refs #33` until a valid paired measurement exists. The implementation
and deterministic work reductions do not substitute for that remaining evidence.
