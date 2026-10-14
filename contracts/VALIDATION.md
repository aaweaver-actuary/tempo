# M02-B delivery evidence — October 14, 2026

## Historical foundation evidence (3940831d)

## Scope and source binding

This change adds contract artifacts and reference consumers only. It changes no production routes, transaction implementations, workers, migrations, live roles/data, hosted workflows, Actions settings or protection. `tests/REGRESSIONS.md` gains a compatibility link to the owning registry. Root manifests, workspace files and toolchains remain with #263/#201.

Tests were executed on base `d5f9ef31f5123b31ce0bc5d54b5f288a79829db5` **plus the uncommitted contract implementation subsequently committed on this PR branch**, macOS arm64. They are not attributed to clean base HEAD. Restricted-build reports record that dirty state, exact runtime identities, every collected case, stage outputs/timings and wheel/sdist SHA-256 hashes. Documentation-only handoff edits followed the final runtime checks; no runtime inputs changed afterward.

## Executed proof

| Command from checkout root | Result | Observed time |
|---|---|---|
| `contracts/.venv/bin/python contracts/run_tests.py --typescript` | 287 Python cases: Learning 79, Content 62, Game Evidence 27, Intelligence 27, jobs 47, shared boundary/consumer/catalog/negative tests 45. TypeScript: 100 cases (78 producer records + 22 semantic negatives), zero skips/cancellations | Python subprocess scopes 2.62s total; TypeScript build/execution scope 1.03s, Node test phase 0.19s |
| `contracts/.venv/bin/python contracts/qualify_artifacts.py --python 3.12 --report contracts/qualification/python312-final.json` | CPython 3.12.12; five allowlisted wheel/sdist builds, installed-artifact suites 242/242, all five synthetic patch producer/consumer combinations, installed schema parity; temporary environments removed | Stages 5.04s total; suite 1.06s |
| `contracts/.venv/bin/python contracts/qualify_artifacts.py --python 3.14 --report contracts/qualification/python314-final.json` | CPython 3.14.8; same restricted proof, 242/242; temporary environments removed | Stages 5.66s total; suite 1.08s |
| `contracts/.venv/bin/python contracts/database-ownership/run.py` | PostgreSQL 18.6 real authenticated connections: 32/32 ownership cases, zero skipped. Unique container `tempo264-e4cd0ffb5e10431f97bfc73e770a7b0b-postgres` removed and absence verified | 1.70s pytest, including provisioning and teardown |
| `contracts/.venv/bin/python contracts/export_schemas.py --check` | All 28 deterministic exports match authoritative Pydantic models | <1s |
| `contracts/.venv/bin/python contracts/check_dependencies.py` | Five packages contain only declared local, Pydantic and standard-library imports; negative direct/lazy/conditional/dynamic loader tests pass | <1s |
| `uvx --from ruff==0.14.0 ruff check contracts` and `ruff format --check contracts` through the same pinned invocation | Static checks pass; 64 Python files formatted | <1s |
| `npm run check:conflicts`, `git diff --check` | Tracked conflict guard and whitespace review pass | <1s |

The pure test environment was installed from contract-local requirements, with a generated hash-locked dependency set subsequently used by both restricted builds. PostgreSQL adds only pytest/psycopg through its dedicated requirements. TypeScript uses its own package.json/package-lock.json with TypeScript 5.9.3 and AJV 8.20.0. Node **26.10.0** was observed. The initial AJV 8.17.1 setup reported a test dependency advisory; updating this local dependency to 8.20.0 produced a zero-vulnerability audit and fresh consumer proof. Root dependencies were not installed.

The PostgreSQL fixture used cached image ID `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`, resolved from `postgres:18.6-trixie`. This proves authorization configuration only, not production adoption or shared-server resource isolation.

## Discovery and corrections

Focused iteration exposed a test-helper import issue under isolated collection and a dependency-check selector accidentally including the TypeScript source root. Both were corrected in this contract-local harness. Independent TypeScript semantic checks initially treated observed-game provenance as execution provenance; the failing imported-game case led to restricting execution chronology checks to execution provenance.

