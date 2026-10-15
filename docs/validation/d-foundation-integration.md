# Agent D foundation integration — October 15, 2026

Refs #263 and #201; coordination #251; contract definitions #264.
Starting main: `6b99085f8978346a959b5d4a6ea44b6e56f05dd6`.

| Boundary | Before this increment | Delivered registration | Production adoption |
| --- | --- | --- | --- |
| Five contract distributions | Merged #271, manifest planned | Implemented catalog-backed 1.0.0 / wire v1 artifacts | Not asserted |
| Chess-line and scheduling | Implemented #270 artifacts | Existing interfaces plus scoped validation/build/test selection | Existing legacy adapters only |
| Eight app/service/worker roots | Planned extraction targets | Remain planned on this baseline | Not asserted |
| Backend interpreter metadata | >=3.14 / selector 3.14 versus image 3.12 | >=3.12,<3.15; selector/baseline 3.12; normally regenerated lock | Image and running installation unchanged |

The contract catalog and each package's metadata remain authoritative. The root
adapter invokes the existing contract AST checker, checks public module paths and
artifact inclusion, and verifies installed namespaces/resources. It does not copy
wire validation or reference semantics. Analysis jobs retains its public worker
modules. Shared compatibility and native ownership suites have one registration
each and explicit working directories; native ownership remains opt-in.

Selectors are repeatable `--component <id>`; `--list` is collection only.
Existing default checker and legacy lane commands remain usable; the restricted
builder retains its two-package default. Current-source consumer checks remain
separate from explicitly selected package independence. See the executable
commands and registration examples in [component tooling](../../tools/component-build/README.md).

The backend lock retains all eighteen package identities/versions, including
scheduling 0.1.0 and pinned boundary tools. Python 3.12 parses the 191 existing
backend modules, but package/seam qualification is not full-backend compatibility.
C owns subsequent scheduling/chess-core releases and their exact registration;
B owns Learning application integration; A owns the supported browser lane.

## Acceptance and remaining owners

- #263: consumes the five merged artifacts and extends existing restricted contexts
  and negative import/export proofs. Complete artifact/build boundaries for future
  apps, services, workers, CLI and integration remain their extraction owners.
- #201: reconciles backend metadata and minimal setup, provides scoped collection
  and execution with local inventory registration, and preserves legacy discovery.
  Per-component environments for unimplemented roots remain later extraction work.
- #264: the contract-definition deliverable is satisfied by merged #271's scoped
  packages, semantics, producer/consumer matrix and native authorization fixture
  evidence. Production adoption is a separate implementation responsibility under
  #224/#193/#265/#41/#109/#43/#110 and related reconstruction work. This integration
  does not claim a fresh semantic audit or a fresh PostgreSQL result.

No migrations, production image changes, deployment, live-resource manipulation,
Actions activation or merge are part of this increment. Existing #271 native
ownership evidence is historical; reuse requires verifying the fixture/test and
requirements inputs against its preserved source binding.

## Source-bound validation

Clean tested source: **`ccc83cb85649bfd0f5bf23d6d20938ee9dceb0a6`**.
The subsequent handoff commit changes this document only. All relevant source,
dependencies, fixtures, commands and toolchain inputs remain those tested here.

Observed Node 26.10.0, CPython 3.12.12 and 3.14.8. Commands below run from the
checkout root unless stated otherwise; `CP` means the prepared installed-contract
environment's `contracts/.venv/bin/python`. `TEMPO_COMPONENT_PYTHON` selects that
interpreter for contract execution and python3.12 for stdlib validation.

