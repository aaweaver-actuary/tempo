# Continuous game feedback — #491 preparation checkpoint

This is unmerged source preparation under [#491](https://github.com/aaweaver-actuary/tempo/issues/491)
and the pinned [SPEC v0.2](https://github.com/aaweaver-actuary/tempo/blob/6885b9420320517ac74df47e631ed796da96c94a/SPEC.md).
The complete A03/A04 product workflow is **pending**, not installed or deployed.
#488 retains the entire first shared-root editing window and does not depend on
this checkpoint. No root, Settings, Compose or central registration is changed.

## Usable private interfaces

[`ProviderAcquisition`](../../../backend/app/product_composition/game_sync.py)
adapts the concrete async #484/#485 operations to the frozen synchronous
`GameAcquisitionPort.fetch_page(account, window, continuation)` seam. Async worker
callers can use `fetch_page_async`. It returns the actual `GamePage` subtype,
including Chess.com's original metadata; no projection discards provenance.
It does not persist, retry, advance a checkpoint, choose a window/account/filter,
or assign a durable source revision. #489 owns those decisions and state.

`create_game_sync(policies, store, snapshots, learning, clock)` now wires the
published #489 `Coordinator` / `FeedbackRunner` to these actual providers,
#486 normalization/comparison, #483 index and the injected #487 Learning owner.
The returned `GameSyncRuntime` offers `run_once`, `request_stop` and `close`.
It starts no process or timer and closes no borrowed store/snapshot/Study
capability. A shared cancellation event connects its stop request to provider
cooperative cancellation. The worker launcher still needs a separate process.

`WorkflowAcquisition` translates numeric or HTTP-date provider Retry-After headers
to #489's numeric `AcquisitionFailure.retry_after`; otherwise its retry arithmetic
cannot consume the header string. `sync_job` creates the actual #489 `JobSpec`
from explicit caller choices, binding the providers' casefolded account and exact
filter facts without changing saved Settings. Normalizer/comparer wrappers use
the original record so provider time precision remains authoritative. Current
`compare_record` normalizes again internally; this is bounded existing behavior,
not another comparison policy or writer.

`AcquisitionPolicy(provider, speeds, rated, source_revision)` requires explicit
filters. `rated=None` means both; `True` means rated-only. Lichess additionally
supports `False` for casual-only; Chess.com rejects that unsupported choice
instead of widening it. The default resource bounds are 100 Lichess rows,
8 MiB response, 10-second socket budget and 30-second total I/O budget. Hard
caps are 1000 Lichess rows, 8 MiB Lichess / 32 MiB Chess.com, 15-second socket
and 30-second total I/O. Each call owns a credential-free client with one
connection, redirects and ambient environment configuration disabled. Errors
retain provider Retry-After/checkpoint facts; `AcquisitionDeadline` retains the
input continuation. The caller supplies cancellation. No retry or sleep occurs.

[`create_game_review_router(operations)`](../../../backend/app/games/feedback/http.py)
constructs an **unregistered** FastAPI router. `GameReviewOperations` lists the
minimal synchronous consumer capability for an injected owner facade:

| Method | HTTP route | Effect |
| --- | --- | --- |
| `recent_games` | GET `/api/product/game-review/games` | Stored `RecentGames` only |
| `game_detail` | GET `/api/product/game-review/games/{provider}/{game_id}?repertoire_id=...` | Stored `GameDetail` only |
| `sync` | POST `/api/product/game-review/sync` | Enqueue configured provider job with explicit operation ID |
| `retry_feedback` | POST `/api/product/game-review/feedback/retry` | Resolve current stored Finding and replay its logical command |

The two POST request envelopes use the shared provider/Finding values, not a new
game or learning DTO. Extra account/filter fields are rejected. The concrete
owner must validate current stored evidence and durable operation replay; a
well-formed client Finding is never authority. Responses preserve exact owner
`LearningReceipt` values: `applied` is not changed to `available`. Router reads
never call acquisition, comparison, admission or retry. Missing/unavailable
projection handling belongs to the injected owner; no fallback fabricates data.
The facade is not wired until that implementation exists.

[`createProductGameReviewApi`](../../../app/product-composition/game-review.ts)
is the private frontend endpoint composition for the published #490 client.
It maps those exact four operations, percent-encodes game IDs and repertoire
query context, and performs no I/O at construction. Construct it once in the
optional shell and mount the actual `GameReviewView` after root reservation.

## Actual dependency and failure boundary

```mermaid
flowchart LR
  Async["#484 Lichess / #485 Chess.com\nimplemented concrete async acquisition"]
  Bridge["#491 ProviderAcquisition\nimplemented private composition"] --> Async
  Worker["#491 runtime factory\n#489 runner + coordinator"] --> Bridge
  Worker --> Games["#489 Games-owned durable store\nborrowed; migration activation pending"]
  Worker --> Compare["#483 index / #486 comparer\nsame source seams as #488"]
  Worker -->|"accept_lapse / control_effect"| Study["#487 Study owner\nborrowed capability"]
  UI["#490 Game Review"] --> Client["#491 endpoint composition"]
  Client -. "root mount pending" .-> HTTP["#491 unregistered HTTP adapter\nstored reads / explicit commands"]
  HTTP -. "owner facade/projections awaited" .-> Games
  Study --> Queue["ordinary Study queue / review lifecycle"]
```

Solid links are implemented here or supplied owner operations; dotted links are
pending integration. #488's preview/delivery seam is retained unchanged. No
producer is added to Study's startup, readiness or save
path. This source checkpoint alone does **not** prove process/host/database
resource isolation. No Study database connection is acquired by either new file.
Existing legacy background entry points remain unchanged. The current Compose
`background-worker` uses Celery `app.celery_app`, queue `background`, concurrency
1, 0.35 CPU and 448 MiB; that is a legacy launch fact, not proof of the new worker's
independence. A separate optional process must use #489's public run-once/stop
interface without importing the HTTP application root or Study lifecycle.

## Exact later shared changes — proposed, not reserved or edited

After #488 releases its window, publish a short #481 reservation before editing:

- `backend/app/main.py`: register the factory-created router using the concrete
  Games facade; no optional job starts or blocks readiness there.
- `app/types.ts`, `app/components/navbar.tsx`,
  `app/lib/study-workspace-policy.ts`, `app/lib/workspace-data.ts`, and
  `app/views/legacy-workspace.tsx`: add the Game Review view and lazy #490 mount
  in the retained optional shell. Preserve the existing Home/Study controller
  instance and active attempt; selecting Game Review cannot issue Study commands.
- `backend/app/contracts/transport/settings.py`,
  `backend/app/settings_commands.py`, `app/views/settings_view.tsx`,
  `app/utils/settings.ts`: inspect the current owner contract before adding any
  field. Existing provider usernames and `auto_sync_minutes` are reusable; do not
  overwrite an account or infer window/speed/rated filters. Games-owned persistent
  window/filter/checkpoint settings require #489's store contract first.
- Central dependency/test/Compose registration only for the concrete new worker
  and exact three #491 suites. Add a central diagram-index link to this canonical
  drawing, rather than copying it. #488 owns its own dependency registrations.

Existing coverage settings reject updates while their analysis owner is held.
D04 population/time controls remain unset, and repertoire weight storage remains
with its Study introduction owner. Neither is silently chosen or made a game
feedback prerequisite. No schema or migration is allocated here.

Proposed new worker bounds are one job at a time, 0.25 CPU and 256 MiB. The actual
#489 store currently uses 250 ms statement / 100 ms lock timeouts and requires a
dedicated, bounded connection context. Connection acquisition and host caps need
concrete launcher proof. A 25 ms lock budget would require an owner change and
affected transaction check; it is not an implemented claim. These bounds require concrete launcher and
disposable lifecycle proof before becoming implemented claims. No connection or
Study lock may span HTTP or chess work. Host-specific latency remains unmeasured.

## Evidence and remaining acceptance

Named owning command:

```sh
TEMPO_PYTHON=/Users/andy/Documents/Codex/2026-10-26/task-14/python-491 \
  make python-file FILE=backend/tests/test_product_game_sync_integration.py
```

The wrapper uses already-installed Python dependencies plus this checkout's
source paths; no install. At the current private checkpoint, **9 cases pass**
using actual provider implementations with `httpx.MockTransport`, shared immutable
DTOs, actual workflow/index/comparer/Study scheduling policy, reused semantic
Games/Study fake stores and a disposable in-memory FastAPI app with a fake
projection/command owner.
Exact 205 Lichess IDs span three pages; Chess.com spans two months. Every page
recreates the outer adapter. HTTP checks prove inert GETs, explicit command IDs,
unchanged receipts, validation and local unavailable projection handling. A
blocked transport closes on its total budget. A concrete coordinator run proves
HTTP429 delay conversion/provider independence, gap/unintroduced classifications,
102 genuine miss effects through the existing Study owner and unchanged effect
count after overlapping jobs and runtime recreation. This proves neither native
persisted checkpoints nor normal-product Study processing. Existing advisory validation lock was
opened nontruncating, held for the command and released.

Scoped TypeScript compilation of the private endpoint map and its actual #490
client / #482 contract inputs passes using the existing TypeScript runtime:
`node node_modules/typescript/bin/tsc --noEmit --skipLibCheck --target esnext
--module esnext --moduleResolution bundler --lib esnext,dom --types node
app/product-composition/game-review.ts app/features/game-review/api.ts
app/features/product-api/contracts.ts`. The focused product import-boundary guard
also passes (1 selected, 28 deselected); no whole contract suite was repeated.

Reused owner evidence (unchanged implementation inputs; not rerun wholesale):
#482 `e269421b` 29 Python / 27 TypeScript; #483 `75369bb6` 11 index / 16 snapshot;
#486 `49995ad2` 30 adapter / 19 pure policy. Provider source checkpoints
#484 `1d80fa45` (17 cases) and #485 `df86c68f` (25 cases) are consumed. #489
`d5e3068b` supplies the workflow/store with 13 policy and one real disposable
PostgreSQL rollback/restart case; that transaction evidence is reused, not rerun.
#487 `16446623` supplies the receiver interface. #488 `a0751c7e` contains its
preview/delivery source; its four source checks at executable `d4856935` are
separate from A01/A02. #490 `57739c9f` has 22 focused unit cases. Native
ordinary-Study/browser acceptance and complete product isolation remain pending.

Next ready work: obtain Games-owned recent/detail projections and durable
configured-enqueue/current-finding retry facade, activate #489's schema through
an allocated owner migration, consume #488's current authority/receiver boundary,
then reserve the exact shared window. The coordinator factory, provider wrappers
and #490 endpoint mapping are ready. Required later checks remain:
`make ui-file FILE=product-game-sync.spec.ts` and
`node --test tests/runner/product-game-worker.test.mjs`, plus changed coordinator
and real disposable storage boundaries. Those suites are not yet authored or
passed: real import → finding → card → ordinary Study settlement and stopped/killed
optional-process independence require the remaining owner facade, receiving
authority boundary and launcher/mount. No hollow
browser or lifecycle success is substituted for that proof.

No live account, database, settings, service, software installation, deployment,
GitHub merge or graph migration is touched. Safe current fallback is the retained
Study/Tactics and #488's independent first-milestone path.
