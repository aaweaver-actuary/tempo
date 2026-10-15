# Component build and discovery tools

These are small validation entry points, not a dependency solver or replacement CI planner.
`components.json` declares implemented artifacts and planned targets;
`components.schema.json` describes its fields. Planned roots are not executable.
The five merged contracts are implemented artifacts; `catalog` references their
existing contract catalog, which remains the authority for models, wire versions,
schemas and owners. Registration does not establish production adoption.

## Setup and commands

From the repository root:

- `npm ci --prefix tools/component-build --workspaces=false`: TypeScript parser only.
- `npm ci --prefix packages/chess-line --workspaces=false` then `npm run build --prefix packages/chess-line`.
- `node tools/component-build/check-components.mjs`: manifest, package graphs, public APIs and current consumers. Uses Python stdlib only; `TEMPO_COMPONENT_PYTHON` selects its interpreter.
- `node tools/component-build/check-components.mjs --component learning-contracts`: only that implemented owner and declared component dependencies; no application source is needed. Repeat `--component` to select more owners. Add `--consumers` to request legacy current-source consumer checks explicitly.
- `node tools/component-build/test-node.mjs tools/component-build/tests`: deliberate negative boundary and discovery tests.
- `node tools/component-build/restricted-build.mjs [report-path]`: allowlisted builds, npm tarball / Python wheel and sdist, installed tests in clean consumers, runtime guards, dependency inventories, zero-selection probes. Requires Node, npm, uv and available Python 3.12/3.14; creates no Docker/service/model/Rust environment. Reports stage times, commands, results, runtimes, hashes and source state; removes its temporary source/consumer directories on success or failure. Artifacts are retained next to the report.
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
provided. Package-local `npm test` builds chess-line before testing. `backend/pyproject.toml` declares a noneditable scheduling
path dependency; root CI's `backend/requirements-test.txt` installs that distribution.
The backend image copies only the declared scheduling package before installing it.
These adapters do not establish an independently deployable Learning or Study Web.

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
It installs the local scheduling distribution and declared test/boundary tools;
it does not install the full backend runtime stack. `backend/requirements-test.txt`
retains repository-root-relative local paths, as used by existing hosted commands.
Python 3.14 is an additional package/seam qualification runtime, not a full-backend
compatibility claim. The lock was regenerated normally without version upgrades.
The package retains clock defaults and FSRS's clock-based initial card identity;
fixed-as-of golden tests supply both a review time and an explicit serialized card.
#200 owns removing those compatibility exceptions. Algorithms and atomic transaction
ownership are unchanged. No artificial WASM dependency was introduced.

Actions and required-check settings are untouched. Local component passes do not
constitute hosted qualification or deploy an artifact. #199 consumes frontend setup,
#200 consumes installed scheduling setup, and #43/#110 can reuse manifest, runtime,
restricted-context and local-discovery conventions without consuming these algorithms.
