# Study and Games dependency detail

[Index and source/status legend](README.md) · [System and ownership](system.md) ·
[Sequences](flows.md)

Canonical drawings and their source notes render in the owning directory READMEs
linked below. These headings remain stable pointers; the cross-cutting failure
table stays here. Open gaps and deployment-unverified labels are preserved.

## Main Study path and source gap

The canonical drawing and source notes render directly in
[app/README.md](../../../app/README.md#main-study-path-and-source-gap).

## Conditional Study build and offline assets (MAIN)

The canonical drawing and source notes render directly in
[static/README.md](../../../static/README.md#conditional-study-build-and-offline-assets-main).

## Shared Learning request execution gaps (MAIN)

The canonical drawing and source notes render directly in
[services/learning/runtime/README.md](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main).

## Merged PR322 Games reader

The canonical drawing and source notes render directly in
[apps/workbench-web/README.md](../../../apps/workbench-web/README.md#merged-pr322-games-reader).

## Failure, lifecycle and resource acceptance

| Path | Local ownership and failure result | Limit of claim |
| --- | --- | --- |
| Study → Learning | Browser retains immutable intents; Learning owns readiness, queue and settlement. Authentication failures require reconnection. Learning/database outage can prevent online studying; cached practice remains explicitly unconfirmed. | Learning is an essential dependency. Shared Home remains a source gap. |
| Conditional Study offline assets | The shared build inventories all emitted JS/MJS/CSS; worker installation/cache status can depend on optional chunks **if** they remain in that output. Lazy declarations alone do not execute those features. | [Source-backed conditional view](../../../static/README.md#conditional-study-build-and-offline-assets-main); emitted artifact and failure scenario unverified, no observed live failure. |
| Shared Learning request execution | Essential middleware awaits HostActivity lock/filesystem publication. Optional async endgame handlers contain synchronous DB phases on the shared ASGI loop. | [Source-supported open gaps](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main); no reproduced contention or live failure, no repair-completion claim. |
| Games read (MAIN) | Invalid auth/schema/scope/cursor or DB failure returns Games error; missing scopes remain missing. No hidden run creation, import, analysis, Study call or repair. View disposal destroys its own board and ignores late responses; server lifespan closes only its pools/executor. | See [read/failure sequence](../../../apps/workbench-web/README.md#games-read-failure-and-disposal-main-merged-pr322). No Study/worker shutdown is part of this path. |
| Games DB work (MAIN) | Scope listing ≤100 rows; accepted scope metadata ≤1000 games; page ≤10 games plus one lookahead. Existing 1500 ms statement, 250 ms lock, 3000 ms transaction bounds and configured pool maxima apply. | PostgreSQL may scan/order accepted JSON pages within that budget; no indexed/precomputed projection or host latency guarantee is claimed. |
| Navigation | Optional public Workbench launch and general Open Study root link are user navigation with no credentials/card authority transfer or service readiness check. | A link is not a required Study API dependency. Reuse existing [navigation contracts](../../../apps/workbench-web/src/public-navigation.ts), [launch URL validation](../../../app/lib/workbench-navigation.ts); do not add a wait. |
| Shared host / PostgreSQL cluster | CPU/memory/I/O/connection allocation and cluster failure can cross logical service/database boundaries. | **DEPLOYMENT UNVERIFIED:** separate bounded optional workload/connection budgets, effective limits and independent shutdown need verification before rollout. Separate processes or green component tests alone do not prove it. |

The merged [Games source architecture notes](../../../docs/analysis-restoration/games-only-architecture.md)
and component regression inventories describe its source proofs. This
documentation PR does not rerun those proofs, qualify a deployment, change CI
or touch live Study. Exact live reader grants and operational activation remain
unverified here.