Review identified the two-pending-submission and completed-evidence replay gaps in the new reference illustrations. Named tests now enforce one attempt settlement and prevent completed delivery from regressing to pending. No old production failure was reproduced or claimed repaired. The final pass above supersedes exploratory failures; no failed or zero-selected invocation is counted as a pass.

## Conformance and hosted limits

| Level | Delivered status |
|---|---|
| Contract defined | Five independent distributions; 28 schemas; version-addressable fixtures and ownership catalog |
| Contract examples tested | Python/TypeScript consumers, coherence negatives, transition/replay examples, installed builds/patch compatibility and native authorization fixture |
| Existing producer conformance verified | **No.** Source inspection only; no production contract adapter was added |
| Actual runtime enforcement implemented | **No.** Production domains, roles, transactions and worker protocols remain owning implementation work |

Legacy mapping: `backend/app/study_contracts.py` exposes similar exercise/answer shapes but different endpoint envelopes; `backend/app/models.py::ReviewRequest` contains legacy optional issuance fields and durable-payload rules; `backend/app/review_conflicts.py` uses HTTP exceptions; `backend/app/game_analysis_publication.py` registers commands and writes through application persistence; `app/lib/maia.worker.ts` uses browser message IDs and supersession rather than this job/provenance/cancellation contract. Existing opening probability contracts import chess/application helpers. These are useful foundations, not verified v1 producers.

At the original handoff, Actions was disabled and the PR was draft. No hosted run was claimed. Those observations describe hosted/administrative state, not an engineering defect; the repair status below separates these dimensions. No unrelated full, browser, Redis, model, application startup or live database check ran. Current monorepo checks remain unchanged.

Qualification diagnostics are preserved outside the isolated checkout before cleanup. Task-owned PostgreSQL and temporary restricted environments are removed. The isolated clone can be removed after pushed-source, evidence and activity checks; shared caches and all live study resources remain intact.


## PR #271 repair evidence

Repaired source: **665bb63c27535c4d30c9634e812e755fcdbfd6d0**, clean tree.
Policy compatibility is commit `31c58eab`; advisory/timestamp semantics and
regressions are commit `665bb63c`. Subsequent handoff-only edits do not change
package, schema, dependency, example, test or qualification inputs.

### Findings and reproduction

- Nested advice: shared `assertAdvisoryPublicationSemantics` is invoked for both
  AdvisoryPublication and IntelligenceAvailability, preserving availability and
  workspace checks. It covers every existing advisory invariant and inclusive
  1e-9 normalization tolerance. New paired fixtures are structurally valid before
  semantic rejection; no malformed record enters the positive producer inventory.
- Policy identity: `suggested_order` requires keyword-only expected_policy_id and
  expected_policy_revision. Only exact matches use advice, preserving list order.
  Incompatible policies use the existing sorted/deduplicated capacity-bound fallback.
- Time: all five package-local check_timestamp validators enforce complete lexical,
  calendar, clock and offset bounds; Python aware datetime comparisons remain.
  TypeScript timestampMicroseconds uses Gregorian ordinals and bigint microseconds,
  with no Date/millisecond/float-epoch chronology path or new dependency. Supplied
  strings remain untouched, even for offset-equivalent instants.

Before fixes, selected Python timestamp regressions reproduced **15 failures**
for offset minutes 60/99, with 35 passing controls. Six policy-signature regressions
failed against the old reference, and a separate old-behavior probe demonstrated
wrong policy ID/revision still selecting card-b instead of deterministic card-a.
Selected TypeScript cases reproduced **10 failures / 208 passing controls** for
nested invalid advice and microsecond chronology. The initial repair iteration
also found Python 3.14 accepting hour 24; explicit clock bounds corrected it.
Failed exploratory runs are retained as failure evidence, not passes.

### Final focused acceptance