| Command | Actual result | Wall seconds |
| --- | --- | ---: |
| `node tools/component-build/test-node.mjs tools/component-build/tests` | 71 passed, including 54 preserved protections and 17 integration regressions; zero failures/skips/TODOs | 11.279 |
| `node tools/component-build/check-components.mjs` | Seven implemented package boundaries plus current-source legacy consumers pass | 1.818 |
| `TEMPO_COMPONENT_PYTHON=/python-must-not-run node tools/component-build/test-lane.mjs --component chess-line` | Four real installed-package owner cases; no Python invocation | 1.466 |
| `node tools/component-build/test-lane.mjs --component learning-contracts` | 116 Python cases, explicit package cwd, JUnit identities | 1.496 |
| `node tools/component-build/test-lane.mjs --suite contract-exchange` | 529 Python + 336 TypeScript = 865 actual cases; zero skips/failures | 8.424 |
| `CP contracts/export_schemas.py --check` / `CP contracts/check_dependencies.py` | 28 authoritative exports / all five dependency boundaries pass | 0.186 / 0.037 |
| `CP contracts/qualify_artifacts.py --python 3.12 --report <outside-checkout-report>` | Five restricted wheel/sdist builds; 470 installed tests; existing unchanged-wire synthetic patch proof | 7.161 |
| Same existing qualifier with `--python 3.14` | 470 installed tests and the same bounded proof | 7.465 |
| `node tools/component-build/restricted-build.mjs <outside-checkout-report>` | Existing default: four frontend cases; eight scheduling cases plus eight guarded cases on each Python runtime; zero-selection rejection preserved | 7.874 |
| Same restricted builder with `--component analysis-jobs-contracts` | Wheel and sdist installs each execute 92 cases on each runtime; public v1/Stockfish/Maia namespaces and all seven packaged schemas verified | 5.262 |
| `uv sync --project backend --locked --no-editable --python 3.12` / `3.14`, using fresh task-owned `UV_PROJECT_ENVIRONMENT` paths | Minimal declared dependencies installed; isolated imports prove scheduling is in site-packages; environments removed | 0.049 / 0.043 setup |
| From `backend/`: selected environment's `python -m pytest tests/test_scheduling_package_adapter.py tests/test_scheduling_review_application.py tests/test_scheduling_boundary.py -q --junitxml=<report>` | 31 passed on each runtime, zero skips | 2.994 / 3.538 |
| `npm run typecheck` | Pass, including its existing package build prerequisite | 8.953 |
| `npm run test:frontend -- domain-boundary-regressions` | Seven actual consumer cases | 2.938 |
| `npm run test:runners -- merge-conflict-guard` | Five real subprocess cases | 0.377 |
| `TEMPO_PYTHON=<installed-backend-environment> npm run test:producer-parity -- scheduling-producer` | One genuine Python producer/TypeScript consumer case | 1.410 |
| Focused ESLint; pinned Ruff check/format; `uv lock --project backend --check --offline`; conflict and whitespace checks | All pass; no dependency-version changes | Recorded separately |

Stage and collection/setup times are retained separately and are not summed as
elapsed time. These are warm-cache local measurements with other work active,
not a cold-setup budget or speedup claim. Restricted reports retain artifact hashes,
actual commands, runtime identities and every executed case identity. The shared
exchange adapter preserves producer execution and uses the generic Node reporter;
Python summaries are not counted as TypeScript execution.

The initial regression reproduction recorded 12 failures before dispatch changes.
Later fixture-only corrections normalized macOS temporary paths and resolved the
selected package's declared Node dependency after workspace hoisting. Those failed
exploratory runs are retained and are not passing evidence.

Published #271's 32-case native ownership result is reused only for its unchanged
scope: SHA-256 comparisons verify all five fixture/runner/test/requirements inputs
against `3940831d`. The comparison record is preserved; no new PostgreSQL execution
or production isolation is claimed. No Docker image, browser, model, Redis,
deployment, full-product or full-backend suite ran.

Actions was read back as disabled (`enabled:false`). Main's branch endpoint
reported no branch protection/required status contexts at observation time;
no broader ruleset conclusion or merge authorization is inferred. Independent PR
review remains pending. The unchanged root Node lock's install reported 23
dependency advisories; upgrading those unrelated dependencies is outside this PR.

Raw command records, complete identities, qualification JSON, artifact hashes,
reproduction logs and native-input comparisons are preserved outside the disposable
checkout under `test-results/d-foundation-integration-20261015` in the primary
repository. The summary binds all 24 captured checks to the clean tested revision.
