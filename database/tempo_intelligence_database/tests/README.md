# Intelligence database tests

**Current source placement; native execution unverified for this move.**
The existing native suite now lives in [`integration/`](integration/), physically
inside the single root database owner tree. These files use Psycopg/SQL or the
same invocation-owned database fixture. They are shipped in the service source
distribution, not the runtime wheel. Component pure/fake/contract tests remain in
[`services/intelligence/tests`](../../../services/intelligence/tests).

| Files | Existing responsibility |
| --- | --- |
| [conftest.py](integration/conftest.py), [support.py](integration/support.py) | Invocation-owned state, local-only socket fixture, synthetic request/credential helpers and owner SQL. |
| [test_postgres.py](integration/test_postgres.py) | Runtime/owner/migration/receipt/cache/Maia persistence and installed CLI/API contracts. |
| [test_analysis_postgres.py](integration/test_analysis_postgres.py) | Durable Analysis staging, leases, results, publication, dismissal, report/history and browser export fixture. |
| [test_games_readonly_postgres.py](integration/test_games_readonly_postgres.py) | Installed Games-reader grants, transaction settings and no-mutation proof. |
| [test_provider_postgres.py](integration/test_provider_postgres.py), [test_server_postgres.py](integration/test_server_postgres.py) | Native lease/provider/session/server lifecycle races against the same disposable database. |
| [test_actual_analysis.py](integration/test_actual_analysis.py), [test_actual_a.py](integration/test_actual_a.py) | Explicit optional installed B and A/B/C durable qualification. These combine cross-component acceptance with SQL state assertions; kept intact in the native harness rather than split into a new framework. |

The [runner](../tooling/README.md) retains its default, Analysis, Games-reader and
explicit actual A/B selection exactly. Relative fixture reads resolve the service
fixture directory in repository, extracted source-distribution and staged clean
consumer layouts. Test-to-test imports and the standalone `integration` test
package remain unchanged. No test body, Docker, server, engine, provider, role,
credential operation or database was executed during this relocation. Collection
only is a discovery check, not native qualification. Existing bounded runner,
synthetic-input and resource ownership/cleanup guards remain authoritative.
