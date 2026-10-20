# Study and Games dependency detail

[Index and source/status legend](README.md) · [System and ownership](system.md) ·
[Sequences](flows.md)

Canonical drawings and their source notes render in the owning directory READMEs
linked below. These headings remain stable pointers; the cross-cutting failure
table stays here. The canonical views describe merged source at `588cea54` where repaired; merged baseline, retained legacy coupling and deployment-unverified limits remain explicit.

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
| Study → Learning | Browser retains immutable intents; Learning owns readiness, queue and settlement. Authentication failures require reconnection. Learning/database outage can prevent online studying; cached practice remains explicitly unconfirmed. | Learning is an essential dependency. Merged PR324 physically separates optional composition from the Study shell; no deployment claim. |
| Conditional Study offline assets | PR324 excludes named optional implementations before emission; the worker still requires every emitted JS/MJS/CSS asset and reports missing required assets honestly. | [Canonical build view](../../../static/README.md#conditional-study-build-and-offline-assets-main); prior actual build and synthetic offline/asset-loss evidence are scoped to PR324. No live incident, combined browser qualification or deployment is inferred. |
| Shared Learning request execution | Merged PR325 uses bounded-memory request accounting with daemon-owned publication/cleanup; PR326 runs complete optional persistence phases in one separate daemon with size-one optional pools. Essential shutdown does not join either stalled optional owner. | [Canonical runtime view](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main); healthy idle may allow foreground overlap up to its remaining 15-second lease. Missing-source denial requires configured expected workspace identities; default `[]` accepts absence. Shared host/DB and deployment isolation remain unverified. |
| Games read (MAIN) | Invalid auth/schema/scope/cursor or DB failure returns Games error; missing scopes remain missing. No hidden run creation, import, analysis, Study call or repair. View disposal destroys its own board and ignores late responses; server lifespan closes only its pools/executor. | See [read/failure sequence](../../../apps/workbench-web/README.md#games-read-failure-and-disposal-main-merged-pr322). No Study/worker shutdown is part of this path. |
| Games DB work (MAIN) | Scope listing ≤100 rows; accepted scope metadata ≤1000 games; page ≤10 games plus one lookahead. Existing 1500 ms statement, 250 ms lock, 3000 ms transaction bounds and configured pool maxima apply. | PostgreSQL may scan/order accepted JSON pages within that budget; no indexed/precomputed projection or host latency guarantee is claimed. |
| Navigation | Optional public Workbench launch and general Open Study root link are user navigation with no credentials/card authority transfer or service readiness check. | A link is not a required Study API dependency. Reuse existing [navigation contracts](../../../apps/workbench-web/src/public-navigation.ts), [launch URL validation](../../../app/lib/workbench-navigation.ts); do not add a wait. |
| Shared host / PostgreSQL cluster | CPU/memory/I/O/connection allocation and cluster failure can cross logical service/database boundaries. | **DEPLOYMENT UNVERIFIED:** separate bounded optional workload/connection budgets, effective limits and independent shutdown need verification before rollout. Separate processes or green component tests alone do not prove it. |

The merged [Games source architecture notes](../../../docs/analysis-restoration/games-only-architecture.md)
and component regression inventories describe its source proofs. This
documentation PR does not rerun those proofs, qualify a deployment, change CI
or touch live Study. Exact live reader grants and operational activation remain
unverified here.
