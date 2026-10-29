# Optional provider-worker packaging — #491

This is an **untested source extraction** of #558 `403c19e5`, based on main
`346d1741` (includes #586). No tests, builds, provider imports/HTTP, containers,
services, settings writes or activation were run for this extraction. The donor's
retained receipts below are historical evidence, not a pass for this final diff.

## Owned files and dependencies

The exact slice is `game_sync_current.py`, `provider_priority.py`, the separate
[`docker-compose.games-feedback.yml`](../../../../docker-compose.games-feedback.yml)
overlay and this README. The two Python modules retain the donor's exact bytes.
Only the `game-feedback-worker` service is copied from its Compose file. Current
base Compose, backend Dockerfile, API routes/Settings, central resource policy,
Study/maintenance/recovery and original #558 checkout are untouched. This avoids
reverting later backup/development changes that appear in a whole-donor diff.

Current main already supplies the saved Games configuration/store, acquisition,
canonical Games source/outbox and `receive_admitted_encounter` intake. The
extraction wires the existing concrete intake to the explicit CLI and rechecks
saved configuration before each provider unit. A changed scope refuses old
clients; the process must rebuild them on restart. It does not create settings,
choose filters, alter evidence identity or add a second delivery ledger.

`python -m app.product_composition.game_sync_current --workspace <workspace>` is
the existing entry point, enabled only by explicit
`TEMPO_PRODUCT_GAME_SYNC_ENABLED=1`. `--preview` instead uses the configured reader
URL and never dispatches work; `--once --max-units N` retains the bounded-unit
behavior. `--follow` requires explicit overlap and retains the saved filters.
These describe source entry points, not authorization or instructions to start a
process. No CLI command was executed here.

End-to-end dependencies stay separately tracked: #590 supplies durable
command/API/Settings and exact route transitions, #589 owns claimed-feedback
recovery, and #581 owns independent Learning preparation/empty-queue behavior.
This worker must acknowledge accepted evidence without waiting for any of them
to make a card available. Source assembly can proceed separately; activation and
assembled settlement remain unqualified.

## Default-off profile and resource declaration

The new overlay is combined with current base Compose only by the release owner;
it declares `game-feedback-worker`, profile `games-feedback`, `init: true`,
0.15 CPU, 256 MiB and 96 PIDs. It reuses the existing backend Dockerfile and writer
secret. No port, persistent worker volume, broker or service dependency is added
to Study or the API. The provider service depends only on the existing PostgreSQL
and Redis health conditions. Saved recurrence alone cannot start the process.

Only this optional worker receives its own writer DSN/passfile. The API keeps its
reader URL/passfile. The worker's read URL uses its existing writer-role profile
for current canonical capture; preview is explicitly query-only. These are the
donor's existing role boundaries, not new credentials, grants or a reader upgrade.
Each Games database section keeps `DedicatedGamesConnections`' existing finite
transaction/lock/statement bounds. Provider HTTP finishes outside those sections.
The receiver's source capture closes before its borrowed intake writer opens.

`config/resource-budgets.json` remains unchanged. Its listed optional services
sum to 1 CPU/2048 MiB; adding this process would sum to 1.15 CPU/2304 MiB. That
extra allocation is **not registered or approved here**. No capacity is inferred
from absent Maia, a prior loan or an old development ceiling. #586 removed the
fixed development CPU cap; current allocator/memory/concurrency rules and actual
resource accounting remain authoritative. The new service must be reconciled
with the existing managed allocation before activation. No guard bypass, budget
edit, service stop, cgroup inspection or new reservation occurred in this task.

## Receipt, state and failure ownership

The unchanged Games coordinator commits canonical source, original outbox and
checkpoint through its owning store. It delivers the original immutable command
to `CurrentProviderPriorityIntake`. The composition adapter checks actual
controls, captures current source, then delegates to the existing public Study
intake. It returns only after that writer commits. Games records the same receipt
and acknowledges its own outbox. Lost acknowledgment replays the original command;
accepted/pending is not a queue-availability promise. No queue refresh or private
queue polling is introduced. #589's claimed-work recovery remains separate.

```mermaid
sequenceDiagram
    participant W as Explicit optional worker
    participant G as Games canonical source and outbox
    participant I as Existing Study priority intake
    participant S as Independent Study refresh
    W->>G: Persist source, original command and checkpoint
    G->>I: Deliver original GameEncounter
    I->>I: Validate target/source; commit durable obligation
    I-->>G: Accepted/pending owner receipt after commit
    G->>G: Acknowledge original outbox delivery
    Note over W,S: Worker never calls or waits for refresh
    S->>I: Independently consume accepted obligation
    Note over S: Availability/settlement remains #581 qualification
```

## Retained evidence and honest limits

- #558's `packaged-intake-checkpoint.json` records packaged source `c7d78137`,
  image `sha256:a4c9912688dd21b7ee561056c60dcee858c4d7a525cf672e039d347f2c68195b`,
  schema054. The actual CLI/default capture/public intake stored a pending
  first-introduction receipt, duplicate replay kept its identity, and ordinary
  cards/reviews/queue were unchanged. That probe omitted command workers/browser
  and independent refresh; full health truthfully reported missing workers.
- The later `settlement-retry-result.json` at published `403c19e5` records the
  actual reader/command-worker/CLI prefix with one source/delivery/Study effect,
  typed pending receipt and protected attempt. The case **failed** pending→available
  after bounded registered wakes and a PostgreSQL timeout. Review/save/reload
  settlement was not reached. The precise inner statement/task state was not
  retained; the lease explanation is source inference, not a verified diagnosis.
- Earlier native provider helpers inject capture/clock/transport and prove their
  stated component boundaries only. They cannot replace default packaged CLI,
  current HTTP command/Settings, process allocation or Study settlement evidence.
  The donor process helper starts the runner with fake local HTTP; it is not
  by itself proof that the production entry point is packaged correctly.
- The existing selectors `test_edited_configuration_stops_stale_clients_before_provider_work`,
  `test_provider_root_commits_public_intake_before_ack_and_never_refreshes_queue`,
  and `test_default_intake_failure_is_locatable_without_private_payload_or_changed_retry`
  remain in the #558 donor's `test_product_sync_delivery.py`. They were not copied
  into the separate #590-owned test file or executed here. The new overlay has
  not been parsed or executed by Compose. Current-main runtime/image equivalence
  has not been established by the retained donor image.

## Read-only activation preparation

Activation requires an explicit saved `SyncConfiguration` for each selected
provider: workspace, provider, account, UTC window with precision, repertoire IDs,
speed selection, rated policy, recurrence (or explicitly off) and revision.
Missing configuration is refused; no account/window/default filter is invented.
Manual queued work can drain with recurrence off. A run must retain its original
job identity, source revision and saved window; forward windows are opt-in.

The retained provider/packaged evidence is synthetic. **Current user account,
window, filters, repertoires, recurrence and pending-job state are unverified.**
No live route, database connection, provider endpoint, token or setting was read
or changed in this preparation. Do not treat an account seen in historical games
or a fixture as current authorization/configuration.

The release owner must reconcile source/schema/profile and current role/secret
readiness using its existing approved path, obtain a redacted current saved-config
snapshot through the already authorized read route if needed, account for the new
optional allocation, and combine #590/#589/#581 at their declared boundaries.
Token values, account identifiers and private payloads must stay out of this
handoff and PR. There is no merge qualification, deployment or activation claim.
