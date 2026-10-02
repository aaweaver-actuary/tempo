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
The open viewer has a rolling two-item look-ahead; unavailable items do not occupy
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

Base: `dfbb66d67b314357e55c2030ff794a15415f316c`, macOS ARM64,
Node v26.3.0, Vitest 5.0.1/jsdom. Before used the base component with the same new
measurement harness; after used the uncommitted candidate. These are deterministic
work counts, not elapsed-time or physical-presentation measurements.

Three focused baseline regressions failed (2.10 seconds): closed-training work,
incremental readiness, and overlapping feed replacements. The latter recorded a
peak of four simultaneous previews; the scheduler candidate holds it at two.
The separate 100-item scheduler retry storm checks every item retrying and repeated
wakes without duplicate queue entries or exceeding two active requests.

Docker access and localhost binding were denied in the implementation environment.
No local browser, pinned performance, or held-drag before/after trace was obtained.
Existing held-drag fixtures now start a preview through explicit viewer demand,
close the viewer, then release that in-flight response during the held drag. CI
must supply browser/pinned verification. No drag latency improvement is claimed.
