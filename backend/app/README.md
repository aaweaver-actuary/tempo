# Backend application

**MAIN source; deployment unverified.** Reviewed against `1ce172e9` (October 26, 2026 UTC). This Python package contains the local product's legacy HTTP/application composition and progressively separated logical owners. Start with the [finite organization checklist](../ORGANIZATION.md); 202 source reviews are not 202 completed implementation changes.

| Location | Responsibility |
| --- | --- |
| [contracts](contracts/README.md) | Explicit request/fact/interface definitions, grouped by owner. |
| [chess](chess/README.md) | Neutral pure chess algorithms and established identity exports; no engine execution. |
| [study](study/README.md) | Daily practice policy, not authored/imported chapters. |
| [games](games/README.md) | Game records, provider adapters and ingestion/profile calculations. |
| [authoring/discovery](authoring/discovery/README.md) | Pure acceptance decisions over supplied facts/capabilities. |
| [studies](studies/README.md) | Authored/imported PGN and chapter/exercise concerns; distinct from daily Study. |
| [services](services/README.md) | Remaining legacy workflows and mixed composition awaiting the recorded splits. |
| [main.py](main.py) | Existing HTTP/composition root; handler/lifecycle separation remains pending. |
| [models.py](models.py) | Explicit compatibility exports of canonical owner transport models; no model implementations. |

```mermaid
flowchart LR
  composition["Existing outer HTTP/command/worker composition"] --> study["Study practice policy"]
  composition --> games["Games ingestion/provider implementation"]
  composition --> authoring["Authoring acceptance and Studies PGN leaves"]
  study --> chess["Neutral pure chess support"]
  games --> contracts["Explicit contracts"]
  study --> contracts
  authoring --> contracts
  composition --> database["Root database owner adapters"]
  database --> timing["One shared runtime timing ContextVar contract"]
  composition --> timing
```

This bounded view describes the newly separated leaves and timing import, not every legacy workflow or a deployed isolation claim. Each domain can use its own internals, explicit contracts, neutral pure libraries and external packages. Sibling implementation imports are forbidden even through a public entry point. Outer composition connects implementations and injects capabilities; it must not make ordinary Study depend on Games.

## Public surfaces and extension boundary

| Surface | Exported functionality / permitted direction | Remaining limit |
| --- | --- | --- |
| [Explicit contracts](contracts/README.md) | Import named `app.contracts.<owner>` facts, request types and semantic ports. Domain code uses its own implementation, explicit contracts and neutral pure libraries. | Contracts include established framework/model dependencies; a public barrel does not authorize sibling-implementation imports. |
| [Frozen Study policy](study/frozen_order/README.md) | `annotate`, `proposal_valid`, `order_queue_slots` and typed facts/predicates accept supplied data/capabilities. Outer composition binds the database adapter. | The legacy facade still reaches feedback and queue-ordering implementation. The pure leaf's separation does not isolate every caller. |
| [Learning review application](../../services/learning/application/src/tempo_learning_review/__init__.py) | `apply_review`, `review_context`, `submit_review`, `ReviewStore` and `ReviewSubmissionStore` provide semantic review behavior. `register_review_commands(register, submit=..., reconcile=...)` explicitly registers `cards.review` and `cards.review.reconcile`; import alone does not register them. | Caller/gateway owns the transaction and settlement. This separate installed package is not an extension of the legacy global registry. |
| [Legacy command gateway](command_gateway.py) | `register_command(name, handler, prepare=...)` registers existing command handlers; the gateway owns receipt/transaction execution. | Process-global handler/preparer dictionaries, concrete `PostgresConnection` handlers and command-specific execution branches remain. It is not a generic isolated-plugin contract. |
| [HTTP and worker composition](main.py) | Routes translate requests and compose existing capabilities; [background worker](background_worker.py) starts/stops its coordinator. | Both entrypoints use the same compatibility coordinator composition and singleton. The worker still waits for API health; lifecycle remains coupled to that health check. |

