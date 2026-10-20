# Transactions, data flow and failure sequences

[Index and source/status legend](README.md) · [System and ownership](system.md) ·
[Study/Games detail](study-games.md)

These are bounded sequences for the named paths, not every endpoint or branch.
Notes identify durable asynchronous work and transaction owners. Network arrows
do not imply a distributed transaction. No sequence requires Study to call Games.

## Review commit and recovery (MAIN)

Scope: Learning-connected aggregate review through the installed review
application. Typed Study/tactics/defense attempts retain their own handlers and
identity rules; they are not silently folded into `cards.review`.

```mermaid
sequenceDiagram
  actor User
  participant S as Study session /<br/>review-outbox
  participant B as Browser IntentStore
  participant A as Learning HTTP and<br/>gateway
  participant R as Review application +<br/>borrowed adapter
  participant D as Learning PostgreSQL
  User->>S: Complete issued attempt with<br/>stable logical ID
  S->>B: Retain immutable payload,<br/>authority and request ID
  alt Browser retention fails
    B-->>S: storage-failed, no new<br/>unretained dispatch
  else Retained
    B-->>S: pending intent committed<br/>locally
    S->>A: POST card review with same<br/>Idempotency-Key
    Note over A: Authenticate workspace/origin before<br/>DB access
    Note over A: PROPOSED host repair: bounded memory<br/>request entry / exit; daemon owns I/O
    A->>D: record_intent in its own<br/>transaction
    D-->>A: queued intent committed (or<br/>matching identity found)
    Note over A,D: Execution transaction, operation<br/>advisory lock and receipt row lock
    A->>R: submit_review using caller<br/>connection / handler<br/>savepoint
    R->>D: Lock cards then queue date,<br/>validate attempt, revision,<br/>receipt
    Note over R,D: Optional successor uses nonblocking<br/>locks, never delays current review
    R->>D: Required evidence +<br/>review/history/schedule +<br/>attempt completion
    R->>D: Required local obligation<br/>settlement, logical receipt,<br/>queue effects
    alt Valid execution
      R-->>A: Persisted result (or<br/>original idempotent result)
      A->>D: Complete operation receipt,<br/>commit with all handler<br/>effects
      A-->>S: Authoritative result<br/>(response may be lost)
    else Terminal domain conflict
      A->>D: Roll back handler savepoint,<br/>commit failed operation<br/>receipt
      A-->>S: Conflict / failure, no<br/>partial handler effects
    else Transient DB contention or timeout
      A->>D: Roll back execution,<br/>original queued intent<br/>remains
      A-->>S: Unavailable, retain identity<br/>for retry
    end
    opt Response lost or acceptance still unconfirmed
      S->>A: Read original operation<br/>receipt / replay same<br/>request
      A-->>S: Original result, pending<br/>state, or explicit conflict
      Note over S,A: Unknown/pending is not committed,<br/>changed payload under same ID<br/>conflicts
    end
    S->>B: Persist authoritative<br/>settlement before projecting<br/>it
    opt Settled review may advance
      S->>A: Load issued next attempt /<br/>validate prepared<br/>continuation
      A-->>S: Next attempt or explicit<br/>load failure
      Note over S,B: Next-load retry uses progression<br/>checkpoint, never resubmits grade
    end
  end
```

Source owners: [`review-outbox`](../../../app/lib/review-outbox.ts) retains immutable
aggregate intent and adapts receipts;
[`createStudySession`](../../../packages/study-session/src/index.ts) claims delivery,
persists settlement, fences presentation generations and bounds next acquisition;
[`IntentStore`](../../../packages/browser-intents/src/index.ts) owns browser
transactions. [`learningFetch`](../../../app/lib/study-connection.ts) restricts
authority; the [HTTP review handler](../../../services/learning/runtime/src/tempo_learning_runtime/api.py)
dispatches to [`gateway`](../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py).
[`submit_review`](../../../services/learning/application/src/tempo_learning_review/application.py)
uses [`ReviewSubmissionStore`](../../../services/learning/application/src/tempo_learning_review/ports.py),
implemented by [`LearningReviewAdapter`](../../../services/learning/runtime/src/tempo_learning_runtime/review_adapter.py).
[Scheduling/review effects](../../../services/learning/runtime/src/tempo_learning_runtime/review_service.py)
and [required settlement](../../../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py)
remain Learning-local under the borrowed transaction. The
[`Database`](../../../services/learning/runtime/src/tempo_learning_runtime/database.py)
context commits/rolls back, not the application port.

