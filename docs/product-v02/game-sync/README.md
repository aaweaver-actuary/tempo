# Continuous provider synchronization (#491)

This separate follow-on branch is based on frozen retained-history `0db8c980`.
It changes no live service, source account, setting or installed schema. Provider
activation, browser mounting and deployment remain unqualified.

## Public interface and ownership

The root supplies the receiving owner's existing
[`EncounterFeedbackPort`](../../../backend/app/contracts/product_v02/learning.py)
to `create_current_provider_worker(priority_intake=...)`. Missing intake fails
before any source read/claim. Games produces immutable encounter evidence and
calls only public semantic accept/control/status operations. It never constructs
a private Study owner, borrows its writer, writes scheduling tables or rebuilds
its queue. The receiver owns idempotent acceptance and durable obligations; Study
owns independent refresh, current validation and actual availability.

An accepted/pending receipt acknowledges evidence, not queue availability. Games
acknowledges that outbox delivery and stops delivery work. A lost acknowledgment
replays the exact original command. No private queue poll or new ingestion path
is required. #487 is supplying this decoupled intake/refresh; no duplicate ledger,
interface DTO, compatibility export or generic framework is created here.
The [composition sequence](../../../backend/app/product_composition/README.md#continuous-provider-public-priority-intake-489--491)
is the canonical cross-component drawing.

## Existing source reused

- Published #484/#485 adapters retain finite request/byte/page/month bounds,
  filters, cancellation and numeric/HTTP-date Retry-After behavior.
- #489 `2e6aa9d6` is remote-verified. Strict PGN proof is preserved separately
  from retained canonical ID/revision/fingerprint. Canonical row, page capture,
  frozen original outbox and cursor share the Games transaction. Changes/identity
  races hold or roll back instead of rewriting evidence.
- #483 `current_published_snapshot` supplies the explicit publication revision.
  The authored `current_snapshot` remains an independent audit API.
- Existing GameEffectKey, coordinator/store/runner, delivery controls and receipts
  are reused. No second comparer, ingestion workflow or scheduler is added.
- Preview uses the existing query-only reader profile; the API reader receives
  no writer credential. Games' optional worker owns only its own connections,
  cancellation, bounded units and stored synchronization state.

#489 `65881c5c` now emits immutable `GameEncounter` facts for new confirmed
introduced misses as well. Its exact producer patch is integrated without a
compatibility converter. Original frozen legacy lapse commands retain their
existing identity/path; none is relabeled. Study alone chooses current disposition.
The owner's 35 focused cases and dependency guard are reused unchanged.

## Explicit bounded forward windows

`ForwardWindow(overlap_seconds)` and `ContinuousSync` have no timer/database
lifecycle. They reuse saved provider/account, repertoire IDs, speed/rated filters,
window and interval. Fixed recurrence remains fixed. Explicit
`--follow --overlap-seconds N` alone selects consecutive windows: width 60 seconds
to 1 day, overlap 0..3600 and smaller than width. First scope is exactly saved;
subsequent completed windows advance contiguously from the previous covered end,
with bounded overlap/width. Outages drain rather than jumping to now.

Window/job identity stays stable on restart. Pending/rate-limited/failed/held
work retains its original cursor; a held provider does not hide the other one.
No recurrence means disabled. Changed saved scope is refused, not inferred.
The optional loop retains signal/cancellation ownership and finite `--once
--max-units N`. Existing readable controls and foreground/maintenance admission
precede enqueue and bounded work. Study-only and ordinary caps remain unchanged;
unknown controls, actual foreground activity and stop remain effective.
No startup hook, service registration or live configuration activates this code.

## Evidence and remaining activation boundary

`test_product_priority_intake_boundary.py` proves public accepted/pending receipt,
no availability polling, immutable lost-ack replay and unavailable-intake retry.
`test_product_provider_feedback.py` proves exact public-capability injection and
failure before source access when it is missing. The existing focused import/DAG
guard enforces no Games/Study/private-sibling dependency cycle. Existing cold
Study-maintenance import proof remains independent of Games.

The earlier inline provider adapter and its unqualified native fixture were
removed; earlier preparation receipts are not acceptance for this architecture.
Next integrate #487's published durable intake, then prepare/run the smallest
real persisted acceptance/replay and independent Study-refresh boundary. #489's
producer alignment is already consumed. Do not repeat unchanged provider/stop/kill suites.
No native/image/browser work runs ahead of current repair/R11 priority.

The new main guidance at `d87accc5` has been read and applies here. The parent
allocates ingestion/sync interfaces to #491, root wiring to #501 and guards to
#482. Mechanical interface moves await the exact revision-2 allocation path/
commit; it is not in this branch or the checked published main/#501 baseline.
No owner paths, interface map or receiving export names are guessed or duplicated.

Source migration052 was reused unchanged from published `f137d970`. It adds only
Games tables. Readiness remains schema051, so ordered upgrade/readiness number
and packaged reader/writer role qualification require a coordinated owner window.
No live migration or activation is authorized from this lane.

Prepared read-only next-window preview, after candidate schema/configuration:

```sh
python -m app.product_composition.game_sync_current --workspace legacy \
  --preview --follow --overlap-seconds 300
```

It reports `executable=false`. Default run still refuses a missing qualified
receiver. After decoupled intake/native/profile qualification, the release owner
can explicitly bind it using saved settings and the existing writer process,
`TEMPO_PRODUCT_GAME_SYNC_ENABLED=1`, `TEMPO_DATABASE_WRITE_URL`, and the existing
`TEMPO_DATABASE_READ_URL` for previews. `--follow --overlap-seconds 300 --once
--max-units 50` selects at most one due window per configured provider, then drains
bounded units. This is preparation, not a working live activation command yet.

Window completeness, compared/unresolved positions, pending/held receipts and
actual Study availability are distinct. Zero eligible misses is never all-correct.
#490 usability/onReviewGap mounting remains separate; no invented source URLs or
expected moves are added to standalone receipts.
