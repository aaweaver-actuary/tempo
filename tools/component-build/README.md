# Component build and discovery tools

These are small validation entry points, not a dependency solver or replacement CI planner.
`components.json` declares implemented artifacts, mapped composition owners and
planned targets; `components.schema.json` describes its fields. Mapped and
planned owners are not executable artifact selections.
The Game Evidence contract is an implemented artifact; `catalog` references its
existing contract catalog, which remains the authority for models, wire versions,
schemas and owners. Registration does not establish production adoption.

For implementation discovery, also inspect current package READMEs, public APIs
and consumers using the [discovery guide](../../docs/architecture/component-reconstruction.md#discover-before-building).
For example, `packages/board-ui` is an implemented package with a registered
owner-local qualifier. A mapped composition is an ownership record, not a
standalone artifact or permission to duplicate its implementation.

## Setup and commands

From the repository root:

- `npm ci --prefix tools/component-build --workspaces=false`: TypeScript parser only.
- `npm ci --prefix packages/chess-line --workspaces=false` then `npm run build --prefix packages/chess-line`.
- `node tools/component-build/check-components.mjs`: manifest, package graphs, public APIs and current consumers. Uses Python stdlib only; `TEMPO_COMPONENT_PYTHON` selects its interpreter.
- `node tools/component-build/check-components.mjs --component game-evidence-contracts`: only that implemented owner and declared component dependencies; no application source is needed. Repeat `--component` to select more owners. Add `--consumers` to request legacy current-source consumer checks explicitly.
- `node tools/component-build/test-node.mjs tools/component-build/tests`: deliberate negative boundary and discovery tests.
- `node tools/component-build/test-node.mjs tools/component-build/tests 'Graph preparation'`: Study cutover launcher import guards, negative fixtures and legitimate public/injected composition. These preparation files are outside registered artifact source closures. No historical migration or old obligation preservation is required by this check.
- `node tools/component-build/check-graph-preparation.mjs [exact/source/path ...]`: inspect present named preparation files, or require every explicitly selected path. `TEMPO_COMPONENT_ROOT` selects a disposable source tree. Missing optional sources are reported absent, never qualified. The guard reads source only; it checks public imports and backward wiring, not runtime effects, graph semantics or permission to reset/cut over. Existing runtime/component isolation qualification retains those behavior proofs.
- `node tools/component-build/restricted-build.mjs [report-path]`: allowlisted builds, npm tarball / Python wheel and sdist, installed tests in clean consumers, runtime guards, dependency inventories, zero-selection probes. Requires Node, npm, uv and available Python 3.12/3.14; creates no Docker/service/model/Rust environment. Reports stage times, commands, results, runtimes, hashes and source state; removes its temporary source/consumer directories on success or failure. Artifacts are retained next to the report.

Implemented workers declare an `artifact.qualifier` entry point beneath their own
component root. The checker calls its static `boundary` mode; the restricted
builder calls `restricted --report <path> --artifacts <directory>`. The shared
harness rejects missing or escaping hooks, failed/empty reports, zero executed
cases, and mismatched artifact hashes. Worker-specific asset staging and runtime
qualification stay with that component. Selecting a worker explicitly may load
its model for its declared proof; default library selection is unchanged. A
worker's separate container qualification owns Docker and kernel resource-limit
evidence. `--list` executes no qualifier or setup.
- Restricted builds retain the existing chess-line/scheduling default. Add `--component game-evidence-contracts` for selected contract builds, wheel and sdist installs, versioned public namespaces and packaged schema resources on both Python runtimes. Unrelated component source is absent. This does not replace `contracts/qualify_artifacts.py` and its separate synthetic-patch proof.
- `node tools/component-build/test-lane.mjs --component game-evidence-contracts`: execute the declared owning suite from its explicit repository-relative `cwd`, using a prepared installed-package environment selected by `TEMPO_COMPONENT_PYTHON`. Python cases use JUnit identities; Node cases retain the existing structured reporter.
- Add `--list` to checker, restricted-builder, component tests or an existing lane to list targets without installing, building or running producers/tests. Unknown, empty and planned executable selections fail before setup.
- Shared suites are explicit: `test-lane.mjs --suite contract-exchange` invokes the existing Python/TypeScript exchange with genuine producers and case reporting; `--suite contract-ownership` invokes the separately owned native fixture. Repeated suite selectors execute once. Neither is an implicit per-contract prerequisite. D does not rerun unchanged native proofs.

Contract-only setup from the checkout root (no root web or backend runtime setup):

```sh
uv venv --python 3.12 contracts/.venv
uv pip install --python contracts/.venv/bin/python --require-hashes -r contracts/requirements-test.lock
uv pip install --python contracts/.venv/bin/python --no-deps ./contracts/game-evidence
TEMPO_COMPONENT_PYTHON="$PWD/contracts/.venv/bin/python" node tools/component-build/test-lane.mjs --component game-evidence-contracts
```

For the shared exchange, install the Game Evidence contract distribution noneditably
and run `npm ci --prefix contracts/consumer-examples --workspaces=false`; this is
the standalone consumer dependency tree. The reporting adapter delegates package
and producer selection to `contracts/run_tests.py`; it adds JUnit output and uses
the existing generic Node reporter for compiled TypeScript cases.

Package-local commands remain usable without root test wrappers. Scheduling uses
`uv sync --locked --no-editable`, `uv run --locked --no-editable python -m build`,
and `uv run --locked --no-editable python -m pytest tests` from its root. A noneditable
installation prevents its src layout being an ambient runtime dependency.

The legacy root assembly needs its existing Node dependency tree for frontend
consumer checks. `npm ci` links the declared chess-line workspace; build prerequisites
compile its public exports. Every Vitest component lane runs `build:packages` once
before execution, including direct `node tools/component-build/test-lane.mjs`
invocation; a failed build stops the lane. `npm run dev` and `npm run dev:local`
prepare the initial artifact. Package source edits during a running development
server may require `npm run build:packages`; continuous package watching is not
provided. Package-local `npm test` builds chess-line before testing. `backend/pyproject.toml` declares noneditable scheduling and chess-core
path dependencies; root CI's `backend/requirements-test.txt` installs both distributions.
The backend image copies only the declared scheduling and chess-core packages before installing them.
These adapters do not establish an independently deployable Learning or Study Web.

## Component-owned interface and priority-intake guard

`check-interface-boundaries.mjs` extends the existing TypeScript import parser
and Python AST checker. It reads source only: no application import, database,
producer, worker, engine or hosted CI activation. The integration owner supplies
the **single authoritative** component/path map; this tool creates no second
production ownership registry.

```sh
node tools/component-build/check-interface-boundaries.mjs <owner-map.json>
node --test --test-concurrency=1 tools/component-build/tests/interface-boundaries.test.mjs
```

Before the global owner map is joined, the same call walker also supports
`--intake <source.py> <entry-functions comma-list> <forbidden-call-patterns comma-list> [source-root]`.
This checks the named function and statically resolved helpers in its source
directory, rejects direct SQL and opaque loaders, and permits frozen-field
comparisons that do not invoke dynamic capabilities. The functional Study
selection uses it through `backend/tests/test_study_priority_interface.py`;
this does not create a competing component ownership map.

The optional second checker argument is a disposable source root for negative
controls. `TEMPO_COMPONENT_PYTHON` selects the existing Python interpreter;
the Python source reader uses only the standard library. The regular
`npm run test:components` command already discovers the negative controls.

Each map component supplies `id`, repository-relative `sources`, designated
`interfaces`, `modules` (import-prefix to source path), `dependencies` (component
IDs), and `tables` (owned SQL relation names). A named composition component may
set `composition: true`; only that owner wires private implementations, and
domains cannot import it. Explicitly list source roots for all participating
owners. An interface belongs beneath its owner's source root, has a README and
cannot designate the entire implementation directory as public. Python
interfaces require literal `__all__`; TypeScript interfaces use named exports.

`intakes` optionally identifies an exact `path`, `functions` (for example
`EncounterFeedback.accept_encounter`) and `forbiddenCalls` patterns for broad
eligibility enumeration, preparation, refresh and promotion. The guard follows
statically resolved local/imported helper calls and rejects prohibited capability
references as well as direct calls. Use the receiving owner's actual names;
the guard does not invent a second queue or intake API.

Enforcement covers Python and JavaScript/TypeScript private imports (including
lazy/conditional/type-only imports), unavailable public exports and namespace
member bypass, interface-to-private/infrastructure leaks, known lifecycle/I/O
calls, source symlinks/escapes, direct dependency cycles, and known SQL write
primitives crossing table ownership. Unknown targets at recognized database
execution sites fail closed. An interface cannot expose an implementation by
renaming its private import. Old compatibility modules may forward **to** the new
interface; the new interface must not back-import its compatibility wrapper.

This is not a SQL engine or a whole-program semantic effect proof. Computed
capability dispatch, callbacks supplied at runtime, SQL stored procedures and
unsupported languages require their existing owner/compiler/runtime checks.
Do not claim static success proves bounded admission or failure isolation.
Study's runtime tests must still prove acceptance survives refresh failure,
accepted work is consumed with Games absent, one effect on replay, and protected
attempt safety. The required affected-component suite must include those actual
cases and the existing #491 public-injection/failure/replay/cold-Study tests;
map/suite registration is an integration requirement, not an optional README task.

### Short README convention for each owner's `interfaces/`

Keep the README next to the interface code. In a few explicit paragraphs or a
small table, state:

- Responsibility and state/effect owner; designated module and supported exports.
- Commands and data inputs/outputs; no database handles, queue implementation,
  worker objects or private storage types in the public surface.
- Allowed dependency direction: consumers import the interface; composition
  alone supplies concrete implementations. Study never requires optional Games
  to save, select, consume or refresh accepted work.
- State semantics. For priority intake, **accepted** means durably recorded;
  **pending** has not established queue availability; **available** requires the
  Study owner's actual serving receipt. Pure components explicitly say that
  admission states are not applicable rather than manufacturing them.
- Failure/retry/idempotency: stable logical key, receipt/outcome, original source
  and detection preserved; optional refresh failure cannot undo acceptance.
- Forbidden caller behavior: private imports/calls, cross-owner table writes,
  inline broad queue eligibility/preparation/promotion or availability guesses.
- The exact enforced checker/required test command and its actual limitations.

The checker enforces interface location, explicit exports, README presence and
source rules. A human owner still reviews whether the README and public types
truthfully describe supported behavior; a heading is not runtime evidence.

## Test lanes and identities

`test-inventory.json` classifies every current legacy unit file exactly once as
frontend, genuine Python-producer parity, or legacy subprocess/runner wrapper.
Every Node runner file is listed separately. Missing or duplicate files fail inventory
validation. No existing test is deleted, renamed, or silently removed from the old
Vitest configuration. Integration selections are explicit additional consumer proofs.
Component entries independently declare their local test inputs and optional
`testInventory` document; they need no additional case list in the legacy inventory.
New tooling regressions are owned by [REGRESSIONS.md](REGRESSIONS.md).

- `npm run test:frontend -- [file-substring] [test-name]`
- `npm run test:producer-parity -- [file-substring] [test-name]`
- `npm run test:runners -- [file-substring] [test-name]`
- `npm run test:component-integration -- [file-substring] [test-name]`
- `node tools/component-build/test-lane.mjs controlled-browser [file-substring] [test-name]`

The controlled-browser entry delegates to its owning Node runner/configuration,
with a sanitized environment, one chess-line build and exact selected case accounting.
Its local file discovery is authoritative; the compatibility index must agree.
Browser execution is imported only when that lane runs. Generic lanes also work
in minimal repositories without browser modules. Synchronous inventory validation
allows an absent browser lane only when both its owning directory and registration
are absent; a present lane still requires exact, nonempty ownership.
`controlled-browser --list` reads files only. Production native/visual execution
uses `npm run test:ui-isolated`; see the [supported browser lane](../../tests/controlled-browser/README.md).

These entry points print selected files and executed passing identities and reject
empty file or case selection. Node runners require a successful child exit, a valid
final structured summary, and at least one actual passing case. Suites, TODOs,
skips and synthetic file records never count as passing cases. Counts for failures,
skips and TODOs are separate; passing identities include file, source location,
name, event ordinal and nesting/test number so repeated names remain distinguishable. Producer-parity still consumes legacy Python producers
and their declared environments; separation of discovery is not certification that
those services have been extracted. The integration lane currently runs frontend
adapter proofs; Python adapter/application proofs run separately:

From `backend/`, run `.venv/bin/python -m pytest tests/test_scheduling_package_adapter.py tests/test_scheduling_review_application.py tests/test_scheduling_boundary.py`.

Use an environment containing installed tempo-scheduling, FSRS, pytest, import-linter
and grimp. No PostgreSQL proof is claimed by these pure/seam tests.

Python public APIs are policy-enforced, not a language sandbox: `__all__` defines
exports, consumers may import the package root and declared names, and the
validator rejects private/deferred imports. Python technically permits importing
internal modules; the validation policy rejects that usage. Unresolved dynamic
loaders are prohibited in the selected packages. Local transitive sources are all
checked; installed tests prove the third-party runtime closure with unrelated source
and service infrastructure unavailable.

## Runtime reconciliation and remaining owners

Scheduling and backend metadata target Python >=3.12,<3.15, with FSRS 6.3.2.
The legacy assembly baseline and backend interpreter selector are 3.12, matching
the unchanged production image. The minimal clean seam setup from the checkout
root is `uv sync --project backend --locked --no-editable --python 3.12`.
It installs the local scheduling and chess-core distributions and declared test/boundary tools;
it does not install the full backend runtime stack. `backend/requirements-test.txt`
retains repository-root-relative local paths, as used by existing hosted commands.
Python 3.14 is an additional package/seam qualification runtime, not a full-backend
compatibility claim. The lock was regenerated normally without version upgrades.
Scheduling 0.2.0 requires explicit time, calendar day and serialized card inputs.
The legacy backend adapter owns clock defaults and FSRS's clock-based initial card
identity; fixed-as-of package golden tests supply all inputs explicitly. Chess-core
0.1.0 preserves stable card digests and first-four-field FEN keys with stdlib only. Algorithms and atomic transaction
ownership are unchanged. No artificial WASM dependency was introduced.

Actions and required-check settings are untouched. Local component passes do not
constitute hosted qualification or deploy an artifact. #199 consumes frontend setup,
#200 consumes installed scheduling setup, and #43/#110 can reuse manifest, runtime,
restricted-context and local-discovery conventions without consuming these algorithms.


## Centralized Python package inputs

The Python component checker keeps centralized database directories as registered
build inputs. Their top-level `tests` and `tooling` directories are sdist and
qualification inputs, rather than wheel runtime modules, matching the existing
Content, Game Evidence and Learning wheel hooks. Input path/symlink checks still
apply; shipped adapters, DDL Python modules and nested runtime directories retain
the full import and dynamic-loader audit. Qualification code retains its regular
owning suites instead of being accepted as a runtime dependency.

`tempo-learning-remediation` already ships both `tempo_learning_remediation` and
`tempo_remediation_database`. The latter import root is recognized only when that
exact distribution is declared in both manifest and package metadata. Unknown or
undeclared namespaces remain errors. The regular
`tests/python-package-inputs.test.mjs` owns the packaging/alias regressions and
negative controls; `tests/boundaries.test.mjs` retains the original guards.
This checker correction changes no application API, packaging hook, schema,
provider or runtime lifecycle.

## Pure package owner qualification

`board-ui` declares its package exports, React peer and published asset subpaths;
`evidence-math` declares its root exports and the public
`tempo_evidence_math.position_learning` module. The module declaration checks
existence; it does not yet enforce Python consumer imports. Backend consumer
boundary enforcement belongs to the later backend/storage ownership batch. Their tests select through
`test-lane.mjs --component <id>`. Evidence-math's suite expects its wheel and
locked test dependencies installed in the interpreter selected by
`TEMPO_COMPONENT_PYTHON`.

`restricted-build.mjs --component board-ui --component evidence-math REPORT`
invokes each package's existing isolated qualifier from declared inputs and rejects
missing, failed, skipped, or empty qualification evidence. Set `npm_config_cache`
to a populated task-owned npm cache. For evidence-math provide
`TEMPO_EVIDENCE_BUILD_PYTHON` (with its locked build dependencies),
`TEMPO_EVIDENCE_PYTHON_312`, `TEMPO_EVIDENCE_PYTHON_314`, and
`TEMPO_EVIDENCE_WHEELHOUSE` (all absolute paths). The package qualifier tests both
wheel forms with both interpreters after removing producer sources. These package
proofs do not establish browser acceptance, application assembly, or coverage of
other runtime owners. Ownership reconciliation in this batch covers only tracked
source files in these two packages; composition and backend/storage are later
batches.

## Browser and CLI composition map

`study-web`, `tempo-cli`, and `integration` are **mapped**, not separately
implemented artifacts. Their exact tracked `ownedFiles` record the browser application runtime, the local lifecycle command, and development/qualification tooling.
The CLI depends on the integration owner for shared budget and timing helpers;
that dependency does not transfer their ownership. These mappings expose no new
package, deployment or public service and cannot be selected as restricted builds.

The default checker compares the declared files with the tracked browser source, static entrypoints and assets, root executable configs, `tempo`
launchers, direct scripts and component-build executable tooling. An added tracked
source must be assigned once; duplicate, stale and unowned entries fail. Script test fixtures, backend/storage and remaining package ownership are
outside this finite batch. Static-file assignment is an ownership map, not a
data or asset qualification. This map does not by itself qualify browser behavior,
CLI installation, or complete T02 runtime ownership.

`affected.mjs` remains an independently tested pure planning utility over its
own supplied snapshot format. It does not currently consume this registry or
select production checks, and this batch does not adopt it as an authoritative
planner. Its synthetic ownership rules are test inputs, not a second live map.

## Retained backend seam map

The next finite map records 37 tracked Python files across backend contracts,
Study queue selection/composition and position issuance, their borrowed SQL
adapters, and minimal Game Review. Named `path#symbol` entries are source
navigation points, checked against Python AST without importing modules; they do
not assert a frozen public package API. `mapped` continues to mean no standalone
artifact or executable selection. Each entry records the observed effect boundary
and selected dependencies; notes identify out-of-scope collaborators. The
checker tests exact ownership only within this named set and fails unowned,
overlapping, stale, or nonexistent named entrypoints.

A second explicit selection maps 14 review and command files (including two package initializers): dispatch, gateway,
review command, adapter, scheduling and reconciliation, and their receipt,
review and savepoint storage. The gateway spans multiple domains; dispatch imports
Celery and the review command registers handlers at import. Review policy imports
storage and scheduling, and review storage depends on queue-refresh presentation
and admission. The retained `submit_legacy_review` adapter serves registered review
commands; the `apply_legacy_review` compatibility bypass was removed. These are
source facts, not claims of import purity or a complete dependency closure.

A third selection maps five durable-task and shared-claim files: the task
application, storage package, advisory queue wake, and claim SQL builders. The
application has both owned and borrowed transaction paths and imports private
storage SQL constants. The wake uses Celery lazily; worker execution and periodic
recovery remain outside this selection. Claim builders are also used by background
activity and PGN preparation. Direct production consumers of the task service
include `activity_commands.py`, `queue_commands.py`, `tasks.py`, queue application
and refresh, review adapter, integrity, and opening graph services. The wake is
also consumed by command gateway, activity commands, and tasks. `main.py` reads
task storage and the task service directly. These are observed source edges, not
a complete runtime dependency or isolation proof.

A fourth selection maps two Study calendar files: the explicit instant and zone
conversion policy and the caller-transaction capture adapter. The latter reads
the configured zone through the existing multi-purpose queue storage owner and
fails closed if that setting is absent. Commands and other consumers remain
outside this selected map; this records existing dependencies rather than a new
ownership boundary.

A fifth selection maps seven existing manual prefix split files: HTTP transport,
registered command handling, split application and deferred recovery, and three
borrowed SQL adapters. The adapters operate on cards, queue, review receipts and
the migration 066 intent table. Receipt gateway, queue ordering, integrity,
graph rebuild and the existing recovery beat remain outside this selection.
These are source roles and existing cross-owner edges, not an independent
artifact, pure policy or a new scheduler.

A sixth selection maps the PostgreSQL readiness composition, its package initializer,
and the storage fact reader. The HTTP route calls the composition, which captures
the Study calendar and inspects Celery; the storage reader queries schema, position,
settings, queue and durable-task facts through the supplied connection. These
existing edges and readiness coupling are recorded without a startup or isolation claim.

A seventh selection maps the registered settings command and three SQL adapters.
The command uses the existing transport schema, Study calendar, queue refresh and
post-commit priority policy; the adapters read limits, lock and update global
settings, and read the public projection on caller-owned connections. This records
existing registration and dependencies without changing behavior.

An eighth selection maps the existing teaching-state command and its SQL adapter.
The command registers `cards.teaching.record` and is imported by task composition;
the adapter locks the active card and inserts or replays the timestamp under the
caller transaction. The transport, gateway and composition remain external edges.

A ninth selection maps the existing position-annotation command and SQL adapter.
The command validates FEN and registers `repertoires.annotation.save`; task
composition imports it. The adapter locks a repertoire and deletes or upserts
annotations in the caller transaction. Gateway, transport and task composition
remain external. The inherited legacy command fixture remains unresolved; this
map does not qualify runtime behavior.

A tenth selection maps the existing activity control and manual retry command and
its SQL adapter. Task composition imports the registered commands; background
activity, durable task, queue wake and retired-kind policy remain external edges.
The adapter locks and resets a failed task and records activity and generation
events in the caller transaction. The inherited legacy fixture remains unresolved.

An eleventh selection maps the existing repertoire integrity repair command,
scan and source services, and their SQL and certificate adapters. Task composition,
retained mutations, graph rebuild, queue, durable tasks, Redis admission, transport
and source validation remain existing external edges. SQL stages and publishes
scan facts and card blocks and supports repair under caller-owned transactions.
This source map does not establish runtime isolation or native qualification.
The inherited legacy command fixture remains unresolved.

A twelfth selection maps opening graph construction, bounded PostgreSQL rebuild
slices and native storage. Task composition dispatches rebuilds; authored edits
and integrity repair request generations. Existing durable task fences, Redis
lease, graph publication, queue retirement, card links and integrity blocks remain
visible. This is a source map, not a runtime isolation or native qualification.
The inherited legacy command fixture remains unresolved.

A thirteenth selection maps existing PGN import commands, validation, explicit
delivery filtering, and preparation and import SQL. Receipt, operation and task
generation identity remain with existing owners; graph, integrity, canonical
prefix, durable task, Redis admission and task/main composition are existing
edges. This source map does not establish native or runtime qualification.
The inherited legacy command fixture remains unresolved.

A fourteenth selection maps existing explicit card and repertoire deletion decisions
and their SQL adapter. Main, commands, Study, tactic, review and graph callers,
calendar and observation-summary policy remain visible external edges. Caller-owned
operations include tombstones, retention, task cancellation, queue, graph and
evidence mutations. Mapping does not authorize deletion or establish native proof.
The inherited legacy fixture and three focused PGN failures remain unresolved.

A fifteenth selection maps canonical prefix scope and its storage adapter. The
application checks routes and positions with python-chess; caller-connection SQL
optionally locks repertoire metadata, reads current-revision anchors and guards
position replacement by source revision and route order. Main, PGN, branch, card,
repair, retained mutation and analysis consumers remain external edges. This is a
source map, not a native or runtime qualification. The inherited legacy fixture and
three focused PGN failures remain unresolved.

A sixteenth selection maps repertoire change conflict facts and validation, retained
line/card mutations, and bounded source-lock storage. Existing snapshot/index,
canonical prefix, integrity/card-edit storage, and branch/card/repair command
edges remain visible. The caller owns the transaction and operation receipt; this
source map does not establish native or runtime qualification. The three PGN and
three legacy fixture failures remain unresolved. The retained mutation boundary
test also fails importing the missing `retained` module; it is not runtime proof.

A seventeenth selection maps registered card and branch edit commands and their
SQL adapters. Existing caller transactions handle revisions, replacement, reviews,
queue and membership changes, branch locks and graph/integrity requests. Gateway,
calendar, source validation, canonical prefix, deletion, transport and task
composition remain visible edges. This is a source map, not native proof. The
three PGN, three legacy and one retained mutation fixture failures remain unresolved.

An eighteenth selection maps repertoire control commands, opening limit responses
and their foreground SQL. Main selection uses the existing advisory and stable row
locks; deletion delegates to its owner, and settings updates request queue refresh
within the caller transaction. Task/main composition, transport, calendar and
queue consumers remain external. This is a source map, not native qualification.
The three PGN, three legacy and one retained mutation fixture failures remain open.

A nineteenth selection maps Tactics capture, attempt, activation and catalog
services and SQL. Capture replay binds the original capture ID and request to its
receipt; attempt storage retains its supplied identity. Existing Study admission
and queue placement own scheduling, while Tactics supplies capture and catalog
facts. Assets, calendar, queue locks and task/main composition remain visible
edges. This source map is not native or runtime qualification. The three PGN,
three legacy and one retained mutation fixture failures remain unresolved.

A twentieth selection maps Study queue commands, placement and foreground SQL,
plus the queue-position lock. Issuance uses prepared-head and issued-origin
identity, card/date locks and owner fences; placement retains existing protected
order and promotion checks. Gateway, durable tasks, review, Tactics, split,
availability and task/main composition remain external edges. These are source
roles, not native proof. The three PGN, three legacy and one retained mutation
fixture failures remain unresolved. The focused queue command boundary suite also
has eight unresolved failures: missing `postgres_sql` (one), SQLite rejection of
PostgreSQL placeholders (four), and missing `is_postgres_connection` (three).

A twenty-first selection maps Study ordering policy, receipt preservation, backend
facade, presentation and priority preparation, and presentation SQL. Existing
protected attempts, ownership rechecks, bounded reads and caller transactions remain
visible; ordering does not grant serving eligibility. Frozen-order, review, Redis
defer, queue refresh and main/task/placement are existing edges. This map is not
native qualification. The eight queue, three PGN, three legacy and one retained
mutation fixture failures remain unresolved.

A twenty-second selection maps Study queue application coordination and its
admission, eligibility, publication, repeat and seeding SQL. Prepared source and
queue facts, graph and card/day locks, caller transactions and existing order,
Tactics, frozen-order and durable-task edges remain visible. This is a source map,
not native qualification. The eight queue, three PGN, three legacy and one retained
mutation fixture failures remain unresolved. The queue admission boundary fixture
also fails on missing `resolve_opportunity`; it is not runtime proof.

A twenty-third selection maps the bounded queue refresh worker and its epoch,
reader, eligibility, opening, admission and capacity SQL. Existing task/current-day
and epoch fences, queue locks, frozen ordering, graph/source checks and caller
transactions remain visible. This source map does not establish native proof. The
eight queue command, one queue admission, three PGN, three legacy and one retained
mutation fixture failures remain unresolved. The queue publication fixture also
fails because its SQLite adapter rejects PostgreSQL `FOR SHARE`; it is not native
proof.

A twenty-fourth selection maps Study exercise transport, attempt commands,
retirement guard, review finalization and storage. Existing attempt/card/entry/
cycle/revision, issued-origin and retained presentation bindings, gateway receipt,
grader, calendar, queue lock and scheduling review edges remain visible. This is a
source map, not native qualification. The seventeen previously reported queue,
PGN, legacy and retained-mutation fixture failures remain unresolved. Both Study
attempt guard fixture cases also fail on missing `current_presentation_available`;
they do not establish runtime behavior.

A twenty-fifth selection maps Study grading policy and its assignment-only backend
facade. Existing grader version 1, exercise/answer contracts and python-chess
remain unchanged; attempt and retained presentation consumers remain external.
This source map does not establish runtime or browser qualification. The nineteen
previously reported fixture failures remain unresolved.

A twenty-sixth selection maps supplied-snapshot repertoire conflict computation,
its borrowed-reader facade and line storage. Existing six-field source identity,
python-chess, bounded cache, PGN and main consumers remain visible. This source
map does not establish native or runtime qualification; the nineteen previously
reported fixture failures remain unresolved.

A twenty-seventh selection maps frozen Study ordering policy, borrowed-connection
composition and storage. Existing activation token, rank/fingerprint, exposed-card
protection, queue change/rollback and reader-version edges remain visible. This
source map does not authorize activation or establish native qualification; the
nineteen previously reported fixture failures remain unresolved.

A twenty-eighth selection maps original queue attempt recovery, provenance SQL and
explicit abandonment lifecycle. Existing entry/card/revision/content, receipt,
locked-origin fingerprint and accepted-review/position exclusions remain visible.
This source map does not authorize abandonment or establish native qualification;
the nineteen previously reported fixture failures remain unresolved.

A twenty-ninth selection maps opening evidence policy, checkpoint coordination
and SQL. Existing immutable presentation, original attempt/event identities, issued
position fences, review receipt and caller transaction edges remain visible. The
policy also checks current UTC time; this is a source map, not native proof. The
nineteen previously reported fixture failures remain unresolved.

A thirtieth selection maps position first-response proposal, envelope codec and
receipt storage. Original issued plan, attempt/event/decision identities, digest,
acceptance clock and caller-owned source/queue/owner fences remain visible. Grade
ledger uniqueness and scheduling remain with their existing owner. This is a source
map, not native proof; nineteen previously reported fixture failures remain open.

A thirty-first selection maps position daily credit acceptance, borrowed-transaction
grade effects and SQL. Existing original plan/event, owner epoch, completion receipt,
one-Good-per-day and version-fenced schedule boundaries remain visible; checkpoint
and Learning review are callers. This source map is not native qualification. The
nineteen previously reported fixture failures remain unresolved.

A thirty-second selection maps position Study ports, clean-prefix proof, route
selection, immutable plan contract and sealed preparation. Supplied receipt facts
and the preparation seal are claims, not storage attestations; the issuer still
fences source, admission and schedule witnesses. This source map does not establish
native qualification. The nineteen previously reported fixture failures remain open.

A thirty-third selection maps bounded repertoire snapshot and position source
preparation, source change policy and owner epoch fences. Original source digest,
revision, admission identity and borrowed-writer checks remain visible; prepared
claims do not authorize settlement or activate an owner. This source map is not
native proof. Nineteen previously reported fixture failures remain unresolved.

A thirty-fourth selection maps the Study captured-tactic admission interface,
owner and borrowed storage. Study validates card identity and owns admission and
queue placement; the Tactics capture owner retains the original receipt. Existing
daily and queue locks and caller transaction remain visible. This source map is
not native proof. Nineteen previously reported fixture failures remain unresolved.

A thirty-fifth selection maps backend chess identity, opening segment and PGN
helpers. The existing card ID facade imports chess-core; python-chess and graph,
evidence, Tactics, Study and authoring consumers remain visible. This source map
is not a standalone package or runtime qualification. Nineteen previously reported
fixture failures remain unresolved.

A thirty-sixth selection maps Study scheduling review contracts, policy, maturity,
legacy adapters and borrowed SQL store. Existing scheduling package, FSRS defaults,
source identity, caller transaction and review receipt owners remain visible. This
source map does not establish native proof. Nineteen previously reported fixture
failures remain unresolved. Ten scheduling store fixture cases also error on the
missing `real_game_feedback_adapter`. The initial interpreter lacked `grimp` and
installed scheduling/chess-core metadata; a task-owned locked, non-editable Python
environment subsequently passed 49 scheduling boundary and package metadata cases.

A thirty-seventh selection maps supplied repertoire snapshot projection, position
index and scheduling occurrence interface. Existing source identities, chess and
contract dependencies remain visible. Prepared facts and physical bindings do not
grant writer authority or issuance; consumers must retain source fences. The
nineteen earlier fixture failures and ten missing-adapter errors remain unresolved.

A thirty-eighth selection maps background activity progress/control coordination and
SQL. Existing source/generation checks, Study-only admission, fixed-source row
locks and caller transaction remain visible; advisory progress emission catches
operational failure. This source map does not establish native or runtime proof.
The nineteen earlier fixture failures and ten missing-adapter errors remain open.
The selected PGN activity-scope fixture reports 24 failures (18 incompatible claim
signatures, six missing storage attributes); a separate environment lacked sqlglot
and could not collect it. These observations do not establish runtime behavior.

A thirty-ninth selection maps bounded background metric labels, diagnostic types,
aggregation and transaction-fenced storage. Existing runtime snapshot and fixed
bucket/shard dependencies remain visible; this is not runtime or native proof. The
24 PGN activity-scope, nineteen earlier and ten missing-adapter fixture gaps remain
unresolved. The diagnostics suite also has two unresolved label/runbook snapshot
failures; these source-test results do not establish runtime behavior.

A fortieth selection maps Redis admission, policy, coalesced wake and runtime
instrumentation owners. Existing token, expiration, generation and advisory wake
boundaries remain visible; durable tasks remain authoritative. This source map
does not establish runtime or native proof. The two diagnostics, 24 PGN scope,
nineteen earlier and ten missing-adapter fixture gaps remain unresolved.

A forty-first selection maps process activity gating, Study-only policy and the
generation-fenced expiring control snapshot. Database settings remain authoritative;
unavailable control holds optional admission while existing essential exceptions
remain in task composition. This source map is not runtime proof; the two diagnostics,
24 PGN scope, nineteen earlier and ten missing-adapter fixture gaps remain open.

A forty-second selection maps bounded repertoire move verification and its
read-only source adapter. Existing legal replay, cursor, source-token recheck and
main endpoint remain visible; this source map is not native or runtime proof. The
previously reported fixture gaps remain unresolved.

A forty-third selection maps current analysis-paste preview, commit command and
line storage. Existing preview token/currentness, command identity and graph/
integrity edges remain visible; retired Coverage inputs are rejected and no gap
state is written. This source map is not runtime proof; previous fixture gaps
remain unresolved.

A forty-fourth selection maps HTTP composition storage for availability, retained
position holds, queue projections, repertoire state, practice, line authoring and
system facts. Existing caller-owned transactions include queue and repertoire
writes; the separately mapped settings adapter retains its owner. Queue consumers
include main and Study composition as well as refresh and command storage. This is
a source map, not runtime or native qualification; previous fixture gaps remain open.

A forty-fifth selection maps existing Study issuance provenance and position
due/state and introduction storage. Original issued entry/card/revision and
receipt binding, bounded owner-epoch/source-digest reads, namespace and seed
fences and the borrowed transaction remain with their existing owners. Due facts
do not grant issuance or establish live source currentness. This source map is
not native or runtime qualification; previous fixture gaps remain open.

A forty-sixth selection maps the existing PostgreSQL pools and connection scopes,
storage admission binding and application composition. Main and task composition
bind the existing gate and measurement; the pool module registers shutdown at
import. This source map is not a runtime, startup or isolation qualification;
previous fixture gaps remain open.

A forty-seventh selection maps the existing practiced-opening evidence predicate
and legacy child unlock storage. Its caller transaction preserves the published
graph exclusion and existing non-opening maturity condition. This source map is
not native or runtime qualification; previous fixture gaps remain open.

A forty-eighth selection maps existing puzzle parsing, packaged deck reads and
python-chess move validation used by tactics catalog, queue refresh and main. It
does not confer scheduling authority. This source map is not runtime or native
qualification; previous fixture gaps remain open.

A forty-ninth selection maps the caller-transaction daily queue readiness
projection consumed by task composition, including membership hash and queued
entry presence. It does not establish worker or runtime behavior. Previous fixture
gaps remain open.

The queue ordering owner also maps the existing legacy bridge workspace and miss
reason constants consumed by its facade. This records identity only and does not
revive an optional producer or establish runtime behavior. Previous gaps remain open.

A fiftieth selection maps supplied-input frozen opening math and bounded capacity
frontier proposals, their current composition edge and unmapped admission/main
consumers. This source map does not decide the disputed capacity behavior or
qualify runtime; previous fixture gaps remain open. The existing lazy cycle
between position queue admission and next-card preparation remains unresolved.

A fifty-first selection maps existing opening admission planning and borrowed
storage/capacity adapters, including current frozen and Study-only fences. This
source map does not decide the disputed capacity behavior or qualify runtime;
previous fixture gaps and the position-admission lazy cycle remain open.

Three existing namespace initializers are assigned to their sole mapped local
review bridge, prefix mutation and Study capture-interface owners. The interfaces
initializer retains its empty `__all__`; namespace assignment does not allocate
future siblings or alter import behavior. Previous gaps remain open.

A fifty-second selection records API, worker and Celery composition. Python
imports remain dependency edges; Celery task loading is a separate typed lifecycle
relationship and is not asserted acyclic. Witnesses check beat target names, not
their intervals, and exact task queue mappings. Static witnesses do not prove startup,
recovery or delivery. Existing fixture gaps and the position admission cycle remain
open.

At this base revision, 20 of 289 tracked Python files in those trees remain
outside the combined 269-file selections. The registry alone assigns owners;
independent bounded path predicates discover new tracked inputs within each
selected scope. Removing an ownership entry therefore fails the checker. The T02 delivery owner should select and inspect the next
coherent owners before expanding the map; exact-file selection does not discover
new siblings automatically.
Known existing edges remain visible rather than being called pure isolation:
`study/queue_placement.py` imports the private service helper
`queue_ordering._priority_queue_order`, `study/policy/review_reconciliation.py`
imports storage and scheduling services, and `tempo_storage/commands/queue.py`
imports the private queue-refresh projection SQL constant. These edges are not resolved by
this map and this change neither authorizes nor performs their extraction. The
current next-exercise main branch still has its existing untyped callbacks;
unmerged B12/B13 are not represented as released. Native, browser, package and
assembled behavior are not established by these source checks.

## DDL resource source census

The database schema source owner records each tracked SQL and Python input in the
DDL tree by its forward-history, recent-forward, checksum-history, maintenance
rollback, fixture SQL, source-readiness or namespace role. Database migration
tooling owns the existing explicit operator and read-only source inventory. The
050 wrapper binds a retained checksum resource; 049 rollback is separately
classified. No current direct source consumer was established for
`study_conflicts.sql` beyond its fixture documentation. Source readiness 66 is
not an installed-state observation. This census executes no SQL and does not
qualify migration, rollback, recovery or native behavior; other executable-role
inventories and existing fixture gaps remain open.

## Historical reduction inspection tools

The three existing `tools/reduction` scripts are source-inspection tooling: they
read Git revisions or parse current source and emit audit findings. Their historical
classifications do not authorize product deletions and are not a production planner.
The source guard inventories and parses them without execution. The selection of
`tools/component-build/tests/test_source_delta.py` and
`scripts/tests/tempo-retained-history-authority.test.mjs` remains unconfirmed;
this mapping does not establish either as passing. Other executable-role inventories
and previously recorded fixture gaps remain open.

The contract-tooling source census assigns twelve tracked executable Python and
TypeScript files outside implemented package roots to tooling, tests, or the
independent TypeScript consumer. The existing contract-exchange suite remains
unchanged. This is source-role inventory and syntax inspection only; it does not
execute tooling or establish artifact, runtime, native, or whole-repository coverage.

## Fixed database CLI operation sources

The database CLI backup and probe adapters are mapped separately with their
existing exports. Backup and restore calls perform effects through caller-supplied
Compose; probes read the migration ledger or ping Redis. The caller retains target
selection, lifecycle ordering and cleanup. This source census neither performs
those operations nor qualifies backup, native, runtime or installed behavior. Other
database tooling and previously recorded fixture/test-selection gaps remain open.

## PostgreSQL backup verification source roles

The ordered fingerprint helper and backup verifier are separately mapped, with
their existing caller-supplied database and CLI lifecycle dependencies. Their
functions query databases when invoked; this static census parses source only.
It does not run or qualify backup, restore, native or installed behavior. The
CLI-state verifier and other database tooling remain separately unclassified, as
do the previously recorded fixture and test-selection gaps.

## Managed migration witness source roles

The CLI-state verifier and managed-isolation helper retain consequential historical
cut and private-witness capabilities as well as read-only checks. Existing callers
own target selection, exclusive writer/DDL and Docker/admission fences, transaction
and recovery. This source census parses only; it does not execute or qualify a cut,
backup, native, runtime or installed behavior. Other database tooling and existing
fixture and test-selection gaps remain open.
