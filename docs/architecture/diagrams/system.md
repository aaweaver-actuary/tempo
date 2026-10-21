# System, ownership and failure domains

[Index and source/status legend](README.md) · [Study/Games detail](study-games.md) ·
[Sequences and data flows](flows.md)

## Implemented component increments

The canonical drawing and source notes render directly in
[docs/architecture/diagrams/README.md](README.md#implemented-component-increments).

## Legacy assembly and existing coupling

The canonical drawing and source notes render directly in
[backend/README.md](../../../backend/README.md#legacy-assembly-and-existing-coupling).

## Data, resource and lifecycle owners

| Owner | Durable state and transaction authority | Resource / lifecycle / failure boundary |
| --- | --- | --- |
| Study browser | [Intent store adapter](../../../database/browser/study-intent-storage.ts) and [browser-intents](../../../database/browser-intents/src/index.ts) own browser intent transactions; [offline cache](../../../app/lib/offline-training.ts) owns retained practice data. Credentials stay in the connection's session storage; durable attempt identity includes authority. Browser persistence does not settle server state. | [Study session](../../../packages/study-session/src/index.ts) owns delivery and next-load state. Dispose keeps durable work; stale responses must not replace newer session state. Storage failure blocks unretained dispatch. Learning outage may allow cached practice, never fabricated commitment. |
| Learning | [Runtime migration manifest](../../../database/tempo_learning_database/ddl/migrations/manifest.json) orders owned schema; [remediation migration](../../../database/tempo_remediation_database/ddl/migrations/001_remediation.sql) is a module inside Learning. [Gateway](../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py) and [Database](../../../database/tempo_learning_database/connections.py) own transactions; review adapters borrow them. Accepted definitions, queue, attempts, history, schedule, required evidence, receipts and settlement stay in this authority. | Reader/writer pools each default to 4, configured maximum 16; essential maintenance/intake share a separate size-1 writer pool. Foreground acquire/lock/statement/transaction bounds: 250/250/1500/2500 ms. Background: 100/25/200/250 ms; intake transaction: 1000 ms. [Lifespan](../../../services/learning/runtime/src/tempo_learning_runtime/api.py) stops/awaits its maintenance task then signals HostActivity and EndgameIO without joining their active I/O, and closes only foreground/maintenance pools. Merged optional endgame pools are lazy reader/writer max1 each; the daemon closes its own pools after actual phase completion. Configured process maximum: `2 * pool_size + 3`. Learning database failure affects connected Study. |
| Content | [Store](../../../database/tempo_content_database/adapters/store.py), [explicit migrations](../../../database/tempo_content_database/ddl/migrate.py), [bridge approval/operation state](../../../services/content/src/tempo_content/bridge/__init__.py) own sources, inventory, immutable publication and delivery records. | CLI slices/leases can resume; runtime does not migrate at startup. An invalid successor retains the last complete immutable output. Publication alone cannot activate Learning. |
| Game Evidence | [Store](../../../database/tempo_game_evidence_database/adapters/store.py) owns its connections and [migrations](../../../database/tempo_game_evidence_database/ddl/migrations); raw sources, revisions, indexes, comparisons, desired results, outbox and accepted-head projection are local. | [CLI work loop](../../../services/game-evidence/src/tempo_game_evidence/cli.py) bounds slices; signals stop between operations. [Provider](../../../services/game-evidence/src/tempo_game_evidence/providers.py) cooldown/cursor/lease state survives restart. HTTP delivery occurs outside DB transactions; immutable attempted payloads and lease fences survive lost responses. |
| Intelligence | [Store](../../../database/tempo_intelligence_database/adapters/store.py) owns pools/transactions; [manifest](../../../database/tempo_intelligence_database/ddl/migrations/manifest.json) owns source caches, accepted inputs, runs, engine intents, reports and command receipts. These are not Learning state. | [API lifespan](../../../services/intelligence/src/tempo_intelligence/api.py) / [runner](../../../services/intelligence/src/tempo_intelligence/runtime.py) own background execution and cleanup. Pool defaults: foreground 4, background 1; transaction statement/lock/total bounds: 1500/250/3000 ms. Provider calls hold no DB transaction. Mutable mode can run its worker; merged Games-only mode excludes workers/providers and forces read-only transactions. Deployment remains unverified. |
| Stockfish / Maia | Workers own execution, cancellation, child processes and model/engine resources, not queue/review writes. See [Stockfish](../../../workers/stockfish/README.md) and [Maia](../../../workers/maia/README.md). | Caller job/worker session and generation checks fence late results. Intelligence [dispatch](../../../services/intelligence/src/tempo_intelligence/analysis/worker.py) cancels or closes a failed session before releasing durable capacity. Model/engine availability is optional for ordinary Study. |

Separate logical databases, roles and bounded transactions do not guarantee host
CPU, memory, disk, network or PostgreSQL cluster capacity isolation. Shared
infrastructure can still fail or contend. Effective deployment budgets and
shutdown under load require their own relevant evidence before claiming
operational independence; none is established by this documentation.

The [canonical Learning execution view](../../../services/learning/runtime/README.md#shared-learning-request-execution-gaps-main)
now describes the merged host/endgame source repairs: bounded-memory
request accounting, daemon-owned publication/cleanup, and complete optional
persistence phases on a separate worker and lazy size-one pools. No essential
filesystem or optional shutdown join is introduced. The healthy-idle lease can
permit new foreground work to overlap managed work for up to 15 seconds; missing
source protection requires expected workspace identities configured before
deployment, and default `[]` accepts absence. Canonical receipt authority and
late optional cleanup remain explicit. No live incident, native database proof,
host resource isolation or deployment is established by these source drawings.