Observed environment: macOS arm64; Node **26.10.0**, npm **11.19.1**, uv **0.12.22**,
TypeScript **5.9.3**, AJV **8.20.0**, Pydantic **2.12.5**, pytest **9.1.1**,
jsonschema **4.25.1**, build **1.4.0**, hatchling **1.27.0**, Ruff **0.14.0**.
Package builds were reinstalled non-editably after source changes. No root
PYTHONPATH, editable package leakage or root dependency installation was used.

| Command from checkout root | Actual result | Wall time |
| --- | --- | ---: |
| `contracts/.venv/bin/python contracts/export_schemas.py --check` | 28 authoritative schema exports match; schema patterns unchanged, regeneration unnecessary | 0.232 s |
| `contracts/.venv/bin/python contracts/check_dependencies.py` | Five declared package boundaries pass | 0.039 s |
| `contracts/.venv/bin/python contracts/run_tests.py --typescript` | **529 Python + 336 TypeScript pass**, zero skips/cancellations | 4.909 s |
| `contracts/.venv/bin/python contracts/qualify_artifacts.py --python 3.12 --report contracts/qualification/pr271-repair-python312.json` | CPython **3.12.12**, **470 installed tests pass**, five wheel/sdist builds and unchanged metadata-only synthetic patch producer/consumer proof | 5.369 s |
| Same qualification with `--python 3.14 --report contracts/qualification/pr271-repair-python314.json` | CPython **3.14.8**, **470 installed tests pass**, same restricted proof | 5.725 s |
| `uvx --from ruff==0.14.0 ruff check contracts` | Pass | 0.087 s |
| `uvx --from ruff==0.14.0 ruff format --check contracts` | Pass; 70 Python files | 0.024 s |
| `git diff --check` / `git diff --cached --check` | Pass | 0.013 / 0.012 s |

Python regular scopes: Learning 116, Content 99, Game Evidence 64, Intelligence 99,
Analysis Jobs 92, shared contract tests 59. Restricted reports record every
installed identity, actual runtime, stage output/time and artifact hash. Each
executes all **185 package-local timestamp cases** (37 per package), exact policy
checks, advisory controls/tolerance/chronology and both workers' provenance cases.
Both restricted invocations verify temporary resource removal.

TypeScript executes 78 unchanged producer records, 36 semantic negatives and
222 additional timestamp/advisory cases from the registered consumer.test.ts
entry point. Outcomes are independently specified in each language. Every semantic
negative is checked for structural schema acceptance before semantic rejection.
The shared Python negative test's historical name is not counted as TypeScript
execution; the independent Node output proves those cases separately.

### Reused evidence and separate status statements

- **Technical implementation/review status:** all three reported findings are
  fixed and the specified local acceptance matrix passes. No known scoped defect
  or required local-validation gap remains; human review remains pending.
- **Hosted qualification:** no hosted run is claimed. Actions was read back as
  disabled; Actions/protection/workflow settings were untouched. Missing hosted
  execution is not the remaining engineering blocker for this bounded repair.
- **Administrative merge status:** PR #271 was observed draft, with GitHub reporting
  MERGEABLE and no status-check executions. Draft/ready state remains unchanged.
  No broader protection/ruleset conclusion or merge authorization is inferred.
- **Production conformance:** production adapters, service extraction, deployed
  database isolation and runtime enforcement remain unimplemented/uncertified by
  this foundation; they belong to reconstruction implementation owners.

The original **32-case PostgreSQL ownership** log is available at
`test-results/issue-264-contracts-20261014/postgres-final.log`. Its source binding
is 3940831d; hashes of all five fixture/test/requirements inputs were checked
against the original preservation record and match. The old log also records
owned-container cleanup. That unchanged-scope evidence is reused, not rerun or
represented as a fresh repair result. No PostgreSQL, browser, Redis, model, live
database, migration, deployment or full-product suite ran for this repair.

New diagnostics are preserved outside the disposable checkout under
`test-results/pr271-contract-repair-20261014`, including baseline failures, actual
toolchains, acceptance logs/summary, both existing qualification JSON reports and
unchanged PostgreSQL-input verification. Live study resources are untouched.
