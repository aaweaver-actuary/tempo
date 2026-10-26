# Database proof scripts and runner

Existing disposable PostgreSQL scenarios, fixture helpers and runner. Source checks do not start any database or Docker service. The fresh-start source cut retires the adaptive-priority benchmark, its frozen baseline, producer recovery proof and refresh-input proof together with that producer; `priority-benchmark` mode is rejected before setup. Other scenario resource and cleanup authority remains unchanged.

Read the owning [test-selection policy](../../docs/testing.md) before running a scenario. `node database/tests/test-postgres-docker.mjs --list --mode browser` validates discovery without infrastructure; choose only the relevant owning checks. The root Make/package commands reference this location. `check_postgres_*.py` retains each existing scenario's public name and acceptance behavior. Retained graph/freshness/background native proofs use explicit saved-score fixtures where needed; their edits are not a native qualification receipt.

Application/contract tests that exercise storage alongside feature behavior remain in their regular backend/service suites. The [mixed-source inventory](../INVENTORY.md) describes the mechanical task's limits; [schema charts](../README.md) describe actual stored data. This PR does not introduce a new qualification framework or run a full-system suite.


[Disposable writer benchmark](benchmarks/README.md) retains its original entry point and measurement behavior. Its one-table fixture is documented separately from production schemas; import/discovery checks do not execute the benchmark.

## Additional fixed proof operations

- [Redis native proofs](redis/README.md): dedicated background wake, socket deadline and persisted readiness scripts now live entirely here; no old-path shims.
- [CLI lifecycle fixtures](cli/README.md): existing fixed PostgreSQL setup/read operations; CLI scenario/lifecycle decisions remain in the caller.
- [Learning browser fixtures](browser/README.md): existing serialized IndexedDB setup/inspection/fault callback expressions; qualifier scenario policy remains outside.

These are source/discovery moves. No actual Redis, PostgreSQL, browser or resource qualification was run. Application-facing SQLite/fake-storage assertions retain their regular feature suite owners; the supplemental test-factory inventory distinguishes fixture organization from runtime storage implementation.
