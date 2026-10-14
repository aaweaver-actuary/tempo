# M02-B delivery evidence — October 14, 2026

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

GitHub readback reports **Actions disabled**. No hosted exact-candidate plan/check execution is claimed. The PR remains draft and **not merge-qualified**; enabling Actions or changing checks/protection is outside this task. No unrelated full, browser, Redis, model, application startup or live database check was run. Current monorepo checks remain unchanged; these focused proofs do not represent their complete gate.

Qualification diagnostics are preserved outside the isolated checkout before cleanup. Task-owned PostgreSQL and temporary restricted environments are removed. The isolated clone can be removed after pushed-source, evidence and activity checks; shared caches and all live study resources remain intact.
