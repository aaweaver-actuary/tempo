# Optional provider-worker packaging — #491

## Immediate collection-only release mode — Task C

The prepared `games-feedback` profile explicitly selects `--collect-only`.
The same optional worker acquires, normalizes and compares games, then commits
canonical imported source, original capture, findings, encounter evidence,
pending owner commands and checkpoints through the existing Games transaction.
It constructs no `CurrentProviderPriorityIntake` and supplies no receiving port.
`FeedbackRunner(delivery_enabled=False)` queries neither pending-delivery branch
and never invokes delivery. An outbox alone returns `idle`; repeated calls and
stop/restart preserve original pending commands, attempt counts and receipts.
Deliberately disabled delivery is neither success nor failure. Encounter mode
remains enabled; switching to the legacy lapse-producing path is not collection.

CLI contract (source interfaces, not activation instructions):

```text
python -m app.product_composition.game_sync_current [--collect-only | --feedback] [--workspace legacy]
    [--once --max-units N]
    [--follow --overlap-seconds O [--reconcile-interval-seconds R]]
```

`--once` drains ready acquisition within the existing 1..10000 unit bound and
exits on idle, even with pending deliveries. Existing write-DSN and explicit
`TEMPO_PRODUCT_GAME_SYNC_ENABLED=1` requirements remain. `--help` is inert;
`--preview` uses only the read-only profile and starts no provider or worker.
Omitting both mode flags keeps delivery disabled. Explicit `--feedback` requires
the qualified first-departure composition before constructing the real receiver;
the supported live transition uses the verified managed operator. Deleting
`--collect-only` or changing an environment variable cannot activate feedback.
The lower-level explicit composition option remains `delivery_enabled: bool = True`;
the CLI supplies it only from the finite feedback choice.

`--reconcile-interval-seconds R` requires `--follow` and an integer at least 60.
It forwards `reconciliation_interval_seconds` only when supplied. Task D owns
that optional `ContinuousSync` keyword and its scheduling policy; omitted values
preserve current construction. The prepared profile requires explicit
`TEMPO_PRODUCT_GAME_SYNC_OVERLAP_SECONDS` and
`TEMPO_PRODUCT_GAME_SYNC_RECONCILE_INTERVAL_SECONDS`. G must assemble D's scheduler
before executing that profile. C's native collection proof uses fixed-window
fixture acquisition, not combined forward/reconciliation execution. D's
`continuous_state(workspace, now)` and `forward_job` preview wiring also require
G's assembled validation; the retained preview currently uses `last_job`.

### Consumers and activation exclusions

At inspection baseline `c63c8947`, the two `FeedbackRunner.run_once` branches are
the only automatic consumers of `GamesStore.pending_delivery`. The Study
maintenance sweep reads `PostgresGameFeedbackStore.owed_page`, meaning obligations
already accepted by Study; it does not drain the Games outbox. Collection creates
no accepted Study obligation. Existing Study obligations can still progress
independently and must not be disabled to obtain a collection-only claim.

Activation must exclude another delivery-enabled provider worker sharing this
outbox. The existing background worker/scheduler were observed running during
read-only preparation. Legacy `game_derivation_misses`/priority work and explicit
`game_evidence.product_accept` or `product_games.retry_feedback` commands can
produce separate learning effects. Their queued/running work and installed
source correspondence remain release-owner checks; no process was stopped and
no host-wide absence is claimed. Publishing canonical source here does not
enqueue that legacy derivation pipeline.

### Bounded evidence and remaining wiring

Named `test_collect_only_*` regressions in
[`test_product_game_sync_integration.py`](../../../../backend/tests/test_product_game_sync_integration.py)
cover receiver-free import, both forbidden pending-delivery paths, original
retry state, restart/replay, enabled-mode compatibility, missing receivers,
optional reconciliation forwarding, CLI validation, cold imports/help and preview.
The single
[`test_collect_only_current_worker_persists_evidence_without_study_effects`](../../../../backend/tests/test_product_feedback_jobs_postgres.py)
uses the current composition, real published repertoire and canonical Games
stores on the existing labelled disposable PostgreSQL fixture. Provider HTTP
and foreground admission controls are fixture inputs; no receiving capability
is supplied. Fresh connections read retained evidence and original pending
command identity after restart/replay. Complete cards (including schedules),
queue, attempts, reviews, review receipts and Study feedback rows are compared
against nonempty sentinels, including a genuine saved review and issued attempt.
The fixture retains resource identity and verifies exact cleanup.

