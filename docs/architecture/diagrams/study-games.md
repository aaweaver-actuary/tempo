# Study and Games dependency detail

[Index and source/status legend](README.md) · [System and ownership](system.md) ·
[Sequences](flows.md)

## Main Study path and source gap

**PR324 IMPLEMENTED SOURCE, UNMERGED:** the browser composition below describes
`799d6a56` on `codex/study-games-composition`, with PR323's documentation merged
separately. It replaces the shared-Home Games import gap in this branch. MAIN at
`fc92a4d9` and any deployed artifact are not claimed to contain this change.
The Learning serving and transaction owners are unchanged.

```mermaid
flowchart TB
  Build["Study Vite composition"] -->|"select physical entry before graph discovery"| Entry["static/src/study-main.tsx"]
  Entry -->|"connect/bootstrap, then lazy import"| Boundary["StudyConnectionBoundary"]
  Boundary -->|"shared Study shell and training controller"| Home["home_view.tsx"]
  Home -->|"Train / Tactics / Endgames / appearance"| StudyUI["Core Study views"]
  Boundary -->|"Learning-owned settings"| Settings["study-settings-panel"]
  Home -->|"Study intents / review adapter registration"| Session["StudySession - durable submit, receipt recovery, next loading"]
  Session -->|"IntentStore"| Browser[("Authority-scoped browser intents / checkpoints")]
  Session -->|"LearningClient supplied by application adapters"| Adapter["review-outbox / study-command-client"]
  Adapter -->|"learningFetch: exact origin and allowed Study paths"| HTTP["Learning HTTP API"]
  HTTP -->|"record_intent / execute / read_operation"| Gateway["Learning gateway - transaction owner"]
  Gateway -->|"borrowed connection"| Review["LearningReviewAdapter"]
  Review -->|"semantic ReviewSubmissionStore"| App["Pure Learning review application"]
  Gateway -->|"queue command handlers"| Queue["Learning queue / admission / first issuance"]
  Review -->|"same-transaction required settlement"| Remediation["Learning remediation adapter / repository"]
  Review -->|"history / scheduling / evidence / receipt effects"| DB[("Learning-owned PostgreSQL state")]
  Queue -->|"canonical writer and lock protocol"| DB
  Remediation -->|"Learning-owned obligation settlement"| DB
  Full["static/src/main.tsx - full local/demo entry"] --> Legacy["legacy-home / legacy-workspace"]
  Legacy -->|"same Home; narrow facts and callbacks"| Home
  Legacy -->|"owns optional imports, state and handoffs"| Optional["Games/sync, Builder, comparison, repertoire, insights, studies, administration and dialogs"]
  Boundary -.->|"optional user navigation only; no readiness wait"| WB["Independent Workbench URL"]
```

