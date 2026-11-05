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

## Retained Game Review and historical Games reader

The current Game Review command and recovery boundary is documented in
[product composition](../../../backend/app/product_composition/README.md#durable-game-review-commands-and-settings-recovery-590).
The standalone PR322 Workbench reader is retired; its [source view](https://github.com/aaweaver-actuary/tempo/blob/aec931f9820b8cd6b219074ae0c32d6686c21684/apps/workbench-web/README.md#merged-pr322-games-reader) is historical.

## Failure, lifecycle and resource acceptance

| Path | Local ownership and failure result | Limit of claim |
| --- | --- | --- |
| Study → Learning | Browser retains immutable intents; Learning owns readiness, queue and settlement. Authentication failures require reconnection. Learning/database outage can prevent online studying; cached practice remains explicitly unconfirmed. | Learning is an essential dependency. Merged PR324 physically separates optional composition from the Study shell; no deployment claim. |
| Conditional Study offline assets | PR324 excludes named optional implementations before emission; the worker still requires every emitted JS/MJS/CSS asset and reports missing required assets honestly. | [Canonical build view](../../../static/README.md#conditional-study-build-and-offline-assets-main); prior actual build and synthetic offline/asset-loss evidence are scoped to PR324. No live incident, combined browser qualification or deployment is inferred. |
| Shared Learning request execution | Merged PR325 uses bounded-memory request accounting with daemon-owned publication/cleanup; PR326 runs complete optional persistence phases in one separate daemon with size-one optional pools. Essential shutdown does not join either stalled optional owner. | [Canonical runtime view](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main); healthy idle may allow foreground overlap up to its remaining 15-second lease. Missing-source denial requires configured expected workspace identities; default `[]` accepts absence. Shared host/DB and deployment isolation remain unverified. |
| Game Review | The normal workspace reads and replays captured games; Games feedback submits typed work to Study. Provider failure must not block ordinary Study. | [Game Review owner](../../../backend/app/games/README.md); source behavior does not establish installed acceptance. |
| Shared host / PostgreSQL cluster | CPU/memory/I/O/connection allocation and cluster failure can cross logical service/database boundaries. | **DEPLOYMENT UNVERIFIED:** separate bounded optional workload/connection budgets, effective limits and independent shutdown need verification before rollout. Separate processes or green component tests alone do not prove it. |

The [historical Games source architecture notes](https://github.com/aaweaver-actuary/tempo/blob/aec931f9820b8cd6b219074ae0c32d6686c21684/docs/analysis-restoration/games-only-architecture.md) describe earlier source proofs. This
documentation PR does not rerun those proofs, qualify a deployment, change CI
or touch live Study. Exact live reader grants and operational activation remain
unverified here.
