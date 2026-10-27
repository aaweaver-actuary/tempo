# Component build and discovery tools

These are small validation entry points, not a dependency solver or replacement CI planner.
`components.json` declares implemented artifacts and planned targets;
`components.schema.json` describes its fields. Planned roots are not executable.
The five merged contracts are implemented artifacts; `catalog` references their
existing contract catalog, which remains the authority for models, wire versions,
schemas and owners. Registration does not establish production adoption.

For implementation discovery, also inspect current package READMEs, public APIs
and consumers using the [discovery guide](../../docs/architecture/component-reconstruction.md#discover-before-building).
For example, `packages/board-ui` is a merged standalone package although its
shared registry target remains planned; use its package-local instructions until
registration is reconciled. Do not treat that status as permission to duplicate
the implementation, or change it merely to make an executable selection pass.

## Setup and commands

From the repository root:

- `npm ci --prefix tools/component-build --workspaces=false`: TypeScript parser only.
- `npm ci --prefix packages/chess-line --workspaces=false` then `npm run build --prefix packages/chess-line`.
- `node tools/component-build/check-components.mjs`: manifest, package graphs, public APIs and current consumers. Uses Python stdlib only; `TEMPO_COMPONENT_PYTHON` selects its interpreter.
- `node tools/component-build/check-components.mjs --component learning-contracts`: only that implemented owner and declared component dependencies; no application source is needed. Repeat `--component` to select more owners. Add `--consumers` to request legacy current-source consumer checks explicitly.
- `node tools/component-build/test-node.mjs tools/component-build/tests`: deliberate negative boundary and discovery tests.
- `node tools/component-build/test-node.mjs tools/component-build/tests 'Graph preparation'`: named graph-transfer/fresh-Learning and launcher import guards, negative fixtures and legitimate public/injected composition. These preparation files are outside registered artifact source closures. No historical migration or old obligation preservation is required by this check.
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
- Restricted builds retain the existing chess-line/scheduling default. Add `--component analysis-jobs-contracts` for selected contract builds, wheel and sdist installs, versioned public namespaces and packaged schema resources on both Python runtimes. Unrelated component source is absent. This does not replace `contracts/qualify_artifacts.py` and its separate five-package synthetic-patch proof.
- `node tools/component-build/test-lane.mjs --component learning-contracts`: execute the declared owning suite from its explicit repository-relative `cwd`, using a prepared installed-package environment selected by `TEMPO_COMPONENT_PYTHON`. Python cases use JUnit identities; Node cases retain the existing structured reporter.
- Add `--list` to checker, restricted-builder, component tests or an existing lane to list targets without installing, building or running producers/tests. Unknown, empty and planned executable selections fail before setup.
- Shared suites are explicit: `test-lane.mjs --suite contract-exchange` invokes the existing Python/TypeScript exchange with genuine producers and case reporting; `--suite contract-ownership` invokes the separately owned native fixture. Repeated suite selectors execute once. Neither is an implicit per-contract prerequisite. D does not rerun unchanged native proofs.

Contract-only setup from the checkout root (no root web or backend runtime setup):

```sh
uv venv --python 3.12 contracts/.venv
uv pip install --python contracts/.venv/bin/python --require-hashes -r contracts/requirements-test.lock
uv pip install --python contracts/.venv/bin/python --no-deps ./contracts/learning
TEMPO_COMPONENT_PYTHON="$PWD/contracts/.venv/bin/python" node tools/component-build/test-lane.mjs --component learning-contracts
```

For the shared exchange, install all five local contract distributions noneditably
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
exports, consumers may import only the package root and declared names, and the
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