| Interface / decision | Source correspondence |
| --- | --- |
| Physical entry and loaded graph guard | [`studyComposition`](../../../scripts/study-composition.ts) changes the HTML entry before graph discovery. Its semantic optional implementation list rejects loaded Games/sync, authoring, comparison, insights, administration, and legacy composition roots, even if later tree-shaken. [`study-main.tsx`](../../../static/src/study-main.tsx) has no legacy import. |
| Optional state, presentation and handoff owner | [`LegacyHome`](../../../app/views/legacy-home.tsx) injects [`LegacyWorkspace`](../../../app/views/legacy-workspace.tsx). It owns Builder/comparison/discovery/filter/paste/repair state, repair race fencing, deletion recovery and optional views/dialogs. [`LegacyWorkspaceProps` / slots / controller](../../../app/components/optional-workspace.ts) expose selected card/position facts and explicit queue/card commands; they do not pass the Home store. Home owns one training controller and queue acquisition. Unsupported controls are omitted when their action capability is absent. |
| Genuine Study settings and navigation | [`StudyConnectionBoundary`](../../../app/components/study-connection-boundary.tsx) retains [`StudySettingsPanel`](../../../app/components/study-settings-panel.tsx) for Learning settings. [`StudySettingsView`](../../../app/views/study-settings-view.tsx) changes browser appearance/shortcuts. [`canOpenWorkspace`](../../../app/lib/study-workspace-policy.ts) checks direct intents as well as Navbar visibility. |
| Connection and allowed transport | [`study-connection.ts`](../../../app/lib/study-connection.ts): `connectStudy`, `studyWorkspace`, `learningPaths`, `authenticatedLearningFetch`. Only the configured Learning origin/Study paths receive the credential; no Games fallback. The boundary waits for Learning bootstrap or explicitly permitted cached practice. |
| Session and browser data ownership | [`study-session-runtime.ts`](../../../app/lib/study-session-runtime.ts) composes adapters into [`createStudySession`](../../../packages/study-session/src/index.ts); [`study-intent-storage.ts`](../../../app/lib/study-intent-storage.ts) supplies [`IntentStore`](../../../packages/browser-intents/src/index.ts). [`review-outbox.ts`](../../../app/lib/review-outbox.ts) and [`study-command-client.ts`](../../../app/lib/study-command-client.ts) preserve family-specific payloads/identities. |
| Serving, borrowed review and settlement | [API registration](../../../services/learning/runtime/src/tempo_learning_runtime/api.py), [gateway](../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py), [`submit_review` / ports](../../../services/learning/application/src/tempo_learning_review/application.py), [review adapter](../../../services/learning/runtime/src/tempo_learning_runtime/review_adapter.py), [queue application](../../../services/learning/runtime/src/tempo_learning_runtime/queue_application.py), [admission](../../../services/learning/runtime/src/tempo_learning_runtime/admission.py), [receivers](../../../services/learning/runtime/src/tempo_learning_runtime/receivers.py), [remediation adapter](../../../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py). The review application receives semantic capabilities; gateway retains transaction authority. Settlement uses Learning-local facts, not an online Games call. |

The independent Learning install closure contains no Game Evidence service,
Intelligence service, legacy backend, Redis/Celery or engine/model runtime. Its
[package definition](../../../services/learning/runtime/pyproject.toml) includes
Game Evidence **contracts** and Learning's own remediation module. Pure shared
libraries do not create a producer lifecycle dependency.

**Acceptance invariant:** Study must have no Games call, import, wait,
readiness, transaction or lifecycle dependency. Games must be able to fail
completely while ordinary Study continues. This PR repairs the browser source and
artifact boundary only; shared host/request execution gaps below and deployment
resource isolation retain their own proof owners.

