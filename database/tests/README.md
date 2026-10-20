# Database proof scripts and runner

Existing disposable PostgreSQL scenarios, benchmarks, fixture helpers and runner, relocated from `scripts/`. Their resource limits, scenario selection, cleanup authority and assertions are unchanged. Source checks do not start any database or Docker service.

Read the owning [test-selection policy](../../docs/testing.md) before running a scenario. `node database/tests/test-postgres-docker.mjs --list --mode browser` validates discovery without infrastructure; choose only the relevant owning checks. The root Make/package commands reference this location. `check_postgres_*.py` retains each existing scenario's public name and acceptance behavior. `priority_baseline_postgres.py` remains the benchmark's frozen legacy baseline, not a new runtime owner.

Application/contract tests that exercise storage alongside feature behavior remain in their regular backend/service suites. The [mixed-source inventory](../INVENTORY.md) describes the mechanical task's limits; [schema charts](../README.md) describe actual stored data. This PR does not introduce a new qualification framework or run a full-system suite.