The frozen-main component selection passed 89 cases and retained one baseline
failure: `test_no_learning_sql_or_study_runtime_dependency` rejects the existing
public Study interface import in `coordinator.py`. The native collection case
passed independently. Its readback retained one pending command with zero
delivery attempts, one canonical source and two authentic comparison captures;
the baseline comparison hash includes snapshot capture time. B owns the stable
material-hash correction. Replay did not create another logical command/effect.
Compose rendering used synthetic values only and retained the default-off
profile; it did not start a service.

G owns packaged assembly, D's reconciliation/preview contract, resource wiring
with A, and root regression-inventory registration for these named cases. B/G
must reconcile the coordinator's receiver annotation with explicitly disabled
delivery; C does not edit B's coordinator. The existing feedback policy guard
rejects the already-present public `app.study.interfaces.game_priority` import
at the baseline; C leaves that separate failure visible. #594/#595, successful
Study preparation and settlement are not collection prerequisites. Source/native
proof does not establish deployment/resource isolation, live-provider acceptance,
installation or activation.

The completion labels for this bounded mode are: **collection implemented;
delivery disabled; observations durable; Study reprioritization not activated**.

## Delivery-enabled packaging context

The goal is game-versus-repertoire comparison: a missed own decision identifies
an important move and requests front-of-queue Study reinforcement. That goal does
not depend on whether the receipt calls it missed or first introduction, or proof
that the card was already known at the time. Preserve real timestamps, source/card
identity and retry receipts; never invent chronology or completed Study work.
There is no coaching, explanation or engine requirement.

