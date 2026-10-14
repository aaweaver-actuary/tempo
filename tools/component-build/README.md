# Component build and discovery tools

These are small validation entry points, not a dependency solver or replacement CI planner.
`components.json` declares implemented artifacts and planned targets;
`components.schema.json` describes its fields. Planned roots and contract suites are
not required to exist or execute. Contract definitions/catalog belong to #264.

## Setup and commands

From the repository root:

- `npm ci --prefix tools/component-build --workspaces=false`: TypeScript parser only.
- `npm ci --prefix packages/chess-line --workspaces=false` then `npm run build --prefix packages/chess-line`.
- `node tools/component-build/check-components.mjs`: manifest, package graphs, public APIs and current consumers. Uses Python stdlib only; `TEMPO_COMPONENT_PYTHON` selects its interpreter.
- `node tools/component-build/test-node.mjs tools/component-build/tests`: deliberate negative boundary and discovery tests.
- `node tools/component-build/restricted-build.mjs [report-path]`: allowlisted builds, npm tarball / Python wheel and sdist, installed tests in clean consumers, runtime guards, dependency inventories, zero-selection probes. Requires Node, npm, uv and available Python 3.12/3.14; creates no Docker/service/model/Rust environment. Reports stage times, commands, results, runtimes, hashes and source state; removes its temporary source/consumer directories on success or failure. Artifacts are retained next to the report.

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

`PYTHONPATH=backend python -m pytest backend/tests/test_scheduling_package_adapter.py backend/tests/test_scheduling_review_application.py backend/tests/test_scheduling_boundary.py`

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

Scheduling targets Python >=3.12,<3.15, qualified at 3.12 and 3.14, with FSRS 6.3.2.
The legacy backend still declares >=3.14 and its images use 3.12. Both can consume
this package; that larger metadata/runtime reconciliation remains #201/#200 work.
The package retains clock defaults and FSRS's clock-based initial card identity;
fixed-as-of golden tests supply both a review time and an explicit serialized card.
#200 owns removing those compatibility exceptions. Algorithms and atomic transaction
ownership are unchanged. No artificial WASM dependency was introduced.

Actions and required-check settings are untouched. Local component passes do not
constitute hosted qualification or deploy an artifact. #199 consumes frontend setup,
#200 consumes installed scheduling setup, and #43/#110 can reuse manifest, runtime,
restricted-context and local-discovery conventions without consuming these algorithms.