Transport idempotency and logical attempt identity are separate protections.
Reconciliation may use a new transport operation while retaining the original
logical attempt. A rejected/discarded attempt must not advance as a successful
review. Browser disposal preserves retained work; a late response may settle its
original record but cannot overwrite a newer session snapshot. Auth rejection,
storage failure, pending save and failed next load remain different states.

Ordinary queue reads are not necessarily side-effect free: the
[queue payload handler](../../../services/learning/runtime/src/tempo_learning_runtime/api.py)
dispatches `queue.ensure_current`, runs at most two foreground maintenance assists,
and [queue command handlers](../../../services/learning/runtime/src/tempo_learning_runtime/queue_commands.py)
own attempt issuance. None of those operations calls the game producer.


HostActivity note source: proposed checkpoint
[`09cb4eef`](https://github.com/aaweaver-actuary/tempo/blob/09cb4eef027666aaa3e728660b9e7ca39eb36091/services/learning/runtime/src/tempo_learning_runtime/host_activity.py).
The request-accounting note is source repair only; deployment is unverified.
A last healthy idle snapshot remains valid for its existing 15-second lease;
[the host execution view](study-games.md#shared-learning-request-execution-gaps-main)
describes stale/missing expected-source admission and asynchronous cleanup.


## Observed game evidence to Learning (MAIN)

Scope: independent Game Evidence v1/v2 delivery into independent Learning. This is
distinct from the [legacy-local receiver](system.md#legacy-assembly-and-existing-coupling).
The Games viewer is not the evidence producer, and reading a game does not trigger
this sequence.

```mermaid
sequenceDiagram
  participant G as Game Evidence<br/>compare / reconcile<br/>/ deliver
  participant GD as Game Evidence<br/>database
  participant L as Learning<br/>authenticated<br/>evidence receiver
  participant LD as Learning database
  participant M as Learning essential<br/>maintenance /<br/>admission owner
  participant S as Study ordinary queue<br/>client
  G->>GD: Persist desired comparison<br/>and reconciliation request
  Note over G,GD: Eligible finalization + immutable<br/>outbox insertion commit together
  G->>GD: Claim delivery under<br/>game/outbox locks, commit<br/>lease and attempt
  G->>L: POST immutable<br/>EvidenceDelivery with<br/>evidence credential
  Note over G,L: HTTP outside Game Evidence<br/>transaction, no foreign DB access
  L->>LD: Begin bounded intake<br/>transaction (background<br/>writer pool)
  L->>LD: Validate against own active<br/>accepted definitions and<br/>source/game heads
  alt Accepted current delivery or exact replay
    L->>LD: Accept facts/revisions +<br/>acknowledgement + durable<br/>remediation request
    LD-->>L: Atomic commit
    L-->>G: completed acknowledgement =<br/>accepted evidence
  else Conflict / invalid / stale authority
    L-->>G: Explicit rejection or<br/>conflict, no manufactured<br/>queue/review effect
  else Intake contention or transaction expiry
    L->>LD: Roll back all acceptance<br/>effects
    L-->>G: Retryable unavailable<br/>response
  end
  alt Valid matching acknowledgement received
    G->>GD: Lease-fenced receipt +<br/>accepted heads +<br/>reconciliation request,<br/>commit
  else Lost / invalid response or unavailable receiver
    G->>GD: Retain same attempted<br/>payload and retry status,<br/>fence successors
    Note over G,GD: Never rewrite possibly accepted<br/>bytes or infer non-acceptance from<br/>HTTP alone
  end
  opt Durable remediation work is available
    M->>LD: Claim task in short<br/>transaction, commit before<br/>card/queue locks
    M->>LD: New transaction: prepare one<br/>card, recheck eligibility<br/>and authority
    M->>LD: Sole admission writer<br/>applies valid queue effect
    M->>LD: Commit effects + cursor only<br/>under current unexpired<br/>lease/generation
    Note over M,LD: Yield/rollback releases owned claim<br/>or recovers by expiry, no producer<br/>wait
  end
  S->>L: Read/issue normal queue,<br/>submit controlled review
  Note over S,L: Game Evidence and Games may be<br/>stopped, accepted local facts remain
```

[`Store._compare`](../../../services/game-evidence/src/tempo_game_evidence/store.py)
and [`reconciliation`](../../../services/game-evidence/src/tempo_game_evidence/reconciliation.py)
own desired results/outbox/accepted-head projection.
[`deliver_one`](../../../services/game-evidence/src/tempo_game_evidence/delivery.py)
owns the attempt, external HTTP, immutable retry and local receipt lease fence.
[`EvidenceDelivery` contracts](../../../contracts/game-evidence/README.md) express
observed facts and obligations, not permission to schedule or invent controlled
study evidence. Learning's
[HTTP intake](../../../services/learning/runtime/src/tempo_learning_runtime/api.py)
uses `connection(background=True, intake=True)`;
[`receivers.receive`](../../../services/learning/runtime/src/tempo_learning_runtime/receivers.py)
and [`remediation_adapter.receive`](../../../services/learning/runtime/src/tempo_learning_runtime/remediation_adapter.py)
validate/store inside it. The
[installed remediation repository](../../../services/learning/remediation/src/tempo_learning_remediation/repository.py)
owns Learning-local obligation persistence. [`maintenance`](../../../services/learning/runtime/src/tempo_learning_runtime/maintenance.py),
[`durable_tasks`](../../../services/learning/runtime/src/tempo_learning_runtime/durable_tasks.py)
and [`admission.admit`](../../../services/learning/runtime/src/tempo_learning_runtime/admission.py)
own claims, short slices and canonical queue writing.

Acceptance is bounded to at most 128 wire items in the 1000 ms intake transaction;
ordinary maintenance retains its 250 ms transaction bound and size-1 pool.
`completed` acknowledges durable acceptance, not completed studying or synchronous
admission. A miss may retain a documented allowance/progression exception, but
cannot bypass burial, retirement, invalidity, deletion or revision validation.
Controlled review settlement stays in the review transaction shown above.

Delivery `401` is retained retryable with a minimum 60-second backoff; replacing
the producer's private token file requires producer restart/reload, not Study
restart. Unverifiable terminal/conflict/stale outcomes can block reconciliation
with diagnostics. See [delivery/auth recovery](../../../services/game-evidence/README.md#authentication-recovery).
No command here grants a producer cross-component database writes.

## Content publication and serving activation (MAIN)

The explicit bridge operator talks to owners' public boundaries; it is not part
of an ordinary Study request. Arrows show data/command flow, not shared database
connections or atomic commits across services.

```mermaid
sequenceDiagram
  actor Operator
  participant B as A bridge operator<br/>tool
  participant C as Content CLI / store
  participant L as Learning publication<br/>API
  participant D as Learning database
  Operator->>B: Explicit import/proposal,<br/>exact approval and chosen<br/>authority
  B->>C: Prepare / approve durable<br/>source operation
  C-->>B: Immutable publication /<br/>routes and operation identity
  Note over C: Content owns source, inventory<br/>and publication transactions only
  B->>L: Read original publication<br/>operation receipt
  alt Original intent not yet accepted
    B->>B: execution_bundle(export,<br/>due_date) builds Learning<br/>execution facts / bundle
    B->>L: Explicit v2 stage with<br/>operator credential
    L->>D: Store inactive validated<br/>bundle
    B->>L: Activate same immutable<br/>identity / request
    Note over L,D: Learning rechecks generation,<br/>complete definitions, ownership and<br/>revisions
    L->>D: Own transaction: serving<br/>switch + stale-attempt<br/>fences + queue effects +<br/>receipt
    L-->>B: Complete receipt or explicit<br/>pending/conflict/unavailable
  else Receipt already exists
    L-->>B: Original<br/>complete/failed/queued state<br/>bound to request
  end
  opt Lost response or still queued
    B->>L: Recover original receipt /<br/>retry original operation
    L-->>B: Authoritative state (never<br/>infer failure from missing<br/>response)
  end
  B->>C: Propose completion receipt<br/>or request failure recording
  C->>L: Content's own authenticated<br/>GET of original operation<br/>through receipt_transport
  L-->>C: Original bound terminal<br/>receipt or unconfirmed state
  alt Content verifies matching terminal result
    C->>C: Validate original binding,<br/>matching receipt / terminal state<br/>then commit own record
    C-->>Operator: Durable bridge status /<br/>point-in-time activation<br/>evidence
  else Unavailable, pending or mismatched receipt
    C-->>B: Refuse final recording,<br/>retain original operation
  end
```

Source anchors: Content [`bridge`](../../../services/content/src/tempo_content/bridge.py)
and [`CLI`](../../../services/content/src/tempo_content/cli.py), external
[`Operator` receipt/retry workflow and `execution_bundle`](../../../tools/analysis-restoration/a_bridge/src/tempo_a_bridge/cli.py),
Learning [publication routes](../../../services/learning/runtime/src/tempo_learning_runtime/api.py),
[`publication_bridge`](../../../services/learning/runtime/src/tempo_learning_runtime/publication_bridge.py),
[`publications`](../../../services/learning/runtime/src/tempo_learning_runtime/publications.py)
and [`gateway`](../../../services/learning/runtime/src/tempo_learning_runtime/gateway.py).
Content exports serialized publication/routes; the external bridge constructs
Learning execution facts/bundle. Content's supported CLI then independently calls
[`receipt_transport.authenticated_operation` / `verified_receipt` / `verified_terminal_failure`](../../../services/content/src/tempo_content/receipt_transport.py)
before its local `record` / `record_terminal_failure` write. That verification
uses Content's configured original Learning authority and operator credential,
checks the original request binding and terminal state, and for completion checks
that the proposed receipt matches the authenticated response. It is not merely
trusting the bridge's earlier receipt read. Failure/unavailability retains the
operation rather than recording an unverified result. Content's CLI therefore has
this explicit operator-invoked Learning HTTP dependency; it is not startup or
ordinary Study work.
Retirement is a separate command; it locks/fences affected definitions and attempts.
Failed activation must preserve the previous serving generation. Staging or a
Content publication alone supplies no serving authority. An activation receipt is
historical evidence, not proof that its generation remains current forever.
An Analysis response proposal is a draft input to this explicit approval path,
never automatic Content approval or Learning admission.

## Games read, failure and disposal (MAIN, merged PR322)

PR322 implementation `653a85bb5f5b3e520c0eda0c36234d3b416708b4` merged as
`bc746728ca1967cc8ad9991cbe8c12099a01a3ae`; see the
[source-linked component view](study-games.md#merged-pr322-games-reader).

```mermaid
sequenceDiagram
  actor User
  participant V as Workbench Games view
  participant H as GamesReader HTTP<br/>adapter / connection<br/>owner
  participant A as Games-only API
  participant R as GamesReader query<br/>projection
  participant D as Intelligence<br/>database
  User->>V: Connect and choose existing<br/>game scope
  V->>H: listScopes / readScope /<br/>readGames
  H->>A: Authenticated GET only
  Note over A: Check caller credential, workspace<br/>and origin, unsafe methods rejected
  A->>R: Read accepted scope and<br/>bounded game page
  R->>D: One owner read-only<br/>transaction, bind<br/>run/input/revision/cursor
  alt Valid accepted scope
    D-->>R: At most 10 game payloads +<br/>one lookahead
    R-->>A: Page and next cursor, frozen<br/>comparison provenance
    A-->>H: Validated workspace / scope<br/>response
    H-->>V: Result only if connection<br/>generation still current
    V-->>User: Legal replay and original<br/>matched/missed/unknown<br/>context
  else Auth, schema, missing scope, stale cursor or database failure
    A-->>H: Explicit<br/>unavailable/reconnect/error<br/>result
    H-->>V: Error retained within Games
    V-->>User: No hidden import, run<br/>creation, provider refresh<br/>or Study repair
  end
  opt View or connection is disposed while request is in flight
    V->>V: Invalidate view generation,<br/>destroy owned board
    H->>H: Disconnect invalidates<br/>connection generation
    Note over V,H: Late response cannot mutate<br/>replacement view, no Study lifecycle<br/>action
  end
  Note over A,D: Service shutdown closes owned<br/>pools/executor, starts no optional<br/>worker
```

The query reader does not use engine-position progress, change accepted inputs,
write a command, contact a provider or query Study/Learning. Repertoire conformance
is frozen to the accepted input; a current-repertoire comparison would require a
separate explicit operation. Failure/shutdown isolation is a source boundary;
[shared host/database budgets remain deployment-unverified](study-games.md#failure-lifecycle-and-resource-acceptance).
Parsing this sequence or passing component tests cannot certify a live allocation.
