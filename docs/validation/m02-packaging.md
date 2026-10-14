# M02 packaging handoff — October 14, 2026

## Implementation and reusable boundary

First reusable commit: `565df18089468055437a9554cc776a246ae4c0fa` for #199, #200,
#43 and #110. It contains the two real packages, restricted contexts, manifest,
public adapters and discovery tooling. Later commits declare additional planned
library owners and record this handoff; they do not modify product algorithms.

`components.json` / `components.schema.json` distinguish implemented libraries
from planned apps/services/workers/contracts/libraries/CLI/integration targets.
The eight product roots have explicit planned status. #264 owns the future
`contracts/README.md` and five scoped contract artifacts; no competing definitions
or empty contract root was introduced here. The manifest may reference their
planned suites without requiring unmerged source to exist.

- `packages/chess-line`: `@tempo/chess-line@0.1.0`, ESM/declarations; public
  `canonicalFenKey`, `canonicalizeMoves`, `sanForUci`, and `LineDiagnostic` type.
- `packages/scheduling`: `tempo-scheduling==0.1.0`, wheel/sdist; public
  `Schedule`, `schedule_review`, `unlock_ready` from `tempo_scheduling`.
- Application adapters retain branding and scheduling public object identity.
  The scheduler implementation is byte-identical to main d5f9ef31; both fixed-time
  golden outputs were independently compared against that main implementation.
- Setup/build/test commands, ownership mappings and limitations are in
  [component tooling](../../tools/component-build/README.md) and the two package
  regression inventories. Existing legacy identities remain discoverable.

## Executed evidence

Environment: macOS arm64, Node 26.10.0, npm 11.19.1, TypeScript 5.9.3,
uv 0.12.22, CPython 3.12.12 and 3.14.8, FSRS 6.3.2. Root Node minimum remains
22.13.0. Supported scheduling Python range is >=3.12,<3.15, qualified at both ends.
No database, browser, Rust or model service participated in the restricted proof.

Clean executable revision `565df18089468055437a9554cc776a246ae4c0fa`:

| Command / scope | Result | Observed time |
| --- | --- | --- |
| `node tools/component-build/check-components.mjs` | Manifest, package graphs and actual consumer imports pass | 1.29 s wall |
| `node tools/component-build/restricted-build.mjs` | npm tarball, wheel, sdist and installed consumers pass; contexts removed | 5.27 s wall, warm caches |
| `npm run test:unit -- tests/unit/component-packaging-regressions.test.ts` | 1 wrapper passes, invoking all 20 owning boundary/discovery cases | 2.17 s wall / 1.17 s Vitest |
| Focused ESLint on new tools/package/tests/config | Pass, zero warnings | 1.15 s wall |
| `npm run check:conflicts` | Pass | 0.44 s wall |
| `git diff --check` | Pass | 0.01 s wall |

Restricted stages, measured separately (not added again to wall time):
frontend setup 0.446 s, build 0.531 s, pack 0.208 s, artifact install 0.401 s,
4 installed tests 0.226 s. Python build setup 0.383 s and build 0.411 s.
3.12 collection 0.296 s and execution 0.100 s; 3.14 collection 0.315 s and
execution 0.134 s. Both execute all 8 package cases; guarded runs also execute
all 8 while rejecting infrastructure imports, sockets/binds and SQLite connections.
Python package source is removed before consumer execution. These are cached local
measurements, not cold-setup budgets or a comparative speedup claim.

Zero-selected Node cases exit 1; Python cases exit 5. Node's synthetic file-level
pass is explicitly excluded from actual case counting. Direct, lazy, conditional,
transitive, private/export, undeclared dependency, source escape and aliased dynamic
loader negative cases are named in the owning tool suite. Python privacy is a
validated import policy, not an assertion that Python itself forbids internal imports.

Earlier focused working-tree evidence, with relevant executable inputs unchanged:

- `PYTHONPATH=backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/tempo-m02-seam-venv/bin/python -m pytest backend/tests/test_scheduling_boundary.py backend/tests/test_scheduling_review_application.py backend/tests/test_scheduling_package_adapter.py -q`: **31 passed**, 2.65 s pytest. Minimal installed-package + pytest/import-linter/grimp environment; no backend runtime stack installed.
- `npm run test:unit -- tests/unit/component-packaging-regressions.test.ts tests/unit/training-builder-route-regressions.test.ts tests/unit/study-position-store-regressions.test.ts tests/unit/study-position-index-regressions.test.tsx tests/unit/repertoire-alternative-index-regressions.test.ts tests/unit/repertoire-alternative-home-regressions.test.tsx`: **46 passed**, 2.64 s Vitest. The packaging wrapper was subsequently revalidated with the final 20 owning cases.
- `npm run test:frontend -- domain-boundary-regressions`: **7 passed**, exact identities reported.
- `TEMPO_PYTHON=/tmp/tempo-m02-seam-venv/bin/python npm run test:producer-parity -- scheduling-producer`: **1 passed**, genuine isolated installed Python producer / serialized golden comparison.
- `npm run test:runners -- merge-conflict-guard`: **5 passed**, 0.119 s Node runner, real subprocess semantics retained.
- `npm run test:component-integration -- domain-boundary-regressions`: **7 passed**.
- `npm run typecheck`: pass. `npm run lint`: zero errors, 10 existing warnings.
- `npm run build:local`: pass, 2.02 s Vite build; existing asset/chunk warnings remain. No Rust/WASM rebuild was invoked.

The lane inventory accounts for all **166 legacy unit files** and **7 Node runner
files**, plus explicit consumer integration selections. Independent package tests
are additional owning suites. No legacy tests were deleted, renamed or disabled.

Artifact SHA-256 from the clean restricted proof:

| Artifact | SHA-256 |
| --- | --- |
| `tempo-chess-line-0.1.0.tgz` | `cc0d9fae85640131f2cd1a4bd9b253a6ff2707ce90cc343d952e42cbda214d35` |
| `tempo_scheduling-0.1.0-py3-none-any.whl` | `4bd5a0bdff62bab34b9e0b339107ac712570682587a854d9959d2bd6b312ae0d` |
| `tempo_scheduling-0.1.0.tar.gz` | `b999220914f029e91479aaf77d657f2680054b024b89889fdc9bccbc85475ee8` |

Raw restricted reports include commands, lockfile hashes, actual runtimes, installed
dependency lists and collected/executed identities. Local evidence/artifacts are
preserved outside the task checkout under `test-results/m02-packaging-20261014`.
Generated Python distributions may have build-time-dependent archive hashes;
these hashes identify these observed builds rather than promise reproducible bytes.

## Limits and qualification

Actions was read back as disabled; no hosted runs or passing required checks are
claimed. No Actions activation, branch protection change, merge or deployment
occurred. Docker image assembly changes only declare the new package inputs;
images and database/browser/model/full suites were not executed. Intermediate
whole-product assemblies are not certified by package proofs.

The legacy backend >=3.14 declaration / Python 3.12 image mismatch remains a
larger metadata/runtime reconciliation owner under #201/#200. This extracted
package works on both runtimes. Clock defaults and FSRS's generated initial ID
remain compatible exceptions for #200; deterministic callers provide explicit
review time/day and initial card identity. No review transaction, migration or
persistence ownership changed.

#263 and #201 remain open for programme-level completion and unimplemented
component environments/boundaries. #251 and #192 remain open. #264 is independent.
The deliverable is implementation-complete packaging foundations, **not a
merge-qualified PR**. Hosted qualification and subsequent component extractions
remain separate work; unrelated global CI repair is outside this PR.