Optional practice influence enters the **Learning/queue owner's semantic
interface**, as the [existing evidence sequence](flows.md#observed-game-evidence-to-learning-main)
shows. Learning owns validation, eligibility/admission, idempotency, transactions
and ordinary queue consumption. Producers must not write Learning queue, review
or scheduling tables. PR319 remains paused; no proposed encountered-card API is
assumed here.

## Conditional Study build and offline assets (MAIN)

**PR324 IMPLEMENTED SOURCE AND ACTUAL BUILD, UNMERGED:** the all-emitted-assets
inventory rule remains intact. The physical Study composition removes optional
implementations before emission. This changes the former conditional dependency;
it does not filter required assets out of the worker to conceal coupling.

```mermaid
flowchart TB
  Build["Physical Study source graph"] -->|"semantic implementation guard rejects optional roots"| Output["Study output: core shell, training, settings and worker"]
  Legacy["Legacy optional implementations"] -.->|"excluded from loaded graph and emitted output"| Guard["Study composition guard"]
  Output -->|"all emitted JS, MJS and CSS"| Inventory["offlineShellAssets / BUILD_SHELL_ASSETS"]
  Inventory -->|"inventory embedded in emitted sw.js"| SW["Service worker / requiredShellUrls / precacheShell"]
  SW -->|"cache.addAll required Study URLs"| Cache[("Browser offline shell cache")]
  Ready["Study offline queue readiness / waitForOfflineShell"] -->|"worker readiness and shell status"| SW
  SW -.->|"a missing required Study asset still fails install or readiness"| Failure["Truthful unavailable offline shell"]
```

| Edge / owner | Concrete source and effect |
| --- | --- |
| Composition → actual output | [`studyComposition`](../../../scripts/study-composition.ts) emits `study-composition.json`, recording loaded application modules and each emitted chunk's modules. PR324's actual bounded Study build passed the expanded implementation guard. It emitted seven JS/CSS assets, including the Study worker, and no named optional implementations. |
| Output → inventory | [Vite config](../../../vite.static.config.ts) installs [`offlineShellAssets`](../../../scripts/offline-shell-assets.ts). Its `writeBundle` inventories every emitted `.js`, `.mjs` and `.css` key and replaces `BUILD_SHELL_ASSETS` in `sw.js`. The actual emitted inventory equals that output set. |
| Inventory → install/cache | [`public/sw.js`](../../../public/sw.js): `requiredShellUrls` combines base shell, HTML references and build inventory. `precacheShell` uses `cache.addAll`; installation waits for that promise before `skipWaiting`. Required fetch failures still reject new-shell installation. |
| Cache → offline readiness | Worker `tempo:offline-shell-status` checks all required URLs. [`waitForOfflineShell`](../../../app/lib/offline-shell.ts) verifies worker version and readiness. [Queue preparation/fallback](../../../app/views/fetchAndInitializeQueue.tsx) calls it before claiming offline readiness. [`renderApplication`](../../../static/src/render-application.tsx) registers the production worker. |

**Verification status:** actual emitted modules, chunks and offline inventory were
inspected for this branch: 157 application modules and seven emitted JS/CSS assets.
One native Chromium context served the actual artifact and worker from a disposable
loopback host with canonical synthetic Learning bootstrap/settings/queue/teaching
fixtures. Optional asset URLs returned 404; worker status was ready before and after
offline reload, the prepared opening queue remained usable, and browser appearance
preferences persisted across settings/training navigation. Unknown API, external
network and page-error counts were zero; the context, Chromium process and host
were closed. The PR evidence retains the exact fixture script, screenshots and
initial fixture/oracle failures. No live service, data, deployment, PostgreSQL
behavior or whole-host resource independence is certified by this frontend proof.

## Shared Learning request execution gaps (MAIN)

**MAIN / OPEN SOURCE GAPS:** the source has two request-progress dependencies
that the separation target does not permit us to hide. They concern awaited
host-reporting I/O and shared event-loop execution, not Games service calls.

```mermaid
flowchart TB
  Study["Essential Study HTTP request"] -->|"dispatch / response progress"| Loop["Learning ASGI event loop"]
  Loop -->|"essential middleware awaits<br/>context entry and exit"| Activity["request_activity<br/>asyncio.to_thread + shield"]
  Activity -->|"wait for threading.Lock<br/>and synchronous _publish"| Files[("HostActivity filesystem<br/>mkdir / write / replace")]
  Endgame["Async tablebase probe /<br/>endgame attempt handlers"] -->|"synchronous DB phases<br/>within async handlers"| Sync["connection / execute / fetch<br/>and gateway dispatch"]
  Sync -->|"pool / queries / transactions"| DB[("Learning PostgreSQL")]
  Sync -.->|"occupies the same event loop<br/>during synchronous phases"| Loop
  Endgame -->|"cache miss: awaited HTTP<br/>outside DB transactions"| Provider["Optional tablebase provider"]
  classDef gap fill:#fff1f0,stroke:#c53030,color:#7f1d1d
  class Activity,Sync gap
```

| Actual dependency | Source and ownership |
| --- | --- |
| Essential requests await host publication | [Learning API](../../../services/learning/runtime/src/tempo_learning_runtime/api.py): `authority` wraps essential `/api/` requests (excluding activity/OPTIONS) in `request_activity`. It awaits the thread running `context.__enter__` before `call_next`, and awaits entry/exit completion in cleanup, including cancellation. [`HostActivity.request`](../../../services/learning/runtime/src/tempo_learning_runtime/host_activity.py) takes `threading.Lock` and synchronously calls `_publish`, which creates directories, writes JSON and replaces its file. Moving that work to a thread frees the event loop while awaiting it, but does not remove the request's wait. |
| Reporting configuration and failure limit | [`Configuration.environment`](../../../services/learning/runtime/src/tempo_learning_runtime/config.py) sets the activity directory; `HostActivity.request` is a no-op only when its directory is `None`. Publication catches/logs `OSError`, but a slow lock/filesystem operation can still delay the awaited path. It does not wait for a Games consumer acknowledgement. Database timeout settings do not establish a bound on this filesystem wait. |
| Optional endgame work can occupy the shared ASGI loop | [Learning API](../../../services/learning/runtime/src/tempo_learning_runtime/api.py): async `tablebase` performs synchronous cache reads before awaited provider HTTP and synchronous cache writes afterward; async `endgame_attempt` also performs synchronous receipt/template reads and `gateway.dispatch`. Those synchronous connection/query/dispatch phases run on the shared event loop and can delay unrelated Study request progress. The provider HTTP itself is awaited outside a database transaction; that fact does not isolate the synchronous phases. |

**Verification status:** these are source-supported open architecture gaps. No
contention, latency or live failure was reproduced here. Transaction/pool budgets
remain useful bounds at their own boundaries; they are not proof that optional
work cannot delay Study through the shared event loop or awaited filesystem path.
The target is essential Study progress independent of optional host reporting and
optional workload execution. Coordinator-owned code repairs are separate work,
not completed by or incorporated into this documentation view.

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
| Shared Learning request execution | Essential middleware awaits HostActivity lock/filesystem publication. Optional async endgame handlers contain synchronous DB phases on the shared ASGI loop. | [Source-supported open gaps](#shared-learning-request-execution-gaps-main); no reproduced contention or live failure, no repair-completion claim. |
| Games read (MAIN) | Invalid auth/schema/scope/cursor or DB failure returns Games error; missing scopes remain missing. No hidden run creation, import, analysis, Study call or repair. View disposal destroys its own board and ignores late responses; server lifespan closes only its pools/executor. | See [read/failure sequence](flows.md#games-read-failure-and-disposal-main-merged-pr322). No Study/worker shutdown is part of this path. |
| Games DB work (MAIN) | Scope listing ≤100 rows; accepted scope metadata ≤1000 games; page ≤10 games plus one lookahead. Existing 1500 ms statement, 250 ms lock, 3000 ms transaction bounds and configured pool maxima apply. | PostgreSQL may scan/order accepted JSON pages within that budget; no indexed/precomputed projection or host latency guarantee is claimed. |
| Navigation | Optional public Workbench launch and general Open Study root link are user navigation with no credentials/card authority transfer or service readiness check. | A link is not a required Study API dependency. Reuse existing [navigation contracts](../../../apps/workbench-web/src/public-navigation.ts), [launch URL validation](../../../app/lib/workbench-navigation.ts); do not add a wait. |
| Shared host / PostgreSQL cluster | CPU/memory/I/O/connection allocation and cluster failure can cross logical service/database boundaries. | **DEPLOYMENT UNVERIFIED:** separate bounded optional workload/connection budgets, effective limits and independent shutdown need verification before rollout. Separate processes or green component tests alone do not prove it. |

The merged [Games source architecture notes](../../../docs/analysis-restoration/games-only-architecture.md)
and component regression inventories describe its source proofs. This
documentation PR does not rerun those proofs, qualify a deployment, change CI
or touch live Study. Exact live reader grants and operational activation remain
unverified here.