This source was extracted from #558 `403c19e5`, based on main `346d1741`
(includes #586). PR #592 records current bounded qualification: CLI help/import,
rendered Compose and the existing component tests. The donor receipts below remain
historical evidence; none establishes activation or end-to-end settlement.

## Owned files and dependencies

The original packaging slice is `game_sync_current.py`, `provider_priority.py`, the separate
[`docker-compose.games-feedback.yml`](../../../../docker-compose.games-feedback.yml)
overlay and this README. Qualification additionally restores the small synthetic
process helper documented below. The intake adapter retains the donor's exact bytes.
The worker root adds early validation through the existing pure forward-window
policy.
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

The existing CLI requires `TEMPO_PRODUCT_GAME_SYNC_ENABLED=1` for work. The optional
`games-feedback` profile now explicitly passes `--follow` and requires
`TEMPO_PRODUCT_GAME_SYNC_OVERLAP_SECONDS`: an operator-selected integer 0..3600,
strictly smaller than every recurring provider's saved seed-window width. There
is no default overlap. Recurring seeds must be 60 seconds..1 day; the existing pure
`ForwardWindow.next_job` validates them before client construction or work claims.
Invalid large windows are refused, never shortened or converted. It preserves
account, filters, repertoires, revision and original pending-job checkpoints.

Historical backfill is a different mode: the existing CLI without `--follow`,
using its explicit fixed-window job and bounded `--once --max-units N` drain.
A 90 day saved range remains valid backfill, but cannot serve as a recurring follow
seed. Finish that historical job and explicitly save a suitable forward seed;
never replace an outstanding continuation with a new window. Recurrence off
remains off, even in the follow process; manual queued jobs may still drain.
`--preview --follow --overlap-seconds N` is the existing query-only preview.
These describe interfaces, not authorization or instructions to start anything.
`--help` was checked during qualification; executable and preview modes were not run.

End-to-end dependencies stay separately tracked: #590 supplies durable
command/API/Settings and exact route transitions, #589 owns claimed-feedback
recovery, and #581 owns independent Learning preparation/empty-queue behavior.
This worker must acknowledge accepted evidence without waiting for any of them
to make a card available. Source assembly can proceed separately; activation and
assembled settlement remain unqualified.

## Default-off profile and resource declaration

The new overlay is combined with current base Compose only by the release owner;
it declares `game-feedback-worker`, profile `games-feedback`, `init: true`,
unrestricted CPU, 256 MiB and 96 PIDs under Task A policy. It reuses the existing backend Dockerfile and writer
secret. No port, persistent worker volume, broker or service dependency is added
to Study or the API. The provider service depends only on the existing PostgreSQL
and Redis health conditions. Saved recurrence alone cannot start the process.
Without follow, fixed-window recurrence repeats the same UTC range; it must not
be advertised as ongoing imports.

Only this optional worker receives its own writer DSN/passfile. The API keeps its
reader URL/passfile. The worker's read URL uses its existing writer-role profile
for current canonical capture; preview is explicitly query-only. These are the
donor's existing role boundaries, not new credentials, grants or a reader upgrade.
Each Games database section keeps `DedicatedGamesConnections`' existing finite
transaction/lock/statement bounds. Provider HTTP finishes outside those sections.
The receiver's source capture closes before its borrowed intake writer opens.

Task A registers `game-feedback-worker` in `config/resource-budgets.json` with
256 MiB and 96 PIDs, without an ordinary CPU quota. Current allocator, memory,
concurrency and ownership rules remain authoritative. Source registration does
not establish installed headroom: inspect the frozen release and effective limits
before the explicit operator switch. No capacity is inferred from absent optional
services or another task's reservation.

## Delivery-enabled receipt, state and failure ownership

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
    alt Prepared collection-only profile
        Note over W,G: No receiver constructed; no pending-delivery query
        G-->>W: Original command remains pending without receipt/attempt
        Note over I,S: No new accepted Study obligation
    else Explicit delivery-enabled compatibility
        G->>I: Deliver original GameEncounter
        I->>I: Validate target/source; commit durable obligation
        I-->>G: Accepted/pending owner receipt after commit
        G->>G: Acknowledge original outbox delivery
    end
    Note over W,S: Worker never calls or waits for refresh
    S->>I: Independently consume accepted obligation
    Note over S: Availability/settlement remains #581 qualification
```

## Retained evidence and honest limits

- #558's `packaged-intake-checkpoint.json` records packaged source `c7d78137`,
  image `sha256:a4c9912688dd21b7ee561056c60dcee858c4d7a525cf672e039d347f2c68195b`,
  schema054. The actual CLI/default capture/public intake stored a pending
  first-introduction-labelled receipt, duplicate replay kept its identity, and
  ordinary cards/reviews/queue were unchanged. That is evidence of intake, not a
  demonstration of actual front-of-queue reinforcement. That probe omitted command workers/browser
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

The retained provider/packaged evidence is synthetic. Current saved-configuration
and resource observations belong in the private dated readiness handoff, not in
source as a lasting claim of capacity or activation. No account, identifier or
token is published here. Account, seed window, filters, repertoire selection,
recurrence and overlap must be explicitly supplied before activation; none is
inferred from historical games. The ongoing goal selects follow mode, so no extra
mode preference is needed for the ongoing profile. A historical backfill, if
wanted, remains a separate explicit fixed-window request.

Readable quiet foreground controls, selected-daemon CPU allocation and managed
memory headroom are distinct observations; none grants activation or proves idle
CPU/free physical memory. Task A supplies the central named resource registration; installed allocation
and frozen configuration correspondence still require release-owner verification. Bounded readiness reads use
only existing approved local configuration/control routes and resource-owner
read functions. No tests, builds, provider HTTP/import, direct database access,
settings write, credential or service change accompanies them.

The release owner must reconcile source/schema/profile and current role/secret
readiness using its existing approved path, obtain a redacted current saved-config
snapshot through the already authorized read route if needed, account for the new
optional allocation, and combine #590/#589/#581 at their declared boundaries.
Token values, account identifiers and private payloads must stay out of this
handoff and PR. These readiness observations do not establish merge qualification, deployment or activation.


## API/Settings coordination and mixed-side finding

#590/#591's stored `SyncConfiguration` remains unchanged. Required copy/interface
handoff: fixed UTC ranges and arbitrary-width backfills are still supported;
recurrence repeats that range unless the explicit follow process is selected.
Ongoing follow uses a 60 second..1 day seed plus explicit smaller overlap, not a
silent conversion of a 90 day range. Overlap is an operator/process input in this
slice, not a new Settings/DTO/database field. No API/Settings file is edited here;
the API owner receives this precise copy requirement through the coordinator.

A separate source trace in `games/feedback/coordinator.py` shows all configured
snapshots compared to each game, while `comparison.py` rejects learner-color
mismatch. An unrelated opposite-color snapshot can therefore quarantine a valid
match. This is not attributed to any historical failed run without its captured
configuration. The owning correction must select every exact same-color snapshot
and preserve their complete conflict/mapping evidence; no-match must remain
honestly unresolved. It is outside this four-file packaging lease and is proposed
to the coordinator for explicit ownership, not silently patched here.

## Existing process-stop fixture repair

The regular `test_product_game_sync_integration.py` file referenced a missing
`backend/tests/fixtures/product_game_worker_process.py`. Qualification reproduced
two failures before the child could reach local HTTP. Restore only the existing
published local-process helper from #558: it rewrites transport to loopback, uses
in-memory Games state and the real optional runner, and opens no database. The
existing terminate/kill cases remain the regression owners and retain their
native ordinary-Study review assertions. This fixture repair changes no provider
production behavior or activation policy. Exact initial and repaired results are
recorded in the PR, including verified disposable PostgreSQL cleanup.
