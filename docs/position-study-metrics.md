# Position-owned Study metrics

`database/tooling/position_study_metrics.py` is a pure reducer over a caller
tuple of immutable accepted-receipt values, capped at 1,000 attempts and 256
position events per attempt. It opens no connection, selects no time window,
and reads no clock. A future adapter must also enforce its read-only
transaction, indexed range predicates, and page limit before calling it. No
such accepted position-receipt adapter exists in this change.

## Metric contract

| Metric | Definition |
| --- | --- |
| Distinct positions tested | Unique `(repertoire_id, exact_position)` among accepted first-response receipts with Good, Again, or observation-only `observe`. Alias routes collapse; pending/skipped positions are excluded. |
| Actual response events | Unique immutable accepted event IDs with Good, Again, or `observe`. Two independently accepted events remain two events even if they concern the same position. |
| Distinct positions per minute | Distinct tested positions divided by the caller's positive elapsed window in minutes. Both UTC-aware start/end instants and an aware `accepted_at` inside the inclusive window for every counted event are required. Missing timestamps make this unavailable; any present event outside the window is rejected. It is a throughput measure, not a learner-quality claim. |
| Repeated moves avoided | Sum of explicit receipt counters for prevented repeated learner moves. It is never added to positions tested. |
| Nondue first wrong | Accepted Again events whose Study-owned due-at-issuance witness was false. An accepted first wrong remains included if the attempt was abandoned later. |
| Due Good / due Again | Actual accepted Good or Again events with a true due-at-issuance witness. Separately, positive positions per Study day counts each exact repertoire position at most once for the authoritative `study_day`; it is unavailable if a positive receipt lacks that day. |
| Duplicate attempts suppressed | Sum of explicit idempotency receipts/counters; suppressed attempts do not become response events. |
| Prefix holds / fallback | Counts supplied in accepted attempt receipts. They measure planner outcome signals, not tested positions. |
| Queue latency | Mean and sample count of caller-supplied milliseconds from durable actionable determination to actual position-attempt issuance. Queue presence, prefetch, and admission are not issuance. |
| Review-save latency | Mean and sample count of supplied milliseconds from first response submission to durable accepted effect/save receipt. |
| Donor eligibility / migration exclusions | Counts are unavailable unless every receipt in the input page supplies the relevant value. The eventual source must document its candidate population, ownership proof and exclusion reasons; these counters do not authorize migration. |
| 7/30-day retention | Always unavailable in this reducer. A future source may report a matured cohort only after the full interval has elapsed and real follow-up receipts exist. Synthetic fixtures, unelapsed cohorts and missing follow-up are not retention observations. |

Study day is carried by the owning receipt and is not reconstructed from UTC or
the machine timezone. Good and Again counters count each unique accepted event
ID, including separate same-day positive effects; the daily positive-position
count is a separate deduplicated metric and does not hide duplicate effects.
Missing or naive window/event timestamps leave throughput unavailable; event
counters can still be reported when receipt identity and grade are present.
Attempt-level signals with no released source
are `None` (unavailable), not zero. Conflicting payloads sharing one event or attempt ID must
be rejected by the source adapter; this reducer keeps the first occurrence and
cannot establish receipt authenticity.

## Existing source limits

The current legacy migration `030_opening_decision_evidence.sql` stores
presentation and queue context, an opening-evidence attempt journal, per-decision
observations and aggregate shadow summaries. Its recent-observation index is
for `(decision_id, observed_at, attempt_id, decision_index)`. The existing
`postgres_opening_evidence.py` writer describes this as shadow persistence and
does not write cards, reviews, quotas or queue state. These opening-evidence
observations do not establish the future position-owned accepted Grade receipt,
exact-position identity, due-at-issuance witness, actionable-determination
timestamp, or migration donor population. They therefore cannot back these
metrics as real Study outcomes.

The A1 exact-position identity and C1/C2 accepted grade/effect receipt
interfaces are still pending. This module defines the bounded input shape for
the final released receipt source; it does not claim the current runtime emits
that shape. N2 owns later measurement against integrated released inputs.
There is no SQL, schema change, migration, scheduler write, dashboard, or live
data claim in this deliverable.
