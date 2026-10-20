# Study and Games dependency detail

[Index and source/status legend](README.md) · [System and ownership](system.md) ·
[Sequences](flows.md)

## Main Study path and source gap

**MAIN:** `TEMPO_TARGET=study` selects the Learning connection boundary. The
diagram separates its HTTP serving path from the shared browser source that it
still loads. It does **not** certify that the whole Study artifact is free of
Games imports.

The `React.lazy` declarations shown as source gaps do **not** themselves execute
Games or sync code during ordinary Study. They defer the import factory until the
corresponding component is rendered; Home's Games subtree is conditional on
`currentView === "games"`. Source reachability is distinct from execution and from
the [conditional build/offline asset dependency](#conditional-study-build-and-offline-assets-main)
below. No emitted Study artifact was inspected for these drawings.

```mermaid
flowchart TB
  Entry["static/main +<br/>StudyConnectionBoundary"] -->|"connect/bootstrap, then lazy<br/>import"| Home["Shared home_view.tsx -<br/>legacy composition"]
  Home -->|"Study intents / review<br/>adapter registration"| Session["StudySession - durable<br/>submit, receipt recovery,<br/>next loading"]
  Session -->|"IntentStore"| Browser[("Authority-scoped browser<br/>intents / checkpoints")]
  Session -->|"LearningClient supplied by<br/>application adapters"| Adapter["review-outbox /<br/>study-command-client"]
  Adapter -->|"learningFetch: exact origin<br/>and allowed Study paths"| HTTP["Learning HTTP API"]
  HTTP -->|"record_intent / execute /<br/>read_operation"| Gateway["Learning gateway -<br/>transaction owner"]
  Gateway -->|"borrowed connection"| Review["LearningReviewAdapter"]
  Review -->|"ReviewSubmissionStore<br/>implementation supplied to<br/>submit_review"| App["Pure Learning review<br/>application"]
  Gateway -->|"queue command handlers"| Queue["Learning queue / admission /<br/>first issuance"]
  Review -->|"semantic required settlement<br/>in same transaction"| Remediation["Learning remediation adapter<br/>/ repository"]
  Review -->|"history / scheduling /<br/>evidence / receipt effects"| DB[("Learning-owned PostgreSQL<br/>state")]
  Queue -->|"canonical writer and lock<br/>protocol"| DB
  Remediation -->|"Learning-owned obligation<br/>settlement"| DB
  Home -.->|"GAP: source declares lazy<br/>GamesView import"| LegacyGames["app/views/games_view.tsx"]
  Home -.->|"GAP: source declares<br/>WorkbenchGameSync import"| LegacySync["legacy workbench-game-sync"]
  Entry -.->|"optional user navigation<br/>only; no readiness wait"| WB["Independent Workbench URL"]
  classDef gap fill:#fff1f0,stroke:#c53030,color:#7f1d1d
  class LegacyGames,LegacySync gap
```

| Interface / decision | Source correspondence |
| --- | --- |
| Connection, bootstrap, authority and allowed transport | [`study-connection.ts`](../../../app/lib/study-connection.ts): `connectStudy`, `studyWorkspace`, `learningPaths`, `authenticatedLearningFetch`. Only configured Learning origin/Study paths receive the credential; no fallback to Games. [Connection boundary](../../../app/components/study-connection-boundary.tsx) waits for Learning bootstrap or explicitly permitted cached practice. |
| Browser composition and retained import gap | [`static/src/main.tsx`](../../../static/src/main.tsx) loads the boundary, which imports [`home_view.tsx`](../../../app/views/home_view.tsx). Home declares lazy `GamesView` and `WorkbenchGameSync` imports and contains Games-related navigation/state. [`Navbar`](../../../app/components/Navbar.tsx) filters Study navigation; that is not source/import separation. The [planned Study artifact](../../../apps/study-web/README.md) has no independent build claim. |
| Session and browser data ownership | [`study-session-runtime.ts`](../../../app/lib/study-session-runtime.ts) composes adapters into [`createStudySession`](../../../packages/study-session/src/index.ts); [`study-intent-storage.ts`](../../../app/lib/study-intent-storage.ts) supplies the [`IntentStore`](../../../packages/browser-intents/src/index.ts). [`review-outbox.ts`](../../../app/lib/review-outbox.ts) and [`study-command-client.ts`](../../../app/lib/study-command-client.ts) preserve family-specific payloads/identities. |
| Borrowed review capabilities | [`ReviewStore` / `ReviewSubmissionStore`](../../../services/learning/application/src/tempo_learning_review/ports.py), [`submit_review`](../../../services/learning/application/src/tempo_learning_review/application.py), [`LearningReviewAdapter`](../../../services/learning/runtime/src/tempo_learning_runtime/review_adapter.py). The application receives semantic facts/operations; it does not acquire/commit a connection. |
| Serving and settlement | [API handler registration](../../../services/learning/runtime/src/tempo_learning_runtime/api.py), [gateway](../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py), [queue application](../../../services/learning/runtime/src/tempo_learning_runtime/queue_application.py), [admission](../../../services/learning/runtime/src/tempo_learning_runtime/admission.py), [receivers/settlement](../../../services/learning/runtime/src/tempo_learning_runtime/receivers.py), [remediation adapter](../../../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py). Settlement uses accepted Learning-local facts, not an online Games call. |

The independent Learning install closure contains no Game Evidence service,
Intelligence service, legacy backend, Redis/Celery or engine/model runtime. Its
[package definition](../../../services/learning/runtime/pyproject.toml) includes
Game Evidence **contracts** and Learning's own remediation module. A contract or
pure shared library is not a dependency on the producer lifecycle.

**Acceptance invariant:** Study must have no Games call, import, wait,
readiness, transaction or lifecycle dependency. Games must be able to fail
completely while ordinary Study continues. Existing shared Home imports are a
named **legacy source gap**, not an exception permitting new coupling or evidence
that the invariant is already met. This documentation makes no bundle-elimination
claim and changes no runtime source. PR322 does not remove this gap.

Any optional practice influence enters the **Learning/queue owner's semantic
interface**, as the [existing evidence sequence](flows.md#observed-game-evidence-to-learning-main)
shows. Learning owns validation, eligibility/admission, idempotency, transactions
and ordinary queue consumption. Study must not learn how to contact the producer;
the producer must not write Learning queue, review or scheduling tables. PR319 is
paused; no proposed encountered-card API is assumed here.

## Conditional Study build and offline assets (MAIN)

**MAIN source checked; emitted-artifact verification pending.** The Study build
uses the shared offline-shell inventory plugin. This is an asset-availability
boundary beyond whether a lazy component executes. Arrows below label build data
flow and readiness waits; dotted edges are explicitly conditional.

```mermaid
flowchart TB
  Build["Study Vite build<br/>static/app source graph"] -->|"all emitted JS, MJS and CSS"| Inventory["offlineShellAssets<br/>BUILD_SHELL_ASSETS inventory"]
  Build -.->|"IF optional chunks remain<br/>in emitted Study output"| Optional["Games / sync chunks<br/>Artifact presence unverified"]
  Optional -.->|"IF emitted, included<br/>without feature filtering"| Inventory
  Inventory -->|"inventory embedded in sw.js"| SW["Service worker<br/>requiredShellUrls / precacheShell"]
  SW -->|"cache.addAll required URLs"| Cache[("Browser offline shell cache")]
  Ready["Study offline queue readiness<br/>waitForOfflineShell"] -->|"wait for worker readiness<br/>and request shell status"| SW
  SW -.->|"IF a required asset fails or<br/>is missing from the cache"| Failure["New shell install can reject<br/>or shell ready can be false"]
```

| Edge / owner | Concrete source and effect |
| --- | --- |
| Build composition → inventory | [Vite config](../../../vite.static.config.ts) installs [`offlineShellAssets`](../../../scripts/offline-shell-assets.ts). Its `writeBundle` inventories **all** emitted `.js`, `.mjs` and `.css` bundle keys, not only initial HTML references or a Study-only feature list, then replaces `BUILD_SHELL_ASSETS` in emitted `sw.js`. |
| Inventory → install/cache | [`public/sw.js`](../../../public/sw.js): `requiredShellUrls` combines the base shell, HTML-referenced assets and build inventory. `precacheShell` uses `cache.addAll`; the install event waits for that promise before `skipWaiting`. A required fetch failure can reject installation of the new shell. This does not establish failure of an already active older shell. |
| Cache → offline readiness | The same worker's `tempo:offline-shell-status` handler sets `ready` only when every required URL is cached. [`waitForOfflineShell`](../../../app/lib/offline-shell.ts) waits for service-worker readiness/control and validates the version/status reply. [Queue preparation and cached fallback](../../../app/views/fetchAndInitializeQueue.tsx) call it before reporting phone readiness or accepting that cached fallback. [Static startup](../../../static/src/main.tsx) registers the worker in production. |

**Conditional consequence:** if Games/sync chunks remain in the emitted Study
output, they enter this required inventory even without Games/sync execution.
Unavailable optional assets can then affect Study's new offline-shell installation
or offline-readiness check. The asset set is served from the Study build's own
origin; this is not evidence of an API call or readiness wait on the independent
Games service.

**Verification status:** the inventory, install and status paths above were read
in source. No Study build was produced; emitted chunks and the built inventory
were not inspected. No asset-loss scenario was executed or live/offline failure
observed here.
Whether this conditional edge exists in a particular artifact remains unverified.
The target is a Study composition/artifact whose required offline assets exclude
optional feature implementations. A separate composition repair is in progress
on another branch; it is not incorporated or claimed complete by this view.
Its owning change must inspect the actual output/inventory and verify the affected
offline boundary before claiming the gap removed. This documentation correction
does not require a full-stack suite or change runtime source.

## Shared Learning request execution gaps (MAIN)

**PROPOSED HOST REPAIR / MAIN ENDGAME GAP:** HostActivity source checkpoint
[`09cb4eef`](https://github.com/aaweaver-actuary/tempo/commit/09cb4eef027666aaa3e728660b9e7ca39eb36091) moves
publication and cleanup to an optional daemon; the essential request path only
updates bounded memory. This repair is unmerged and deployment unverified. The
MAIN shared event-loop endgame gap below remains outside this host-only change.

```mermaid
flowchart TB
  Study["Essential Study HTTP request"] -->|"dispatch / response progress"| Loop["Learning ASGI event loop"]
  Loop -->|"PROPOSED: short bounded memory<br/>entry / exit, including cancellation"| Activity["HostActivity request counter<br/>and maximum quiet deadline"]
  Activity -->|"wake event; no I/O wait"| Publisher["Optional daemon publisher<br/>snapshot under memory lock"]
  Publisher -->|"release memory lock, then<br/>mkdir / write / replace"| Files[("HostActivity filesystem")]
  Lifecycle["Learning startup / close"] -->|"start / signal daemon<br/>no publication or cleanup join"| Publisher
  Publisher -->|"on stop after I/O returns:<br/>remove only owned snapshot"| Files
  Endgame["Async tablebase probe /<br/>endgame attempt handlers"] -->|"synchronous DB phases<br/>within async handlers"| Sync["connection / execute / fetch<br/>and gateway dispatch"]
  Sync -->|"pool / queries / transactions"| DB[("Learning PostgreSQL")]
  Sync -.->|"occupies the same event loop<br/>during synchronous phases"| Loop
  Endgame -->|"cache miss: awaited HTTP<br/>outside DB transactions"| Provider["Optional tablebase provider"]
  classDef gap fill:#fff1f0,stroke:#c53030,color:#7f1d1d
  class Sync gap
```

| Actual dependency | Source and ownership |
| --- | --- |
| PROPOSED: essential requests update memory only | [Learning API checkpoint](https://github.com/aaweaver-actuary/tempo/blob/09cb4eef027666aaa3e728660b9e7ca39eb36091/services/learning/runtime/src/tempo_learning_runtime/api.py): `authority` wraps essential `/api/` requests (excluding activity/OPTIONS) in `request_activity`. [`HostActivity.request`](https://github.com/aaweaver-actuary/tempo/blob/09cb4eef027666aaa3e728660b9e7ca39eb36091/services/learning/runtime/src/tempo_learning_runtime/host_activity.py) updates a counter and maximum exit quiet deadline under a short memory lock. At most 1,024 browser sessions bound snapshot memory work. No filesystem I/O occurs while this lock is held; cancellation settles the counter synchronously. |
| PROPOSED: publisher owns I/O and cleanup | `HostActivity.start` starts a daemon without first publishing; `close` signals without joining. The daemon snapshots memory, releases its lock, then publishes; shutdown cleanup runs there after I/O returns. A stalled daemon cannot block essential request entry/exit or Learning shutdown. Optional activity submissions return 503 until a successful healthy publication or following a known failure/staleness/capacity limit; a 200 acknowledges memory submission against the last healthy lease. |
| Retained admission and failure limit | The unchanged [host reader](../../../scripts/host-study-activity.mjs) keeps a last healthy idle snapshot readable until its 15-second lease expires. Asynchronous publication cannot establish instant stall detection. Stale or missing expected sources remain unavailable under `TEMPO_STUDY_ACTIVITY_EXPECTED_SOURCES`, including after owned cleanup. No Games consumer acknowledgement, new protocol, deployment or host-resource isolation is claimed. |
| Optional endgame work can occupy the shared ASGI loop | [Learning API](../../../services/learning/runtime/src/tempo_learning_runtime/api.py): async `tablebase` performs synchronous cache reads before awaited provider HTTP and synchronous cache writes afterward; async `endgame_attempt` also performs synchronous receipt/template reads and `gateway.dispatch`. Those synchronous connection/query/dispatch phases run on the shared event loop and can delay unrelated Study request progress. The provider HTTP itself is awaited outside a database transaction; that fact does not isolate the synchronous phases. |

**Verification status:** the host-only source repair has focused injected stall,
recovery, cancellation, admission and shutdown regressions in the owning
[Learning inventory](../../../services/learning/runtime/REGRESSIONS.md). They
include authenticated middleware and lifespan progress while actual `mkdir` is
blocked, with cleanup after release. Original-source stall reds are retained in
task evidence. These do not establish a live incident, deployment, instant
stall detection or global resource isolation. The MAIN optional endgame
synchronous execution gap remains source-supported and unrepaired by this change.

## Merged PR322 Games reader

**MAIN / MERGED PR322:** implementation
`653a85bb5f5b3e520c0eda0c36234d3b416708b4` merged as
`bc746728ca1967cc8ad9991cbe8c12099a01a3ae`. It restores frozen accepted
input viewing through an enforced read composition. It does not restore live
sync, fresh repertoire comparison, analysis creation or practice commands.

```mermaid
flowchart TB
  subgraph Browser["PR322 browser"]
    Composition["Workbench<br/>connection/bootstrap owner"] -->|"games-readonly-v1 before<br/>pending command recovery"| View["mountGamesView /<br/>mountGameBrowser"]
    View -->|"GamesReader: listScopes,<br/>readScope, readGames"| Client["IntelligenceClient<br/>implements GamesReader"]
    View -->|"supplied legal replay /<br/>rendering"| Board["Chess / Chessground<br/>libraries"]
  end
  subgraph Server["PR322 server"]
    Client -->|"authenticated GET;<br/>workspace/origin checks"| API["games_api read routes only"]
    API -->|"scope / accepted games<br/>projection"| Reader["GamesReader - complete<br/>parameter-bound queries"]
    Reader -->|"borrow read-only transaction"| Store["Store - pools, readiness,<br/>transactions, close"]
    Store -->|"SELECT only for this path"| Data[("Intelligence: heads, runs,<br/>inputs, pages")]
  end
```

Source links below follow the merged implementation. The index retains the exact
implementation and merge SHAs; no deployed process or effective grant is inferred.

| Boundary | Merged PR322 concrete source |
| --- | --- |
| Typed view port | [`GamesReader`](../../../apps/workbench-web/src/games-reader.ts): three typed read methods; [`games-view.ts`](../../../apps/workbench-web/src/games-view.ts) and [`game-browser.ts`](../../../apps/workbench-web/src/game-browser.ts) receive it. [`client.ts`](../../../apps/workbench-web/src/client.ts) adapts HTTP and fences connection generations. |
| Shared Workbench composition | [`workbench.ts`](../../../apps/workbench-web/src/workbench.ts) still imports its ordinary Analysis controller/commands. It selects Games mode after bootstrap, before mutation recovery. Games view modules do not import that controller. The whole Workbench artifact is not claimed to contain only read code. |
| Server composition / enforcement | [`api.py`](../../../services/intelligence/src/tempo_intelligence/api.py) returns after registering [`games_api.py`](../../../services/intelligence/src/tempo_intelligence/analysis/games_api.py), rejects unsafe methods, and starts no worker/provider in read-only mode. Health, bootstrap, scope and game reads are registered; mutation/job/report-progress routes are absent. |
| Query / cursor owner | [`games_reader.py`](../../../services/intelligence/src/tempo_intelligence/analysis/games_reader.py) queries only `analysis_heads`, `analysis_runs`, `analysis_inputs`, `analysis_pages`. Cursor binds workspace/run/revision/input/ordering. Scope and page share one transaction. Ordinary Analysis delegates the same game reader. |
| Database and lifecycle | [`Store`](../../../services/intelligence/src/tempo_intelligence/store.py) forces read-only transactions even when a caller omits the flag. Startup checks SELECT access for these tables plus metadata, without requiring engine/write grants. [`Configuration`](../../../services/intelligence/src/tempo_intelligence/config.py) rejects worker/model configuration in read-only mode and needs caller/read configuration rather than operator/worker secrets. This is not a claim that live grants were provisioned or every possible write privilege is audited away. |

**Before PR322 (historical `4fc2eb8c`):** the earlier
[Workbench](https://github.com/aaweaver-actuary/tempo/blob/4fc2eb8c6b534345b25668761b3746c91be08cf0/apps/workbench-web/src/workbench.ts)
browsed selected games inside ordinary Analysis. The earlier
[`AnalysisStore.runs` / `games`](https://github.com/aaweaver-actuary/tempo/blob/4fc2eb8c6b534345b25668761b3746c91be08cf0/services/intelligence/src/tempo_intelligence/analysis/store.py)
used engine-position aggregates for run listing and loaded accepted input through
`_input` before paging games in memory. The earlier
[API composition](https://github.com/aaweaver-actuary/tempo/blob/4fc2eb8c6b534345b25668761b3746c91be08cf0/services/intelligence/src/tempo_intelligence/api.py)
registered mutation/provider/job routes and could start a background worker.
The mutable mode still has those capabilities; merged PR322 adds the separate
read-only composition, narrow view port and bounded game projection above.

## Failure, lifecycle and resource acceptance

| Path | Local ownership and failure result | Limit of claim |
| --- | --- | --- |
| Study → Learning | Browser retains immutable intents; Learning owns readiness, queue and settlement. Authentication failures require reconnection. Learning/database outage can prevent online studying; cached practice remains explicitly unconfirmed. | Learning is an essential dependency. Shared Home remains a source gap. |
| Conditional Study offline assets | The shared build inventories all emitted JS/MJS/CSS; worker installation/cache status can depend on optional chunks **if** they remain in that output. Lazy declarations alone do not execute those features. | [Source-backed conditional view](#conditional-study-build-and-offline-assets-main); emitted artifact and failure scenario unverified, no observed live failure. |
| Shared Learning request execution | PROPOSED HostActivity repair: essential memory updates, daemon-owned publication/cleanup, no filesystem wait or shutdown join. MAIN optional async endgame handlers still contain synchronous DB phases on the shared ASGI loop. | [Mixed source status](#shared-learning-request-execution-gaps-main); focused injected host regressions only. Last healthy idle evidence can persist for its 15-second lease; deployment/resource isolation unverified. |
| Games read (MAIN) | Invalid auth/schema/scope/cursor or DB failure returns Games error; missing scopes remain missing. No hidden run creation, import, analysis, Study call or repair. View disposal destroys its own board and ignores late responses; server lifespan closes only its pools/executor. | See [read/failure sequence](flows.md#games-read-failure-and-disposal-main-merged-pr322). No Study/worker shutdown is part of this path. |
| Games DB work (MAIN) | Scope listing ≤100 rows; accepted scope metadata ≤1000 games; page ≤10 games plus one lookahead. Existing 1500 ms statement, 250 ms lock, 3000 ms transaction bounds and configured pool maxima apply. | PostgreSQL may scan/order accepted JSON pages within that budget; no indexed/precomputed projection or host latency guarantee is claimed. |
| Navigation | Optional public Workbench launch and general Open Study root link are user navigation with no credentials/card authority transfer or service readiness check. | A link is not a required Study API dependency. Reuse existing [navigation contracts](../../../apps/workbench-web/src/public-navigation.ts), [launch URL validation](../../../app/lib/workbench-navigation.ts); do not add a wait. |
| Shared host / PostgreSQL cluster | CPU/memory/I/O/connection allocation and cluster failure can cross logical service/database boundaries. | **DEPLOYMENT UNVERIFIED:** separate bounded optional workload/connection budgets, effective limits and independent shutdown need verification before rollout. Separate processes or green component tests alone do not prove it. |

The merged [Games source architecture notes](../../../docs/analysis-restoration/games-only-architecture.md)
and component regression inventories describe its source proofs. This
documentation PR does not rerun those proofs, qualify a deployment, change CI
or touch live Study. Exact live reader grants and operational activation remain
unverified here.