**Coupling has been reduced, not eliminated.** Moved policy leaves and removed
optional producers remove specific edges; the [retirement map](services/README.md#retired-optional-capabilities)
names them. Existing local `organized-*` import contracts cover selected moved
namespaces, not all services, commands or `main.py`; no repository-wide cycle or
plugin lifecycle audit is claimed here. The
[resume priorities](../../docs/architecture/diagrams/README.md#bounded-resumption-priorities)
separate concrete follow-up work from the already-implemented public surfaces.

An additional optional integration must supply a narrow owner contract and be
wired at an outer composition root, with one owner for state, effects, retry and
shutdown. It must not gain direct queue/review writes or make ordinary Study wait
for its availability. Adding another global registry entry is not evidence of
safe removal, failure independence or bounded shared-host resources.

Database implementation belongs in the single [root database tree](../../database/README.md), with owner packaging and transaction authority retained. The remaining technical composition edges are tracked there; physical relocation is not proof of full independence. The former `app.database` and its SQLite-first description are obsolete: current connections retain explicit PostgreSQL/SQLite compatibility routing in the database owner, without changing deployment here.

See [validation and existing limitations](../../database/VALIDATION.md), the [architecture index](../../docs/architecture/diagrams/README.md) and the single [proposal-only simplification list](../../database/SIMPLIFICATION.md).

### Proposed B1 command identity boundary

The pure [`command_identity`](contracts/command_identity.py) composition binds
owner-specific original-request projections for review/checkpoint, paste and
discovery to the historical digest format. The gateway keeps its public digest
adapter; HTTP dispatch and retained import-receipt reading use the pure codec.
Dispatch still imports the gateway for command errors and receipt access. No
handler registration, transaction ownership, or startup behavior changes.

```mermaid
flowchart LR
  dispatch["HTTP dispatch"] --> identity["Pure command identity composition"]
  imports["Import receipt reader"] --> identity
  gateway["Gateway digest adapter"] --> identity
  identity --> review["Review/checkpoint projection"]
  identity --> paste["Paste request projection"]
  identity --> discovery["Discovery acceptance projection"]
```


### Compatibility coordinator and authored Studies

The API and SQLite worker call [compatibility composition](product_composition/compatibility_coordinator.py), which registers the existing handlers once on the existing coordinator singleton. The worker still waits for API health and owns its writer/coordinator start and stop. The [authored Studies router](study_routes.py) receives queue refresh per router construction; its native command path remains unchanged. This source boundary does not establish optional-service isolation or deployment.

```mermaid
flowchart LR
  API[API composition] --> CC[Compatibility coordinator composition]
  Worker[SQLite compatibility worker] --> CC
  CC --> S[Existing coordinator and handlers]
  API --> R[Authored Studies router factory]
  API -->|supplies queue refresh| R
  R --> Q[Queue refresh operation]
```

### Proposed B2 command registration seam

`contracts.command_registry` declares frozen, generic decode/preparation/apply
relationships and explicitly constructed registries without importing runtime
providers. The gateway's existing handler and preparer dictionaries are the
same registry-owned objects, and the compatibility registration and executor
continue to use them. `register_descriptor` adds a typed registration path constrained to the
existing `PostgresConnection` supplied by the executor; independent registries
may use another transaction type. Existing feature registrations have not yet migrated. B1 still hashes the
original payload before preparation, and all transaction and exceptional
execution paths remain unchanged. Tests that temporarily replace private gateway
attributes are test doubles, not additional production registries.

```mermaid
flowchart LR
  Legacy["Legacy register_command"] --> Maps["One gateway registry's handler/preparer maps"]
  Descriptor["Frozen typed descriptor"] --> Registry["Registry erasure boundary"]
  Registry --> Maps
  Executor["Existing gateway executor"] --> Maps
  Identity["B1 original-payload identity"] --> Executor
```

Prefix foreground deferral has one public exception class. The diagnostic API
re-exports that same class for existing callers, retaining its HTTP response and
the diagnostic's deadline-first check. The gateway uses the public contract to
recognize immediate foreground deferral during preparation.

```mermaid
flowchart LR
  Gateway["Command gateway"] --> Error["Public prefix_errors exception"]
  API["Prefix evaluation API compatibility alias"] --> Error
```
