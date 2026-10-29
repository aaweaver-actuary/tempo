# Continuous provider synchronization (#491)

## Bounded Game Review receipt-read checkpoint

The backend Game Review GET now preserves the current public owner's complete
EncounterReceipt, including pending first introduction and control/hold status.
It chooses `encounter_status` from the original immutable effect kind; legacy
receipts still use their legacy operation/wire form. The exact Python receipt
union from #536 is consumed, with no new DTO. The public Study surface and affected
import guard are reused from #539. No private Study implementation or queue read
is added. The [owning interface view](../../../backend/app/games/feedback/interfaces/README.md#game-review-receipt-reads)
records the query and serialization boundary.

The paired source consumes #536's exact TypeScript receipt fragment while retaining
all 55 existing contract exports. It adopts #490's receipt-only component at
`241bced7` through the complete four-file delta over #514 `d55dd02a`; component
behavior is unchanged. The focused `product-game-review-api-receipts.test.tsx`
feeds captured current Python GET responses through the canonical client/parser
and real view/board. Pending introduction stays pending, and reads make no POST.

This checkpoint does not mount Game Review or enable a provider worker. Root
wiring must supply the rich public status capability and the existing authorized
command boundary. Packaged/user-browser and live acceptance remain separate;
no API-reader writer permission is introduced. The broader usability/refresh
checkpoint `507111af` is not required by this minimal receipt integration.

This separate follow-on branch is based on frozen retained-history `0db8c980`.
It changes no live service, source account, setting or installed schema. Provider
activation, browser mounting and deployment remain unqualified.

## Current provider delivery candidate (#491)

The remaining sync candidate is based on `adfc8de8`, preserving the merged #554
reader/schema52 behavior. Settings → Games supplies explicit per-provider
account, UTC window, speeds, rated policy, repertoires and recurrence. Configure
and manual run use the existing durable command gateway; reader HTTP receives
no write credential. An uncertain save/run retains its operation identity.

Only an explicitly started `games-feedback` Compose profile runs acquisition:

```sh
docker compose --profile games-feedback up -d --no-deps game-feedback-worker
docker compose stop game-feedback-worker
```

These are later release-owner commands, not actions executed by this lane.
The process reuses the established writer profile, is capped at 0.15 CPU,
256 MiB and 96 PIDs, and owns at most one HTTP connection and one bounded
database section. It is absent from the default profile and Study readiness.
The existing optional-service allocation is 1 CPU / 2048 MiB. Adding this profile
with all baseline optional services would require 1.15 CPU / 2304 MiB and is not
an approved allocation. A configuration with the existing Maia service absent
and Games present would total 0.85 CPU / 1280 MiB; this is a conditional capacity
calculation, not a claim that Maia is stopped or capacity is available on the
user's host. Activation remains with the release owner; no other service's cap,
global resource policy or running state is changed here.
Saved recurrence uses the exact configured window; blank recurrence remains
off. Manual jobs still drain with recurrence off. Changing configuration stops
stale clients before their next unit; the opted-in process's restart policy
rebuilds clients from the new saved scope. No account/filter is inferred.

The concrete root binds durable public priority intake only. Accepted/pending
stays distinct from available; Study independently refreshes accepted obligations.
New provider evidence and retained-history replay use the same canonical source
identity and published repertoire generation. No engine, new migration, queue
writer, live activation or automatic history pass is added. Assembled provider,
browser and process results are still the #491 delivery owner's acceptance.

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

Source migration052 remains unchanged from published `f137d970`. The merged
#554 aligned readiness to schema52 and qualified its packaged reader boundary.
No schema/grant change or live migration/activation is authorized in this lane.

Prepared read-only next-window preview, after candidate schema/configuration:

```sh
python -m app.product_composition.game_sync_current --workspace legacy \
  --preview --follow --overlap-seconds 300
```

It reports `executable=false` because preview never dispatches work. The concrete
optional entry point now binds the public receiver using saved settings and the existing writer process,
`TEMPO_PRODUCT_GAME_SYNC_ENABLED=1`, `TEMPO_DATABASE_WRITE_URL`, and the existing
`TEMPO_DATABASE_READ_URL` for previews. `--follow --overlap-seconds 300 --once
--max-units 50` selects at most one due window per configured provider, then drains
bounded units. Live activation remains release-owned and pending qualification.

Window completeness, compared/unresolved positions, pending/held receipts and
actual Study availability are distinct. Zero eligible misses is never all-correct.
#490 usability/onReviewGap mounting remains separate; no invented source URLs or
expected moves are added to standalone receipts.


## Bounded Game Review composition checkpoint

The full workspace now exposes Games and lazily mounts the existing published
view. Its gap action opens Repertoire without creating or editing content.
Independent Study retains its existing navigation exclusion and physical entry.
`main.py` mounts the existing router and classifies only its optional route
namespace as background work; GET query-only scope remains unchanged.

`CurrentGameReviewOperations` constructs no connection/worker on registration.
GET operations use only `TEMPO_DATABASE_READ_URL`, existing bounded
`DedicatedGamesConnections(read_only=True)`, and the Study owner's actual receipt
status operation. No source capture, scheduling, admission or queue work is needed
for this status. Optional read failure is a local 503; it cannot supply stale
availability. Explicit commands retain their writer profile and fail without it.
No reader grants or credentials are broadened.

Focused source proof uses the real application router/middleware and owner status
with scripted SQL, plus the mounted full-workspace view and retained browser
attempt token. This does not establish actual PostgreSQL grants, packaged process
isolation or genuine-data acceptance. The existing schema052 remains a later
release prerequisite; this source does not apply migrations or change readiness.
The #547 repair stack remains separate and its five files are untouched.
The legacy feedback retry action is hidden when the actual receipt is an
`EncounterReceipt`: that endpoint supports stored lapse replay only. Pending
encounters remain accurately labeled and use their existing delivery/owner path;
this checkpoint adds no encounter retry endpoint.
